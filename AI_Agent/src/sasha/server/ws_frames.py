"""The frames a conversation sends about its tools and its reply (split from ws.py).

`asks` is a tool's fixed reason label ("person_check", the timers' ask-backs). It goes
to the page only when it looks like one: lowercase words with underscores, optionally
":" and a short lowercase tag (review S1 of takeover-followups-e93d3c2). Anything else
is dropped, so no tool can put free text into a frame through it.
"""

from __future__ import annotations

import re
from typing import Any

from sasha.orchestrator import Reply, RequestFailed
from sasha.pipeline.llm import OllamaError

# Matched whole (fullmatch): "$" alone would also let a trailing newline through.
LABEL = re.compile(r"[a-z_]{1,32}(:[a-z]{1,16})?")


def label(asks: Any) -> dict[str, str]:
    """`{"asks": asks}` for a well-formed label, else nothing."""
    return {"asks": asks} if isinstance(asks, str) and LABEL.fullmatch(asks) else {}


def tool_frame(name: str, ok: bool, asks: str = "") -> dict[str, Any]:
    return {"type": "tool", "name": name, "ok": ok, **label(asks)}


def call_summaries(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The reply's `tool_calls`: name, ok and the label only (never the arguments)."""
    return [{"name": c["name"], "ok": c["ok"], **label(c.get("asks"))} for c in tool_calls]


def timing(reply: Reply) -> dict[str, Any]:
    stages = reply.telemetry.get("stages", [])
    tps = [s["meta"]["tokens_per_s"] for s in stages if s["meta"].get("tokens_per_s")]
    return {
        "total_ms": reply.telemetry.get("total_ms", 0),
        "tokens_per_s": tps[-1] if tps else None,
    }


def failure_message(failed: RequestFailed) -> str:
    cause = failed.__cause__
    if isinstance(cause, OllamaError):
        return str(cause)
    return f"Something went wrong ({type(cause).__name__})"
