"""`sasha listen`: build the real listener and run it in the foreground.

It refuses to start (plain message, exit code 2) when anything the fail-closed design
depends on is missing: the indicator module or its tray host, the VAD model, D-Bus
(`jeepney`), or a capture program. It never starts a brain.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from collections.abc import Callable
from dataclasses import dataclass

from sasha.config import Settings
from sasha.listener.brain import http_health, listener_url
from sasha.listener.capture import CaptureProcess, capture_command
from sasha.listener.contracts import ListenerSettings
from sasha.listener.gate import END_SILENCE_MS, Gate
from sasha.listener.guards import Guards
from sasha.listener.listener import Listener
from sasha.listener.playback import Player
from sasha.listener.window_open import WindowOpener
from sasha.server.security import is_loopback

log = logging.getLogger(__name__)


class ListenerUnavailableError(RuntimeError):
    pass


@dataclass
class ListenOptions:
    debug_dump_audio: bool = False
    pause_on_battery: bool = True
    end_silence_ms: int = END_SILENCE_MS


def _require(what: str, error: BaseException) -> ListenerUnavailableError:
    return ListenerUnavailableError(f"{what} ({type(error).__name__}: {error})")


def build_listener(settings: Settings, options: ListenOptions) -> Listener:
    server = settings.profile.server
    if not is_loopback(server.host):
        # Every gated room segment goes to this address: never off this machine.
        raise ListenerUnavailableError(
            f"The brain address {server.host!r} is not a loopback address; the listener "
            "only talks to a brain on this machine"
        )
    try:
        from sasha.listener.indicator import make_indicator
    except ImportError as e:
        raise _require("The tray indicator module is missing", e) from e
    try:
        from sasha.listener.vad import SileroVad

        vad = SileroVad()
    except Exception as e:
        raise _require(
            "No voice activity detector; the listener will not run without one", e
        ) from e
    try:
        import jeepney  # noqa: F401  (checked here so the error is plain)

        from sasha.listener.dbus_backend import DBusBackend
    except ImportError as e:
        raise _require("D-Bus support missing: uv sync --extra listener", e) from e
    command = capture_command()
    if command is None:
        raise ListenerUnavailableError("No capture program: neither arecord nor pw-record")

    import websockets

    debug_sink = None
    if options.debug_dump_audio:
        from sasha.listener.debug_dump import DebugDump

        debug_sink = DebugDump(settings.paths.data_dir)
    listener = Listener(
        settings=ListenerSettings(
            ws_url=listener_url(server.host, server.port),
            pause_on_battery=options.pause_on_battery,
            end_silence_ms=options.end_silence_ms,
        ),
        indicator=make_indicator(),
        guards_factory=lambda on_change: Guards(
            DBusBackend(), on_change, pause_on_battery=options.pause_on_battery
        ),
        gate=Gate(vad, end_silence_ms=options.end_silence_ms),
        capture_factory=lambda: CaptureProcess(command),
        player=Player(),
        health=http_health(f"http://{server.host}:{server.port}"),
        connect=lambda url: websockets.connect(url, max_size=2**22, open_timeout=5),
        debug_sink=debug_sink,
    )
    listener.window = window_opener(settings, lambda: listener.guards.allowed()[0])
    return listener


def window_opener(settings: Settings, allowed: Callable[[], bool]) -> WindowOpener:
    """The wake phrase brings up the app shell (as its own user unit), if installed."""
    from sasha.app import launcher
    from sasha.app.single import NoRuntimeDir, ask_window, socket_path

    def open_window() -> str:
        if not launcher.available():
            return "unavailable"
        from sasha.server.launch import dead_proxy_port

        port = settings.profile.server.port
        return launcher.open_shell(settings, port, dead_proxy_port(), wake=True, as_unit=True)

    def is_open() -> bool:
        try:
            return ask_window(socket_path(), "ping")
        except NoRuntimeDir:
            return False

    return WindowOpener(settings.profile.window, allowed, open_window, is_open)


def run_listen(settings: Settings, options: ListenOptions) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        listener = build_listener(settings, options)
    except ListenerUnavailableError as e:
        print(f"[refused] {e}. The microphone was not opened.", file=sys.stderr)
        return 2

    async def main() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, listener.stop)
        await listener.run()

    asyncio.run(main())
    if listener.final_error:
        print(f"[stopped] {listener.final_error}", file=sys.stderr)
        return 1
    return 0
