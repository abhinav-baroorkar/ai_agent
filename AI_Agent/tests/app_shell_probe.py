"""Run the real app shell offscreen against a local test server; print what happened.

Used by tests/test_app_shell.py in a subprocess (QT_QPA_PLATFORM=offscreen, no display).
The "brain" here is a small server on a free 127.0.0.1 port that serves one page and a
WebSocket at /ws; a second server on another port counts any connection that reaches
it (it must get none). The page tries: our WebSocket, the other port's WebSocket, an
outside fetch, an outside image, a link to another site, and the microphone.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from typing import Any

from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

PAGE = """<!doctype html><title>probe</title><script>
const out = {};
function done(){ if (Object.keys(out).length === 4) document.title = 'R' + JSON.stringify(out); }
function ws(key, url){ try { const s = new WebSocket(url);
  s.onopen = () => { out[key] = 'open'; s.close(); done(); };
  s.onerror = () => { out[key] = 'error'; done(); }; } catch (e) { out[key] = 'throw'; done(); } }
ws('own', `ws://${location.host}/ws`);
ws('other_port', 'ws://127.0.0.1:OTHER/ws');
fetch('http://example.org/').then(() => { out.fetch = 'leaked'; done(); })
  .catch(() => { out.fetch = 'blocked'; done(); });
navigator.mediaDevices.getUserMedia({audio: true})
  .then(s => { out.mic = 'granted:' + s.getAudioTracks().length; done(); })
  .catch(e => { out.mic = 'denied:' + e.name; done(); });
</script><img src="http://example.org/x.png"><a id="away" href="http://example.org/">x</a>"""


def start_servers() -> tuple[int, int, dict[str, int], asyncio.AbstractEventLoop]:
    hits = {"own": 0, "other": 0}
    ready = threading.Event()
    ports: dict[str, int] = {}
    loop = asyncio.new_event_loop()

    async def own(ws: Any) -> None:
        hits["own"] += 1
        await ws.wait_closed()

    async def other(ws: Any) -> None:
        hits["other"] += 1
        await ws.wait_closed()

    def page(conn: Any, request: Any) -> Response | None:
        if request.path == "/ws":
            return None  # upgrade
        body = PAGE.replace("OTHER", str(ports["other"])).encode()
        headers = Headers([("Content-Type", "text/html"), ("Content-Length", str(len(body)))])
        return Response(200, "OK", headers, body)

    async def main() -> None:
        a = await serve(own, "127.0.0.1", 0, process_request=page)
        b = await serve(other, "127.0.0.1", 0)
        ports["own"] = a.sockets[0].getsockname()[1]
        ports["other"] = b.sockets[0].getsockname()[1]
        ready.set()
        await asyncio.Future()

    threading.Thread(target=lambda: loop.run_until_complete(main()), daemon=True).start()
    ready.wait(10)
    return ports["own"], ports["other"], hits, loop


def run(profile: Path) -> dict[str, Any]:
    port, _other, hits, _ = start_servers()
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from sasha.app.shell import build

    app = QApplication(sys.argv[:1])
    shell = build(app, port, profile, "probe", None)
    result: dict[str, Any] = {}

    def title(t: str) -> None:
        if t.startswith("R"):
            result.update(json.loads(t[1:]))
            shell.page.runJavaScript("document.getElementById('away').click()")
            QTimer.singleShot(800, finish)

    def finish() -> None:
        result["url_after_link"] = shell.page.url().toString()
        from sasha.app.shell import bring_up

        bring_up(app, shell, True, "probe")  # the wake: Home, and the entrance event
        QTimer.singleShot(300, woke)

    def woke() -> None:
        def got(value: Any) -> None:
            result["after_wake"] = value
            app.quit()

        shell.page.runJavaScript("(window.assistantWokeAt > 0) + ' ' + location.hash", 0, got)

    shell.page.titleChanged.connect(title)
    QTimer.singleShot(15000, app.quit)
    shell.window.show()
    app.exec()
    result["hits"] = dict(hits)
    result["blocked"] = shell.interceptor.blocked
    result["port"] = port
    return result


if __name__ == "__main__":
    print("RESULT " + json.dumps(run(Path(sys.argv[1]))), flush=True)
