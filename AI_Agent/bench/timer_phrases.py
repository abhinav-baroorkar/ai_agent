"""Bench the timer tools through the real model: does it fill the fields right, or ask?

Each phrasing in `bench/timer_phrases.yaml` is one typed turn to the local LLM (Ollama)
with the timer tools and the clock tool, on a fresh timer service with a fake clock
and an invented zone (nothing from this machine's clock or zone). For each, it records
the tool calls made, what the server resolved, and the verdict:
- `right`: one item of the right kind at a wanted time;
- `asked`: no item, and asking back is wanted (the tool asked, or the model did);
- `asked_unneeded`: no item and a question, though the phrase was clear (not a fail);
- `wrong_kind`: the right time, but a timer instead of a reminder (or similar);
- `wrong`: an item at a time not wanted, or several items: a **silent wrong time**;
- `none`: no item and no question.
Each row also records `server_line` (the typed path's TimerTruth would add "Nothing was
set.") and `uncaught_claim` (the reply claims a timer change, none was made, and no line
was added, and it is no question back: the server's own `asks_back`). Pass bar: 0
`wrong` and 0 uncaught claims. Target: at least 95% `right` or `asked`.

Text only: no voice stages, no server. The pre-merge run, one command (from AI_Agent/;
77 cases x 2 runs, about 6 minutes on the GPU):
  uv run python bench/timer_phrases.py
Options: [--profile laptop] [--model qwen3.5:9b] [--repeat 2] [--only 12] [--out DIR]
Re-score a saved file (no GPU): uv run python bench/timer_phrases.py --rescore FILE
Results: one JSON file in `<Data>/results/` and a Markdown table on stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from sasha.config import Settings, load_settings
from sasha.config_timers import TimersConfig
from sasha.conversation import current_turn
from sasha.orchestrator import Assistant, RequestFailed
from sasha.pipeline.llm import OllamaLLM
from sasha.server.timers.service import TimerService
from sasha.server.timers.tools import TimerAsk, add_timer_tools
from sasha.server.timers.truth import CHANGING_TOOLS, TimerTruth, asks_back, claims_timer
from sasha.state import JsonStore
from sasha.telemetry import TelemetryWriter
from sasha.tools import Tool, ToolRegistry
from sasha.tools.clock import clock_tool

HERE = Path(__file__).resolve().parent
ZONE = timezone(timedelta(hours=2, minutes=30), "TOY")  # invented
OK_SHARE = 0.95


def parse_local(text: str) -> datetime:
    fmt = "%Y-%m-%d %H:%M:%S" if text.count(":") == 2 else "%Y-%m-%d %H:%M"
    return datetime.strptime(text, fmt).replace(tzinfo=ZONE)


def recording(registry: ToolRegistry, log: list[dict[str, Any]]) -> ToolRegistry:
    """The same tools, each call logged with its arguments and outcome."""
    out = ToolRegistry()
    for name in registry.names:
        tool = registry.get(name)

        def handler(_tool: Tool = tool, **kwargs: Any) -> Any:
            entry: dict[str, Any] = {"tool": _tool.name, "args": kwargs}
            log.append(entry)
            try:
                result = _tool.handler(**kwargs)
            except TimerAsk as exc:
                entry["asked"] = exc.reason
                raise
            except Exception as exc:
                entry["error"] = str(exc)[:120]
                raise
            entry["result"] = result
            return result

        out.register(Tool(tool.name, tool.description, tool.parameters, handler))
    return out


def run_case(
    settings: Settings, llm: OllamaLLM, case: dict[str, Any], start: str
) -> dict[str, Any]:
    now = parse_local(str(case.get("now") or start)).timestamp()
    calls: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(dir=settings.paths.data_dir) as tmp:
        service = TimerService(
            settings.profile.timers, JsonStore(Path(tmp) / "timers.json"), None, lambda: now, ZONE
        )
        telemetry = TelemetryWriter(Path(tmp), "bench", settings.profile.name, enabled=False)
        base = ToolRegistry([clock_tool(service.now_local)])
        assistant = Assistant(settings, llm, base, telemetry)
        # As the server does (deps.make_assistant): the tools see the turn's own words.
        assistant.tools = recording(add_timer_tools(base, service, said=current_turn.get), calls)
        started = time.perf_counter()
        split = 0  # where the last turn's tool calls start
        try:
            reply = assistant.handle_text(str(case["say"])).text
            if case.get("then"):  # a two-turn case: the person answers the question
                split = len(calls)
                reply = assistant.handle_text(str(case["then"])).text
        except RequestFailed as exc:
            reply = f"(failed: {type(exc.__cause__).__name__})"
        seconds = time.perf_counter() - started
        items = list(service.items.values())
    got = [datetime.fromtimestamp(i.due, ZONE) for i in items]
    command = str(case.get("then") or case["say"])
    return {
        "say": case["say"] + (f" / {case['then']}" if case.get("then") else ""),
        "now": datetime.fromtimestamp(now, ZONE).strftime("%a %H:%M"),
        "want": case["want"],
        "kind": case["kind"],
        "calls": calls,
        "items": [
            {"kind": i.kind, "due": d.strftime("%Y-%m-%d %H:%M:%S"), "repeat": i.repeat}
            for i, d in zip(items, got, strict=True)
        ],
        "reply": reply,
        "verdict": verdict(case, items, got, calls, reply),
        "split": split,
        **judge(settings.profile.timers, command, calls[split:], reply),
        "seconds": round(seconds, 2),
    }


def judge(
    cfg: TimersConfig, command: str, calls: list[dict[str, Any]], reply: str
) -> dict[str, Any]:
    """What the server's TimerTruth (server/timers/truth.py) does with the last turn's
    reply: on a typed chat (the reply shown) and on a spoken window turn (what is said)."""
    last = [{"name": c["tool"], "ok": "result" in c, "asks": c.get("asked", "")} for c in calls]
    shown = TimerTruth(cfg, command, spoken=False).finish(reply, last)[1]
    held = TimerTruth(cfg, command, spoken=True)
    held.feed(reply)
    tail, final = held.finish(reply, last)
    added = shown[len(reply.rstrip()) :] if shown != reply else ""
    made = any(c["name"] in CHANGING_TOOLS and c["ok"] for c in last)
    claims = claims_timer(reply, cfg)
    return {
        "server_line": shown != reply,  # the server added its line or question
        "server_question": "?" in added,  # ...and what it added asks ("Morning or evening?")
        "shown": shown,
        "spoken": tail if held.holding else final,
        "claims": claims,
        "made": made,
        "uncaught_claim": uncaught(cfg, claims, made, shown != reply, reply),
    }


def uncaught(cfg: TimersConfig, claims: bool, made: bool, line: bool, reply: str) -> bool:
    """A false claim the server let through (target 0): it claims a change, none was
    made, the server added nothing, and it isn't a question back, which the server
    rightly leaves alone ("I'll set it for 3. AM or PM?"): the server's own asks_back."""
    return claims and not made and not line and not asks_back(reply, cfg)


