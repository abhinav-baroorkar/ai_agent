"""The page's Content-Security-Policy: `connect-src 'self'` lets the UI reach its own
WebSocket and nothing else on this machine, in Qt WebEngine and in Chrome (CSP Level 3:
'self' matches ws: to the same host and port). Review should-fix, fix/app-shell-hardening.

Both engines load a page from a small local server with only the CSP to stop them (the
shell's interceptor is off; Chrome's dead proxy doesn't cover loopback). A second server
on another port counts every connection it gets: it must get none.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.conftest import REPO_ROOT

INDEX = REPO_ROOT / "AI_Agent/src/sasha/avatar/web/index.html"
HAS_QT = (
    subprocess.run(
        [sys.executable, "-c", "import PySide6.QtWebEngineWidgets"], capture_output=True
    ).returncode
    == 0
)


def page_csp() -> str:
    m = re.search(r'Content-Security-Policy" content="([^"]+)"', INDEX.read_text())
    assert m, "index.html has no CSP"
    return m.group(1)


def connect_src() -> str:
    """The page's connect-src, applied alone in the probes (the page's full policy also
    refuses inline scripts, which the probe pages use)."""
    directives = dict(d.strip().split(" ", 1) for d in page_csp().split(";") if " " in d.strip())
    return "connect-src " + directives["connect-src"]


def test_the_page_allows_only_its_own_origin_to_connect() -> None:
    directives = dict(d.strip().split(" ", 1) for d in page_csp().split(";") if " " in d.strip())
    assert directives["connect-src"] == "'self'"  # no ws://127.0.0.1:* wildcard any more


@pytest.mark.skipif(not HAS_QT, reason="the `app` extra (PySide6) is not installed")
def test_qt_webengine_keeps_to_its_own_websocket_by_csp_alone(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "QTWEBENGINE_CHROMIUM_FLAGS": "--use-fake-device-for-media-stream --disable-gpu",
        "XDG_RUNTIME_DIR": str(tmp_path),
        "PROBE_NO_INTERCEPT": "1",  # only the CSP stops anything here
        "PROBE_CSP": connect_src(),
    }
    probe = Path(__file__).with_name("app_shell_probe.py")
    out = subprocess.run(
        [sys.executable, str(probe), str(tmp_path / "profile")],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    line = next((x for x in out.stdout.splitlines() if x.startswith("RESULT ")), None)
    assert line, out.stderr[-2000:]
    r = json.loads(line[len("RESULT ") :])
    assert r["own"] == "open" and r["hits"]["own"] == 1
    assert r["other_port"] == "error" and r["hits"]["other"] == 0


def _chrome() -> str | None:
    from sasha.browser.chrome import find_chrome

    return find_chrome()


@pytest.mark.skipif(_chrome() is None, reason="no Chrome installed")
def test_chrome_keeps_to_its_own_websocket_by_csp_alone(tmp_path: Path) -> None:
    import asyncio

    from tests.app_shell_probe import Headers, Response, serve

    hits = {"own": 0, "other": 0}
    report: dict[str, Any] = {}
    ports: dict[str, int] = {}
    ready = threading.Event()
    meta = f'<meta http-equiv="Content-Security-Policy" content="{connect_src()}">'
    page = (
        "<!doctype html><title>csp</title>" + meta + "<script>"
        "const out = {}; const done = () => { if (Object.keys(out).length === 2)"
        " fetch('/report?r=' + encodeURIComponent(JSON.stringify(out))); };"
        "function ws(k, u) { try { const s = new WebSocket(u);"
        " s.onopen = () => { out[k] = 'open'; s.close(); done(); };"
        " s.onerror = () => { out[k] = 'error'; done(); }; }"
        " catch (e) { out[k] = 'throw'; done(); } }"
        "ws('own', `ws://${location.host}/ws`); ws('other', 'ws://127.0.0.1:OTHER/ws');"
        "</script>"
    )

    async def own(ws: Any) -> None:
        hits["own"] += 1
        await ws.wait_closed()

    async def other(ws: Any) -> None:
        hits["other"] += 1
        await ws.wait_closed()

    def http(conn: Any, request: Any) -> Response | None:
        if request.path == "/ws":
            return None
        if request.path.startswith("/report"):
            report.update(json.loads(parse_qs(urlsplit(request.path).query)["r"][0]))
            body = b""
        else:
            body = page.replace("OTHER", str(ports["other"])).encode()
        headers = Headers([("Content-Type", "text/html"), ("Content-Length", str(len(body)))])
        return Response(200, "OK", headers, body)

    async def main() -> None:
        a = await serve(own, "127.0.0.1", 0, process_request=http)
        b = await serve(other, "127.0.0.1", 0)
        ports["own"], ports["other"] = a.sockets[0].getsockname()[1], b.sockets[0].getsockname()[1]
        ready.set()
        await asyncio.Future()

    loop = asyncio.new_event_loop()
    threading.Thread(target=lambda: loop.run_until_complete(main()), daemon=True).start()
    assert ready.wait(10)
    chrome = subprocess.Popen(
        [
            str(_chrome()),
            "--headless=new",
            f"--user-data-dir={tmp_path / 'chrome'}",
            "--no-first-run",
            "--disable-gpu",
            "--disable-background-networking",
            "--proxy-server=http://127.0.0.1:9",  # a dead proxy: outside addresses fail
            f"http://127.0.0.1:{ports['own']}/",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 30
        while time.time() < deadline and len(report) < 2:
            time.sleep(0.2)
    finally:
        chrome.terminate()
        chrome.wait(10)
    assert report == {"own": "open", "other": "error"}, report
    assert hits["other"] == 0
