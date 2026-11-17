"""After a brain restart, the open app window's page must really use the new token
(found live, 2026-11-16: the shell loaded `/#t=<new>` again, but the page kept the old
token and every /ws/browser was refused with 403 until the window was reopened).

The real shell (`python -m sasha.app.shell`, offscreen, no window on any screen) opens
a probe page on a local server. The page imports the real `js/api.js` and calls the
server every 300 ms, so each call carries the token api.js holds. The token file is
then rewritten, as a new brain does, and the server must see the new token."""

from __future__ import annotations

import http.server
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from sasha.app import launcher
from sasha.server.window_token import WindowToken, write_token

WEB = Path(__file__).resolve().parents[1] / "src" / "sasha" / "avatar" / "web"
PROBE = b"""<!doctype html><meta charset=utf-8><title>token probe</title>
<script type=module>
import { api, openBrowserSocket } from '/js/api.js';
setInterval(() => {
  api('GET', '/report').catch(() => {});
  const ws = openBrowserSocket();  // the panel's own socket, as browser-link.js opens it
  ws.onerror = () => ws.close();
}, 300);
</script>"""


def _server(seen: list[str], sockets: list[str], port: int = 0) -> http.server.ThreadingHTTPServer:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.split("?")[0]
            if path == "/ws/browser":  # the panel socket's token is in its subprotocols
                offered = self.headers.get("Sec-WebSocket-Protocol") or ""
                tokens = [
                    p.strip()[6:] for p in offered.split(",") if p.strip().startswith("token.")
                ]
                sockets.extend(tokens or [""])
                self.send_response(403)  # refused, as the live brain did; only the offer counts
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path == "/report":
                seen.append(self.headers.get("X-Window-Token") or "")
                body, kind = b"{}", "application/json"
            elif path.startswith("/js/") and (WEB / path[1:]).is_file():
                body, kind = (WEB / path[1:]).read_bytes(), "text/javascript"
            elif path == "/":
                body, kind = PROBE, "text/html"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    return http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)


def _until(check: object, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():  # type: ignore[operator]
            return True
        time.sleep(0.1)
    return False


@pytest.mark.skipif(not launcher.available(), reason="the `app` extra (PySide6) is not installed")
def test_the_page_uses_the_new_token_after_a_brain_restart(tmp_path: Path) -> None:
    seen: list[str] = []  # the X-Window-Token of the page's API calls
    sockets: list[str] = []  # the token the panel socket offered
    server = _server(seen, sockets)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    state, run = tmp_path / "state", tmp_path / "run"
    run.mkdir(mode=0o700)
    old, new = WindowToken(), WindowToken()
    write_token(state, old)
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",  # no window on any screen
        "XDG_RUNTIME_DIR": str(run),
        "QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu",
    }
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    cmd = [sys.executable, "-m", "sasha.app.shell", "--port", str(port), "--dead-proxy", "9",
           "--profile", str(tmp_path / "profile"), "--state-dir", str(state)]  # fmt: skip
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        first = _until(lambda: old.value in seen and old.value in sockets, 30)
        assert first, "the page never used the first token"
        write_token(state, new)  # the brain restarted and wrote its own
        assert _until(lambda: new.value in sockets, 15), "the panel socket kept the old token"
        assert new.value in seen  # the API calls too
        for got in (seen, sockets):  # and the old one is no longer used
            assert old.value not in got[got.index(new.value) + 2 :]
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        server.shutdown()


@pytest.mark.skipif(not launcher.available(), reason="the `app` extra (PySide6) is not installed")
def test_a_token_written_before_the_brain_listens_still_loads(tmp_path: Path) -> None:
    """Review S1 (window-token-reload): the brain writes its token before it binds the
    port. A watch tick in that gap loaded the new token against a closed port, got an
    error page and never loaded again. Now the failed load is tried again."""
    seen: list[str] = []
    sockets: list[str] = []
    server = _server(seen, sockets)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    state, run = tmp_path / "state", tmp_path / "run"
    run.mkdir(mode=0o700)
    old, new = WindowToken(), WindowToken()
    write_token(state, old)
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "XDG_RUNTIME_DIR": str(run),
           "QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu"}  # fmt: skip
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    cmd = [sys.executable, "-m", "sasha.app.shell", "--port", str(port), "--dead-proxy", "9",
           "--profile", str(tmp_path / "profile"), "--state-dir", str(state)]  # fmt: skip
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    revived = None
    try:
        assert _until(lambda: old.value in sockets, 30), "the page never used the first token"
        server.shutdown()  # the old brain stops
        server.server_close()
        write_token(state, new)  # the new one writes its token first...
        time.sleep(3.0)  # ...and a watch tick (2 s) lands before it listens
        revived = _server(seen, sockets, port)  # now it listens, on the same port
        threading.Thread(target=revived.serve_forever, daemon=True).start()
        assert _until(lambda: new.value in sockets, 15), "stuck on the error page"
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        if revived is not None:
            revived.shutdown()
