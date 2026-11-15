"""Do wake turns just answer? Counts replies that bring up the time or date unasked.

From the owner's real use: every wake answer was "I can tell you the time and date".
This builds the wake prompt the way the server does (the voice bot's prompt, the
generated abilities for the wake turn's tools with device control off, the spoken style
and the wake note), runs a short phrase set through the real model (text only: no
speech stages, no server) and prints, per phrase, the reply, the tool calls, and
whether it mentions the time, date or clock. `--wording main` uses the wording before
this fix, for a before/after.

Usage (from AI_Agent/): uv run python bench/wake_answers.py [--wording new|main] [--repeat 1]
Results: one JSON file in `<Data>/results/`. Target: 0 mentions outside the last two
categories (a device request while control is off, and "what can you do").
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sasha.config import load_settings
from sasha.config_prompts import PromptLines
from sasha.orchestrator import Assistant, RequestFailed
from sasha.pipeline.llm import OllamaLLM
from sasha.prompting import build_prompt, prompt_lines
from sasha.telemetry import TelemetryWriter
from sasha.tools import Tool, ToolRegistry
from sasha.tools.clock import clock_tool

PHRASES = [
    ("general", "what's the capital of Australia"),
    ("general", "why is the sky blue"),
    ("general", "who wrote Hamlet"),
    ("how-to", "how do I boil an egg"),
    ("how-to", "how do I get a stain out of a shirt"),
    ("small talk", "how are you today"),
    ("small talk", "tell me a joke"),
    ("small talk", "recite a short poem"),
    ("small talk", "good morning"),
    ("device, control off", "turn off the kitchen light"),
    ("what can you do", "what can you do"),
]
ALLOWED = {"device, control off", "what can you do"}  # mentions there aren't counted
TIME_WORDS = re.compile(r"\b(time|date|clock|o'clock|\d{1,2}:\d{2})\b", re.I)
DEVICES = [{"entity_id": "light.kitchen", "name": "Kitchen light", "state": "on"}]  # invented

# The wording on main before this fix (config_prompts.py at f5581ab), for --wording main.
MAIN_WORDING = {
    "can_head": "What you can do right now, and only this:",
    "no_tools": "You have no tools right now: you can only talk and answer from what you know.",
    "answer_freely": "",
    "cannot_head": "What you can't do:",
    "never_claim": (
        "When asked what you can do, say only the things above, briefly. Never claim any "
        "other ability; if asked for something else, say you can't do that yet. Use a "
        "tool only when the request needs it, and don't mention a tool's result (such "
        "as the time) unless it answers what was asked."
    ),
}


def wake_tools() -> ToolRegistry:
    """The wake turn's tools with device control off: read-only devices and the clock."""
    obj: dict[str, Any] = {"type": "object", "properties": {}}
    state = {"type": "object", "properties": {"entity_id": {"type": "string"}}}
    return ToolRegistry([
        Tool("list_devices", "List the smart-home devices.", obj, lambda: DEVICES),
        Tool("get_state", "The state of one device.", state, lambda entity_id="": DEVICES[0]),
        clock_tool(),
    ])  # fmt: skip


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--wording", choices=["new", "main"], default="new")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--host", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    args = p.parse_args()
    settings = load_settings(load_env_file=False)
    lines = prompt_lines(settings)
    if args.wording == "main":
        lines = PromptLines(**{**lines.model_dump(), **MAIN_WORDING})
    tools = wake_tools()
    base = settings.assistant.render_system_prompt()  # the voice bot is built in
    prompt = build_prompt(base, tools.names, lines, spoken=True)
    prompt = f"{prompt}\n{settings.profile.wake.prompt_note}"
    llm = OllamaLLM(settings.profile.llm, host=args.host)
    rows: list[dict[str, Any]] = []
    for run in range(args.repeat):
        for category, said in PHRASES:
            telemetry = TelemetryWriter(Path("."), "bench", "p", enabled=False)
            assistant = Assistant(settings, llm, tools, telemetry, system_prompt=prompt)
            started = time.perf_counter()
            try:
                reply = assistant.handle_text(said, input_mode="wake")
                text, calls = reply.text, [c["name"] for c in reply.tool_calls]
            except RequestFailed as exc:
                text, calls = f"(failed: {type(exc.__cause__).__name__})", []
            mentions = bool(TIME_WORDS.search(text)) or "get_current_time" in calls
            rows.append({"run": run, "category": category, "said": said, "reply": text,
                         "calls": calls, "mentions_time": mentions,
                         "seconds": round(time.perf_counter() - started, 1)})  # fmt: skip
            print(f"[{run}] {'TIME' if mentions else '    '}  {said!r}: {text[:110]!r}", flush=True)
    counted = [r for r in rows if r["category"] not in ALLOWED]
    summary = {
        "wording": args.wording,
        "model": llm.model_version(),
        "phrases": len(PHRASES),
        "runs": args.repeat,
        "unasked_time_mentions": sum(r["mentions_time"] for r in counted),
        "out_of": len(counted),
    }
    out_dir = settings.paths.data_dir / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = out_dir / f"wake-answers-{args.wording}-{stamp}.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    print(json.dumps(summary, indent=2))
    print(f"Raw results: {out}")


if __name__ == "__main__":
    main()
