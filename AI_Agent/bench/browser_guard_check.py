"""Measure, on the real headless Chrome, that every request goes through our proxy (B2).

Nothing leaves this machine: the proxy's resolver refuses every name (no DNS), test
pages come from CDP `Fetch` on `test.invalid`, and local "canary" servers on 127.0.0.1
and ::1 count any request that reached them directly. Pass = no canary hit, and the
proxy saw (and refused) every loopback/private address in every spelling.

Usage (from AI_Agent/): uv run python bench/browser_guard_check.py [--out DIR]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sasha.browser.chrome import new_page, start_chrome
from sasha.browser.proxy import FilteringProxy
from sasha.config import load_settings

PAGE = """<!doctype html><title>guard test</title>
<p>guard test page</p>
<img src="http://127.0.0.1:{p}/img">
<img src="http://[::1]:{p}/img6">
<script>
fetch("http://10.0.0.1/x").catch(()=>{{}});
fetch("http://169.254.169.254/latest/meta-data/").catch(()=>{{}});
try {{ new WebSocket("ws://127.0.0.1:{p}/ws"); }} catch (e) {{}}
navigator.sendBeacon && navigator.sendBeacon("http://192.168.1.1/b", "x");
</script>"""


def addresses(port: int) -> list[str]:
    hosts = [
        "127.0.0.1",
        "127.1",
        "0",
        "0.0.0.0",
        "2130706433",
        "0x7f000001",
        "017700000001",
        "0177.0.0.1",
        "0x7f.1",
        "localhost",
        "[::1]",
        "[::ffff:127.0.0.1]",
        "[64:ff9b::7f00:1]",
        "[::]",
    ]
    local = [f"http://{h}:{port}/" for h in hosts]
    private = [
        "http://192.168.1.1/",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://169.254.169.254/",
        "http://100.64.0.1/",
        "http://[fe80::1]/",
        "http://[fd00::1]/",
        "https://127.0.0.1/",
        "https://192.168.1.1/",
        "http://router.lan/",
        "http://example.invalid/",
    ]
    return local + private


async def run(data_dir: Path) -> dict[str, Any]:
    hits: list[str] = []

    async def canary(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        hits.append((await reader.read(200)).split(b"\r\n", 1)[0].decode("latin-1"))
        writer.close()

    v4 = await asyncio.start_server(canary, "127.0.0.1", 0)
    port = v4.sockets[0].getsockname()[1]
    v6 = await asyncio.start_server(canary, "::1", port)

    async def no_dns(host: str, p: int) -> list[Any]:
        raise OSError("no DNS in this measurement")

    proxy = FilteringProxy(no_dns)
    chrome = await start_chrome(data_dir, await proxy.start())
    results: list[dict[str, Any]] = []
    try:
        _, sid = await new_page(chrome)
        pipe = chrome.pipe
        await pipe.call("Page.enable", session_id=sid)
        await pipe.call(
            "Fetch.enable", {"patterns": [{"urlPattern": "*://test.invalid/*"}]}, session_id=sid
        )

        def paused(params: dict[str, Any]) -> None:
            body = base64.b64encode(PAGE.format(p=port).encode()).decode()
            asyncio.get_running_loop().create_task(
                pipe.call(
                    "Fetch.fulfillRequest",
                    {
                        "requestId": params["requestId"],
                        "responseCode": 200,
                        "body": body,
                        "responseHeaders": [{"name": "Content-Type", "value": "text/html"}],
                    },
                    session_id=params["_session"],
                )
            )

        pipe.on("Fetch.requestPaused", paused)
        for url in addresses(port):
            before = sum(proxy.stats.refused.values()) + proxy.stats.bad_requests
            nav = await pipe.call("Page.navigate", {"url": url}, session_id=sid, timeout=15)
            await asyncio.sleep(0.3)
            after = sum(proxy.stats.refused.values()) + proxy.stats.bad_requests
            results.append(
                {
                    "url": url,
                    "error": nav.get("errorText", ""),
                    "seen_by_proxy": after > before,
                    "canary_hits": len(hits),
                }
            )
        before = dict(proxy.stats.refused)
        await pipe.call("Page.navigate", {"url": "https://test.invalid/page"}, session_id=sid)
        await asyncio.sleep(1.5)
        page = {k: proxy.stats.refused[k] - before.get(k, 0) for k in proxy.stats.refused}
    finally:
        await chrome.stop()
        await proxy.stop()
        v4.close()
        v6.close()
    return {
        "chrome": chrome.version,
        "navigations": results,
        "test_page_subrequests_refused": page,
        "proxy_refused_total": dict(proxy.stats.refused),
        "proxy_allowed": proxy.stats.allowed,
        "canary_hits": hits,
        "passed": not hits and all(r["seen_by_proxy"] or r["error"] for r in results),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()
    settings = load_settings()
    report = asyncio.run(run(settings.paths.data_dir))
    for r in report["navigations"]:
        print(f"{r['url']:44} proxy={'yes' if r['seen_by_proxy'] else 'NO ':3} {r['error']}")
    print("test page sub-requests refused by proxy:", report["test_page_subrequests_refused"])
    print("canary hits (requests that bypassed the proxy):", report["canary_hits"])
    print("proxy allowed connections:", report["proxy_allowed"], "| PASSED:", report["passed"])
    out = args.out or settings.paths.data_dir / "results"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"browser_guard-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Saved {path}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
