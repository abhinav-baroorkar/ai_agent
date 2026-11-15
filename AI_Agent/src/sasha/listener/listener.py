"""The listener's main loop: one microphone, one brain socket, one visible indicator.

The microphone (the capture child) runs only while ALL of these hold, re-checked on
every change and every 2 s, and the indicator flag on every 20 ms chunk:
- the indicator is shown (`available()`) and was not lost;
- the guards say yes (unlocked, session active, not suspending, not on battery);
- the owner has not paused it;
- the brain is online, healthy and accepts wake audio;
- no reply is playing (half-duplex: from `tts_start` until 300 ms after playback ends)
  and the brain has not sent `pause_capture`.
Anything else kills the child: the microphone is closed, not muted.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from sasha.listener.brain import CLOSE_SLOT_TAKEN, HealthCheck, frame, segment_frames, writer
from sasha.listener.capture import Backoff, CaptureProcess
from sasha.listener.gate import END_SILENCE_MS, Gate, Segment
from sasha.listener.guards import Guards
from sasha.listener.playback import Player

log = logging.getLogger(__name__)

RECONNECT_S = (1.0, 2.0, 5.0, 10.0, 30.0)
CHECK_S = 2.0
AFTER_SPEECH_S = 0.3
STATS_EVERY_S = 60.0


class Clip(Protocol):
    """An earcon: 16-bit little-endian mono PCM."""

    pcm: bytes
    sample_rate: int


class Indicator(Protocol):
    """Built by the design session (`sasha.listener.indicator`). Its callbacks run on its
    own thread; `available()` makes bus calls (up to ~1.5 s), so it is only called from
    the 2 s check, in a worker thread, never per audio chunk."""

    def start(self, on_pause_toggle: Callable[[], None], on_lost: Callable[[], None]) -> None: ...

    def available(self) -> bool: ...

    def set_state(self, state: str) -> None: ...

    def earcon(self, kind: str) -> Clip: ...

    def stop(self) -> None: ...


@dataclass
class ListenerSettings:
    ws_url: str
    pause_on_battery: bool = True
    end_silence_ms: int = END_SILENCE_MS
    check_s: float = CHECK_S
    reconnect_s: tuple[float, ...] = RECONNECT_S


Connect = Callable[[str], Any]  # url -> async context manager yielding a websocket
SegmentSink = Callable[[Segment], None]  # the debug dump (CLI flag only)


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
        self.paused = False
        self.online = False
        self.server_paused = False
        self.playing = False  # half-duplex: a reply is playing (or just ended)
        self.busy = False  # a wake turn is running on the brain
        self.server_state = "idle"
        self.final_error: str | None = None
        self._lost = threading.Event()  # set from the indicator's thread
        self._shown = False  # last answer of indicator.available(); False until proven
        self._ws: Any = None
        self._out: asyncio.Queue[str | bytes] | None = None  # one writer keeps frame order
        self._capture: CaptureProcess | None = None
        self._capture_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = asyncio.Event()
        self._sent_drops = 0

    # --- the one decision ------------------------------------------------------------
    def mic_allowed(self) -> tuple[bool, str]:
        checks = [
            (self._shown and not self._lost.is_set(), "indicator not shown"),
            (not self.paused, "paused"),
            (self.online, "brain offline"),
            (not self.server_paused, "brain is speaking"),
            (not self.playing, "playing a reply"),
        ]
        for ok, reason in checks:
            if not ok:
                return False, reason
        return self.guards.allowed()

    def _changed(self) -> None:
        """Re-decide now. Closing happens synchronously; opening is scheduled."""
        allowed, _ = self.mic_allowed()
        if not allowed:
            self._close_mic()
        elif self._loop is not None and (self._capture_task is None or self._capture_task.done()):
            self._capture_task = self._loop.create_task(self._capture_loop())
        self._show()

    def _close_mic(self) -> None:
        if self._capture is not None:
            self._capture.kill()
        if self._capture_task is not None and not self._capture_task.done():
            self._capture_task.cancel()
        self.gate.reset()

    def _show(self) -> None:
        mic_open = self._capture is not None and self._capture.running
        if not self.online:
            state = "offline"
        elif self.playing or self.server_state == "speaking":
            state = "speaking"
        elif self.busy or self.server_state == "thinking":
            state = "thinking"
        elif not mic_open:
            state = "paused"
        elif self.debug_sink is not None:
            state = "debug"
        else:
            state = "listening" if self.server_state == "listening" else "waiting"
        self.indicator.set_state(state)

    # --- callbacks from the indicator (any thread) ------------------------------------
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
            self._capture = proc
            try:
                await proc.start()
                self._show()
                async for chunk in proc.chunks():
                    backoff.reset()
                    if self._lost.is_set() or not self.mic_allowed()[0]:
                        break
                    if (segment := self.gate.feed(chunk)) is not None:
                        self._on_segment(segment)
            except OSError as e:
                log.warning("Capture failed to start (%s)", type(e).__name__)
            finally:
                await proc.stop()
                self._capture = None
                self.gate.reset()
                self._show()
            if not self.mic_allowed()[0]:
                return
            await asyncio.sleep(backoff.next_delay())  # the child ended on its own

    def _on_segment(self, segment: Segment) -> None:
        if self.busy or self._ws is None:
            return  # nothing is sent while the brain runs a turn
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
        self.server_paused = self.busy = False
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
        self._ws, self.online, self.busy = None, False, False
        self._changed()

    async def _on_message(self, message: str | bytes) -> None:
        if isinstance(message, bytes):
            await self.player.write(message)
            return
        import json

        try:
            msg = json.loads(message)
        except ValueError:
            return
        kind = msg.get("type")
        if kind == "tts_start":
            self.playing = True
            self._changed()  # closes the mic before the first sound
            await self.player.start(int(msg.get("sample_rate") or 24000))
        elif kind == "tts_end":
            await self.player.finish()
            await asyncio.sleep(AFTER_SPEECH_S)
            self.playing = False
            self._changed()
        elif kind == "wake":
            await self._on_wake(msg)
        elif kind == "pause_capture":
            self.server_paused = True
            self._changed()
        elif kind == "resume_capture":
            self.server_paused = False
            self._changed()
        elif kind == "state":
            if not self.online and self._ws is not None:
                self.online = True
                log.info("Connected to the brain")
            self.server_state = str(msg.get("state"))
            if self.server_state == "idle":
                self.busy = False
            self._changed()
        elif kind == "error":
            log.warning("Brain error: %s", str(msg.get("message", ""))[:200])

    async def _on_wake(self, msg: dict[str, Any]) -> None:
        if not msg.get("heard"):
            return  # a miss: no sound, nothing kept
        if msg.get("command"):
            self.busy = True
            self._changed()
            await self._earcon("wake")
        else:
            await self._earcon("armed")

    async def _earcon(self, kind: str) -> None:
        try:
            clip = self.indicator.earcon(kind)
        except Exception as e:
            log.warning("Earcon %s unavailable (%s)", kind, type(e).__name__)
            return
        await Player(self.player.command).play(clip.pcm, clip.sample_rate)

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
