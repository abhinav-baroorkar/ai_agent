"""The app shell's rules (pure) and the real shell offscreen (needs the `app` extra)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sasha.app import policy

PORT = 8765
HAS_QT = (
    subprocess.run(
        [sys.executable, "-c", "import PySide6.QtWebEngineWidgets"], capture_output=True
    ).returncode
    == 0
)


def test_requests_only_reach_the_brain() -> None:
    ok = [
        "http://127.0.0.1:8765/",
        "http://127.0.0.1:8765/js/app.js?x=1",
        "ws://127.0.0.1:8765/ws?bot=main",
        "ws://127.0.0.1:8765/ws/browser",
        "data:image/png;base64,AAAA",
        "blob:http://127.0.0.1:8765/abc",
    ]
    for url in ok:
        assert policy.request_allowed(url, PORT), url
    refused = [
        "http://127.0.0.1:8766/",  # another local service
        "ws://127.0.0.1:9000/ws",
        "http://localhost:8765/",  # only the exact address the shell loads
        "https://127.0.0.1:8765/",
        "http://example.org/",
        "https://fonts.example/x.woff2",
        "file:///etc/passwd",
        "http://user:pw@127.0.0.1:8765/",
        "http://127.0.0.1:99999/",
        "ftp://127.0.0.1:8765/",
    ]
    for url in refused:
        assert not policy.request_allowed(url, PORT), url


def test_navigation_stays_home_and_only_the_microphone_is_granted() -> None:
    assert policy.navigation_allowed("http://127.0.0.1:8765/#settings", PORT)
    assert policy.navigation_allowed("about:blank", PORT)
    for url in ("http://example.org/", "http://127.0.0.1:8766/", "javascript:alert(1)"):
        assert not policy.navigation_allowed(url, PORT), url
    assert policy.permission_allowed("http://127.0.0.1:8765/", "MediaAudioCapture", PORT)
    for origin, kind in (
        ("http://127.0.0.1:8765", "MediaVideoCapture"),
        ("http://127.0.0.1:8765", "Notifications"),
        ("http://127.0.0.1:8766", "MediaAudioCapture"),
        ("https://example.org", "MediaAudioCapture"),
    ):
        assert not policy.permission_allowed(origin, kind, PORT)


@pytest.mark.skipif(not HAS_QT, reason="the `app` extra (PySide6) is not installed")
def test_the_real_shell_offscreen(tmp_path: Path) -> None:
    """The UI depends on its WebSocket passing the interceptor: checked first."""
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "QTWEBENGINE_CHROMIUM_FLAGS": "--use-fake-device-for-media-stream --disable-gpu",
        "XDG_RUNTIME_DIR": str(tmp_path),
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
    assert r["own"] == "open" and r["hits"]["own"] == 1  # the brain's socket works
    assert r["other_port"] == "error" and r["hits"]["other"] == 0  # never reached
    assert r["fetch"] == "blocked"
    assert r["mic"] == "granted:1"  # our origin, the fake device
    assert r["url_after_link"].startswith(f"http://127.0.0.1:{r['port']}/")  # stayed home
    assert r["blocked"] >= 3  # the other socket, the fetch, the image
    assert r["after_wake"] == "true #home"  # a wake switches to Home and plays the entrance