def verdict(
    case: dict[str, Any],
    items: list[Any],
    got: list[datetime],
    calls: list[dict[str, Any]],
    reply: str,
) -> str:
    want = [str(w) for w in case["want"]]
    # A wanted time with seconds must match to the second, else to the minute.
    times = [(parse_local(w), 1 if w.count(":") == 2 else 60) for w in want if w != "ask"]
    if len(items) > 1:
        return "wrong"
    if items:
        due = got[0]
        match = any(abs((due - t).total_seconds()) < slack for t, slack in times)
        if not match:
            return "wrong"
        return "right" if items[0].kind == case["kind"] else "wrong_kind"
    asked = any("asked" in c for c in calls) or "?" in reply
    if asked:
        return "asked" if "ask" in want else "asked_unneeded"
    return "none"


def table(rows: list[dict[str, Any]]) -> str:
    lines = ["| # | now | said | tool call | resolved | verdict |", "|---|---|---|---|---|---|"]
    for n, row in enumerate(rows, 1):
        call = "; ".join(
            f"{c['tool']}({', '.join(f'{k}={v!r}' for k, v in c['args'].items())})"
            + (f" → asks {c['asked']}" if "asked" in c else "")
            for c in row["calls"] if c["tool"] != "get_current_time"
        ) or "(none)"  # fmt: skip
        resolved = ", ".join(f"{i['kind']} {i['due'][5:16]}" for i in row["items"]) or "—"
        lines.append(
            f"| {n} | {row['now']} | {row['say']} | {call} | {resolved} | {row['verdict']} |"
        )
    return "\n".join(lines)


