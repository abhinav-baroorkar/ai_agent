"""The owner's 2-minute check of the app shell (app-shell design 5): run it yourself.

It serves the web UI in mock mode (no brain, no microphone, no model) on a free
127.0.0.1 port, and opens it twice side by side: in the app shell and in the Chrome app
window. Each counts its orb's frames for 30 s and reports back. Prints both frame rates
and PASS if the shell is within 5 % of Chrome. Then a short checklist for what only a
person can see. Nothing is recorded or kept.

Usage (from AI_Agent/, with `uv sync --extra app`): uv run python bench/app_shell_check.py
"""

from __future__ import annotations

import functools
import http.server
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from sasha.app.launcher import available
from sasha.app.start_check import APT_HINT, MISSING_CURSOR, failure_message, failure_reason
from sasha.server.launch import browser_command, browser_env, dead_proxy_port

WEB = Path(__file__).resolve().parents[1] / "src" / "sasha" / "avatar" / "web"
SECONDS = 30
FPS_PAGE = """<!doctype html><meta charset=utf-8><title>frame check</title>
<style>html,body,iframe{margin:0;border:0;width:100%;height:100%}</style>
<iframe id=f src="/?mock=1"></iframe><script>
const who = new URLSearchParams(location.search).get('who');
const f = document.getElementById('f');
f.onload = () => { const w = f.contentWindow; let n = 0, start = 0;
  function tick(t) { if (!start) start = t; n++;
    if (t - start < SECONDS_MS) return w.requestAnimationFrame(tick);
    fetch('/report', {method: 'POST', body: JSON.stringify({who, fps: n * 1000 / (t - start)})}); }
  setTimeout(() => w.requestAnimationFrame(tick), 3000); };  // let the orb start first
</script>""".replace("SECONDS_MS", str(SECONDS * 1000))
results: dict[str, float] = {}


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/fps.html"):
            body = FPS_PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self) -> None:
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        results[str(data["who"])] = float(data["fps"])
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


def main() -> int:
    if not available():
        print("Install the app extra first: uv sync --extra app")
        return 2
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(WEB))
    )
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    proxy = dead_proxy_port()
    url = f"http://127.0.0.1:{port}/fps.html"
    with tempfile.TemporaryDirectory(prefix="shell-check-", dir=WEB.parents[4] / "Data") as tmp:
        shell = subprocess.Popen([
            sys.executable, "-m", "sasha.app.shell", "--port", str(port),
            "--dead-proxy", str(proxy), "--profile", f"{tmp}/shell", "--title", "Shell check",
            "--start-path", "/fps.html?who=shell",
        ], stderr=subprocess.PIPE, text=True)  # fmt: skip
        time.sleep(5)
        if shell.poll() is not None:  # no window came up: say why, as `sasha window` does
            reason = failure_reason(shell.stderr.read() if shell.stderr else "")
            print(f"FAIL: {failure_message(reason, fallback=False)}")
            if reason == MISSING_CURSOR:
                print(f"To fix it: {APT_HINT}")
            server.shutdown()
            return 1
        chrome_cmd = browser_command(url + "?who=chrome", Path(tmp) / "chrome", proxy)
        chrome = None
        if chrome_cmd:
            for sub in ("home", "tmp"):
                (Path(tmp) / "chrome" / sub).mkdir(parents=True, exist_ok=True)
            chrome = subprocess.Popen(
                chrome_cmd, env=browser_env(Path(tmp) / "chrome"), cwd=Path(tmp) / "chrome"
            )
        deadline = time.time() + SECONDS + 30
        want = {"shell", "chrome"} if chrome else {"shell"}
        while time.time() < deadline and not want <= set(results):
            time.sleep(0.5)
        for proc in (shell, chrome):
            if proc is not None:
                proc.terminate()
    server.shutdown()
    shell_fps, chrome_fps = results.get("shell"), results.get("chrome")
    print(f"Frame rate, app shell:     {shell_fps:.1f} fps" if shell_fps else "shell: no result")
    print(f"Frame rate, Chrome window: {chrome_fps:.1f} fps" if chrome_fps else "chrome: no result")
    passed = bool(shell_fps and chrome_fps and shell_fps >= 0.95 * chrome_fps)
    print("PASS" if passed else "FAIL (or not measured)")
    print(
        "\nThen, by hand (nothing is recorded):\n"
        "  [ ] `sasha window` opens the assistant's own window (not Chrome).\n"
        "  [ ] In it, press the microphone button: the level moves while you speak.\n"
        "  [ ] Put another window in front, say the wake phrase: the window comes to the\n"
        '      front, or GNOME shows "… is ready" or a "listening" notice. Note which.\n'
        "  [ ] Run `sasha window` again: the same window comes up, no second one."
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
