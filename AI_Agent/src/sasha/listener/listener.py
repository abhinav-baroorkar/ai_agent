"""The listener's main loop: one microphone, one brain socket, one visible indicator.

The microphone (the capture child) runs only while ALL of these hold, re-checked on
every change and every 2 s:
- the indicator is shown (`available()`, polled in a worker thread) and was not lost;
- the guards say yes (unlocked, session active, sleep inhibitor held, not on battery);
- the owner has not paused it;
- the brain is online (after its first `state` frame), healthy and accepts wake audio;
- the brain is not answering a wake turn, except for the one-segment confirm window;
- no reply is playing (half-duplex: from `tts_start` until 300 ms after playback ends)
  and the brain has not sent `pause_capture`.
Anything else kills the child: the microphone is closed, not muted.

Visible-sign invariant: the capture child is alive only while the indicator shows a
state in `MIC_OPEN_STATES`. A mic-open state is shown before the child starts; a closed
state is shown only after the child was killed, in the same synchronous step.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Callable
from typing import Any

from sasha.listener.brain import CLOSE_SLOT_TAKEN, HealthCheck, frame, segment_frames, writer
from sasha.listener.capture import Backoff, CaptureProcess
from sasha.listener.confirm import ConfirmWindow, StopWindow
from sasha.listener.contracts import Connect, Indicator, ListenerSettings, SegmentSink, State
from sasha.listener.gate import ANSWER_MIN_SPEECH_MS, MIN_SPEECH_MS, Gate, Segment
from sasha.listener.guards import Guards
from sasha.listener.messages import handle_message
from sasha.listener.playback import Player

log = logging.getLogger(__name__)

STATS_EVERY_S = 60.0


class Listener:
    def __init__(
        self,
        settings: ListenerSettings,
        indicator: Indicator,
        guards_factory: Callable[[Callable[[], None]], Guards],
        gate: Gate,
        capture_factory: Callable[[], CaptureProcess | None],
        player: Player,
        health: HealthCheck,
        connect: Connect,
        debug_sink: SegmentSink | None = None,
    ) -> None:
        self.settings = settings
        self.indicator = indicator
        self.guards = guards_factory(self._changed)
        self.gate = gate
        self.capture_factory = capture_factory
        self.player = player
        self.health = health
        self.connect = connect
        self.debug_sink = debug_sink
        self.confirm = ConfirmWindow(self._changed)
        self.stop_window = StopWindow(self._changed)
        self.prime_gate = False  # after the "armed" earcon: the next command may start at once
        self.paused = False
        self.online = False
        self.server_paused = False
        self.playing = False  # half-duplex: a reply is playing (or just ended)
        self.busy = False  # the brain is answering a wake turn
        self.server_state = "idle"
        self.final_error: str | None = None
        self._lost = threading.Event()  # set from the indicator's thread
        self._shown = False  # last answer of indicator.available(); False until proven
        self._ws: Any = None
        self._out: asyncio.Queue[str | bytes] | None = None  # one writer keeps frame order
        self._capture: CaptureProcess | None = None
        self._capture_task: asyncio.Task[None] | None = None
        self._tearing_down = False  # a cancelled capture task is still cleaning up
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = asyncio.Event()
        self._sent_drops = 0

    # --- the one decision ------------------------------------------------------------
    def mic_allowed(self) -> tuple[bool, str]:
        checks = [
            (self._shown and not self._lost.is_set(), "indicator not shown"),
            (not self.paused, "paused"),
            (self.online, "brain offline"),
            (not self.busy or self.answer_window_open, "brain is answering"),
            (not self.server_paused, "brain is speaking"),
            (not self.playing, "playing a reply"),
        ]
        for ok, reason in checks:
            if not ok:
                return False, reason
        return self.guards.allowed()

    @property
    def answer_window_open(self) -> bool:
        """A one-segment window the brain opened: a spoken yes/no, or "stop" for a ring."""
        return self.confirm.is_open or self.stop_window.is_open

    def _changed(self) -> None:
        """Re-decide now. Closing (and showing it) is synchronous; opening is scheduled,
        with the mic-open sign shown in this same step, before the child exists."""
        # A spoken yes/no or "stop" is often shorter than the usual minimum.
        short = self.answer_window_open
        self.gate.min_speech_ms = ANSWER_MIN_SPEECH_MS if short else MIN_SPEECH_MS
        allowed, _ = self.mic_allowed()
        if not allowed:
            self._close_mic()
        elif self._loop is not None and (self._capture_task is None or self._capture_task.done()):
            self._capture_task = self._loop.create_task(self._capture_loop())
            self._show(mic_open=True)
            return
        elif self._tearing_down:
            # The previous capture is still being torn down (its child already killed);
            # it re-decides when done (see _close_mic), so show the sign now.
            self._show(mic_open=True)
            return
        self._show()

    def _close_mic(self) -> None:
        if self._capture is not None:
            self._capture.kill()
        task = self._capture_task
        if task is not None and not task.done() and not self._tearing_down:
            task.cancel()
            self._tearing_down = True
            task.add_done_callback(self._torn_down)
        self.gate.reset()

    def _torn_down(self, task: asyncio.Task[None]) -> None:
        """A cancelled capture finished its cleanup: decide again, so a reopen asked for
        meanwhile (a confirm or stop window) isn't lost until the next 2 s check."""
        self._tearing_down = False
        if self._loop is not None and not self._stop.is_set():
            self._loop.call_soon(self._changed)

    def _show(self, mic_open: bool | None = None) -> None:
        if mic_open is None:
            # A capture still being spawned counts as open: a `_changed()` during the
            # spawn must not put a closed state on the tray just before the child runs.
            mic_open = self._capture is not None and self._capture.live_or_starting
        state: State
        if mic_open:
            if self.debug_sink is not None:
                state = "debug"
            elif self.answer_window_open or self.server_state == "listening":
                state = "listening"
            else:
                state = "waiting"
        elif self.paused:  # first: the indicator reads the pause direction from this
            state = "paused"
        elif not self.online:
            state = "offline"
        elif self.playing or self.server_state == "speaking":
            state = "speaking"
        elif self.busy or self.server_state == "thinking":
            state = "thinking"
        else:
            state = "paused"
        self.indicator.set_state(state)

    # --- callbacks from the indicator (its own thread) -------------------------------
    def on_lost(self) -> None:
        self._lost.set()
        if self._capture is not None:
            self._capture.kill()  # within this call, not one loop turn later
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._changed)

    def on_pause_toggle(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._toggle_pause)

    def _toggle_pause(self) -> None:
        """Kill (one syscall) and show the new state in one synchronous step, before
        control returns to the loop; the indicator reads the direction from it."""
        self.paused = not self.paused
        self._changed()
        self._queue_frame(frame("listener_state", paused=self.paused))

    # --- capture -----------------------------------------------------------------------
    async def _capture_loop(self) -> None:
        backoff = Backoff()
        while self.mic_allowed()[0]:
            proc = self.capture_factory()
            if proc is None:
                log.error("No capture program (arecord or pw-record): microphone stays closed")
                return
            self._show(mic_open=True)  # the sign goes up before the microphone opens
            self._capture = proc
            try:
                await proc.start()
                if not self.mic_allowed()[0]:  # changed while starting
                    break
                self._show()  # the child runs now: make sure a mic-open state is shown
                if self.prime_gate:  # the mic was closed only for our own "armed" tone
                    self.prime_gate = False
                    self.gate.prime()
                async for chunk in proc.chunks():
                    backoff.reset()
                    if self._lost.is_set() or not self.mic_allowed()[0]:
                        break
                    if (segment := self.gate.feed(chunk)) is not None:
                        self._on_segment(segment)
            except OSError as e:
                log.warning("Capture failed to start (%s)", type(e).__name__)
            finally:
                proc.kill()
                self._capture = None
                self.gate.reset()
                self._show()
                await proc.stop()
            if not self.mic_allowed()[0]:
                return
            await asyncio.sleep(backoff.next_delay())  # the child ended on its own

    def _on_segment(self, segment: Segment) -> None:
        if self._ws is None:
            return
        if self.answer_window_open:
            self._forward_segment(segment)  # read by the brain only as yes/no or stop
            self.confirm.close()
            self.stop_window.close()
            return
        if self.busy:
            return  # nothing is sent while the brain answers
        self._forward_segment(segment)

    def _forward_segment(self, segment: Segment) -> None:
        if self.debug_sink is not None:
            self.debug_sink(segment)
        for part in segment_frames(segment.pcm):
            self._queue_frame(part)

    def _queue_frame(self, message: str | bytes) -> None:
        if self._out is not None:
            self._out.put_nowait(message)

    # --- brain socket ------------------------------------------------------------------
    async def _brain_loop(self) -> None:
        delays = self.settings.reconnect_s
        attempt = 0
        while not self._stop.is_set() and self.final_error is None:
            health = await self.health()
            if health.ok:
                code: int | None = None
                try:
                    async with self.connect(self.settings.ws_url) as ws:
                        attempt = 0
                        await self._session(ws)
                        code = getattr(ws, "close_code", None)
                except Exception as e:
                    self._offline(f"socket closed ({type(e).__name__})")
                    code = _close_code(e)
                if code == CLOSE_SLOT_TAKEN:
                    self.final_error = "another listener already holds the brain's slot"
                    log.error("Listener refused: %s. Not retrying.", self.final_error)
                    return
            else:
                self._offline(health.reason)
            delay = delays[min(attempt, len(delays) - 1)]
            attempt += 1
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), delay)

    async def _session(self, ws: Any) -> None:
        # Online (mic may open) only after the brain's first `state` frame: a refused
        # socket (e.g. close 1013, slot taken) gets an `error` and a close instead.
        self._ws, self.online = ws, False
        self.server_paused = self.busy = self.playing = False
        await self.player.stop()
        self._out = asyncio.Queue()
        writer_task = asyncio.create_task(writer(ws, self._out))
        self._queue_frame(frame("listener_state", paused=self.paused))
        self._changed()
        try:
            async for message in ws:
                await self._on_message(message)
        finally:
            writer_task.cancel()
            self._out = None
            self._offline("socket closed")

    def _offline(self, why: str) -> None:
        if self.online:
            log.warning("Brain offline (%s): microphone closed", why)
        self.server_state = "idle"
        self._ws, self.online, self.busy, self.playing = None, False, False, False
        self.confirm.close()
        if self._loop is not None and self.player.playing:
            self._loop.create_task(self.player.stop())
        self._changed()

    async def _on_message(self, message: str | bytes) -> None:
        await handle_message(self, message)  # the brain's frames: see messages.py

    # --- background checks -----------------------------------------------------------
    async def _stats_loop(self) -> None:
        while True:
            await asyncio.sleep(STATS_EVERY_S)
            drops = self.gate.stats.drops
            if drops != self._sent_drops:
                self._queue_frame(frame("listener_stats", gate_drops=drops - self._sent_drops))
                self._sent_drops = drops

    async def _check_indicator(self) -> None:
        try:
            shown = await asyncio.wait_for(asyncio.to_thread(self.indicator.available), 3.0)
        except Exception:
            shown = False  # unknown means not shown
        self._shown = bool(shown)
        if shown:
            self._lost.clear()  # shown again after a loss (e.g. gnome-shell restarted)

    async def _check_loop(self) -> None:
        while True:
            await self._check_indicator()
            self._changed()  # also catches an indicator that vanished without a signal
            await asyncio.sleep(self.settings.check_s)

    # --- run ---------------------------------------------------------------------------
    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.indicator.start(self.on_pause_toggle, self.on_lost)
        self._show()
        await self._check_indicator()
        await self.guards.start()
        tasks = [
            asyncio.create_task(self._brain_loop()),
            asyncio.create_task(self._check_loop()),
            asyncio.create_task(self._stats_loop()),
        ]
        try:
            await self._stop.wait()
        finally:
            self._close_mic()
            for task in tasks:
                task.cancel()
            await self.confirm.aclose()
            await self.player.stop()
            await self.guards.stop()
            self.indicator.stop()

    def stop(self) -> None:
        self._close_mic()
        self._stop.set()


def _close_code(exc: BaseException) -> int | None:
    """The close code a websockets ConnectionClosed carries (`rcvd.code`), if any."""
    rcvd = getattr(exc, "rcvd", None)
    code = getattr(rcvd, "code", None)
    return code if isinstance(code, int) else None