def summarize(rows: list[dict[str, Any]], head: dict[str, Any]) -> dict[str, Any]:
    counts = Counter(r["verdict"] for r in rows)
    good = counts["right"] + counts["asked"]
    claims = sum(r["uncaught_claim"] for r in rows)
    return {
        **head,
        "counts": dict(counts),
        "right_or_asked": round(good / len(rows), 3),
        "silent_wrong": counts["wrong"],
        # The typed path's own line (truth.py): how often it fired, and false claims it missed.
        "server_line": sum(r["server_line"] for r in rows),
        "server_question": sum(r.get("server_question", False) for r in rows),
        "uncaught_claims": claims,
        "pass": counts["wrong"] == 0 and claims == 0,
        "meets_target": counts["wrong"] == 0 and good / len(rows) >= OK_SHARE,
    }


def rescore(path: Path, cfg: TimersConfig) -> None:
    """Judge a saved results file again with today's TimerTruth (no model, no GPU), and
    write `<name>-rescored.json`. Prints each row where the server adds something."""
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["rows"]
    approx = 0
    for row in rows:
        before = bool(row.get("server_line"))
        two_turn = " / " in row["say"]
        split = row.get("split")
        if split is None and two_turn:
            approx += 1  # older files: the turns' calls weren't split; all are judged
        command = row["say"].split(" / ")[-1]
        row.update(judge(cfg, command, row["calls"][split or 0 :], row["reply"]))
        if row["server_line"] or before:
            mark = "Q" if row["server_question"] else ("L" if row["server_line"] else "-")
            print(f"[{row['run']}] {mark} {row['say']}")
            print(f"      shown:  {row['shown']!r}\n      spoken: {row['spoken']!r}")
    keep = ("model", "profile", "cases", "runs")
    head = {k: data["summary"][k] for k in keep} | {"rescored": path.name}
    summary = summarize(rows, head) | {"two_turn_rows_judged_whole": approx}
    out = path.with_name(f"{path.stem}-rescored.json")
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2, default=str))
    print(json.dumps(summary, indent=2))
    print(f"Rescored: {out}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", default=None)
    p.add_argument("--model", default=None, help="Ollama model (default: the profile's)")
    p.add_argument("--host", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    p.add_argument("--cases", type=Path, default=HERE / "timer_phrases.yaml")
    p.add_argument("--repeat", type=int, default=2)
    p.add_argument("--only", type=int, default=None, help="Run one case (1-based)")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--rescore", type=Path, default=None, help="Score a saved results file")
    args = p.parse_args()
    settings = load_settings(profile=args.profile, load_env_file=False)  # no secrets needed
    if args.rescore:
        rescore(args.rescore, settings.profile.timers)
        return

    data = yaml.safe_load(args.cases.read_text(encoding="utf-8"))
    cases = data["cases"] if args.only is None else [data["cases"][args.only - 1]]
    llm = OllamaLLM(settings.profile.llm, host=args.host, model=args.model)
    rows: list[dict[str, Any]] = []
    for run in range(args.repeat):
        for case in cases:
            row = run_case(settings, llm, case, str(data["start"]))
            row["run"] = run
            rows.append(row)
            print(f"[{run}] {row['verdict']:>14}  {row['say']}", flush=True)

    head = {"model": llm.model_version(), "profile": settings.profile.name,
            "cases": len(cases), "runs": args.repeat}  # fmt: skip
    summary = summarize(rows, head)
    out_dir = args.out or settings.paths.data_dir / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = out_dir / f"timer-phrases-{stamp}.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2, default=str))
    print()
    print(table([r for r in rows if r["run"] == 0]))
    print()
    print(json.dumps(summary, indent=2))
    print(f"Raw results: {out}")


if __name__ == "__main__":
    main()
