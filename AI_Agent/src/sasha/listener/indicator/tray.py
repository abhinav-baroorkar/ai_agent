"""Runs the tray item on its own thread and gives the listener the agreed interface.

Fail closed: `available()` is true only after the bus itself has confirmed that a tray
host is registered and lists our item. Any doubt (no bus, no tray, a timeout, the
thread not answering) is "not available", and losing the tray calls `on_lost` at once.
If there is no tray the indicator keeps waiting for one; it never gives up or exits.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from collections.abc import Callable

from sasha.listener.indicator.base import STATES, Earcon, EarconKind, Persona, State
from sasha.listener.indicator.bus import Bus, BusError
from sasha.listener.indicator.earcons import earcon
from sasha.listener.indicator.sni import SniItem

log = logging.getLogger(__name__)
POLL_S = 0.02  # how long the thread waits for a bus message before looking at its inbox
RETRY_S = 2.0  # how often it looks for a bus or a tray while there is none
ANSWER_S = 1.5  # how long available() waits for the thread
RECHECK_S = 2.0  # how often the thread itself asks the bus whether we are still shown


class _Check:
    """A question from another thread: is the icon really shown right now?"""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.shown = False


class TrayIndicator:
    def __init__(self, open_bus: Callable[[], Bus], who: Persona) -> None:
        self._open_bus = open_bus
        self._who = who
        self._inbox: queue.Queue[State | _Check] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._state: State = "offline"
        self._shown = False  # the last answer from the bus; only the thread writes it
        self._on_lost: Callable[[], None] = lambda: None
        self._on_toggle: Callable[[], None] = lambda: None

    # --- the interface the listener uses ---------------------------------------------
    def start(self, on_pause_toggle: Callable[[], None], on_lost: Callable[[], None]) -> None:
        if self._thread is not None:
            return
        self._on_toggle, self._on_lost = on_pause_toggle, on_lost
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="tray-indicator", daemon=True)
        self._thread.start()

    def available(self) -> bool:
        if self._thread is None or not self._thread.is_alive():
            return False
        check = _Check()
        self._inbox.put(check)
        return check.done.wait(ANSWER_S) and check.shown

    def set_state(self, state: State) -> None:
        if state not in STATES:
            raise ValueError(f"Unknown indicator state: {state!r}")
        self._inbox.put(state)

    def stop(self) -> None:
        thread, self._thread = self._thread, None
        self._stop.set()
        if thread is not None:
            thread.join(timeout=2.0)

    def earcon(self, kind: EarconKind) -> Earcon:
        return earcon(kind)

    # --- the thread ----------------------------------------------------------------
    def _run(self) -> None:
        item: SniItem | None = None
        retry_at = 0.0  # when to next look for a bus or a tray
        check_at = 0.0  # when to next ask the bus if we are still shown
        while not self._stop.is_set():
            now = time.monotonic()
            if item is None:
                self._drain(None)
                if now >= retry_at:
                    item = self._connect()
                    retry_at = now + RETRY_S
                    check_at = now + RECHECK_S
                if item is None:
                    self._stop.wait(POLL_S)
                    continue
            try:
                if not item.registered and now >= retry_at:
                    retry_at = now + RETRY_S
                    with contextlib.suppress(BusError):
                        item.register()  # the tray was gone; look for it again
                    self._note(item.check_shown())
                elif item.registered and now >= check_at:
                    # ask again even with no signal: a tray can drop an item silently
                    check_at = now + RECHECK_S
                    self._note(item.check_shown())
                self._drain(item)
                msg = item.bus.poll(POLL_S)
                if msg is not None and not item.handle(msg):
                    self._note(False)
                elif msg is not None and msg.kind == "signal":
                    self._note(item.check_shown())
            except Exception as exc:
                # Fail closed. A lost connection, or anything unexpected at all, means we
                # can no longer vouch for the sign: say it is lost, drop the connection
                # and start again after the retry delay. Logged once per reconnect.
                if isinstance(exc, BusError):
                    log.warning("Tray indicator: %s", exc)
                else:
                    log.exception("Tray indicator: unexpected error; treating the sign as lost")
                self._note(False)
                item.bus.close()
                item = None
                retry_at = time.monotonic() + RETRY_S
        if item is not None:
            item.bus.close()
        self._shown = False

    def _connect(self) -> SniItem | None:
        try:
            bus = self._open_bus()
        except BusError:
            return None
        item = SniItem(bus, self._who, self._clicked)
        item.state = self._state
        try:
            item.setup()
        except BusError:  # a bus but no tray: keep the connection and wait for one
            item.registered = False
        self._note(item.check_shown())
        return item

    def _clicked(self) -> None:
        try:
            self._on_toggle()
        except Exception:
            log.exception("Tray indicator: pause callback failed")

    def _note(self, shown: bool) -> None:
        """Record what the bus says; going from shown to not shown is reported at once."""
        was, self._shown = self._shown, shown
        if was and not shown:
            try:
                self._on_lost()
            except Exception:
                log.exception("Tray indicator: lost callback failed")

    def _drain(self, item: SniItem | None) -> None:
        while True:
            try:
                job = self._inbox.get_nowait()
            except queue.Empty:
                return
            if isinstance(job, _Check):
                job.shown = item.check_shown() if item is not None else False
                self._note(job.shown)
                job.done.set()
            else:
                self._state = job
                if item is not None:
                    item.set_state(job)
