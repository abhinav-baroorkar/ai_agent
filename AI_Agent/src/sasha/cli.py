"""Command line: `sasha chat` (text-only loop) and `sasha doctor` (connectivity checks)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

from sasha import __version__
from sasha.config import Settings, load_settings
from sasha.orchestrator import Assistant, Reply
from sasha.pipeline.llm import OllamaError, OllamaLLM
from sasha.telemetry import TelemetryWriter, snapshot_resources
from sasha.tools import build_tools
from sasha.tools.home_assistant import HomeAssistantClient

HELP = "Commands: /reset (forget conversation), /tools (list tools), /quit"


def make_llm(settings: Settings, small: bool = False) -> OllamaLLM:
    cfg = settings.profile.llm
    model = cfg.small_model if small and cfg.small_model else cfg.model
    return OllamaLLM(cfg, host=settings.secrets.ollama_host, model=model)


def make_assistant(settings: Settings, small: bool = False) -> tuple[Assistant, list[str]]:
    tools, warnings = build_tools(settings)
    telemetry = TelemetryWriter(
        settings.paths.telemetry_dir,
        assistant=settings.assistant.name,
        profile=settings.profile.name,
        enabled=settings.profile.telemetry.enabled,
        log_text=settings.profile.telemetry.log_text,
    )
    return Assistant(settings, make_llm(settings, small), tools, telemetry), warnings


def format_timing(reply: Reply) -> str:
    stages = reply.telemetry.get("stages", [])
    parts = [f"{s['name']} {s['latency_ms']:.0f}ms" for s in stages]
    tps = [s["meta"].get("tokens_per_s") for s in stages if s["meta"].get("tokens_per_s")]
    total = reply.telemetry.get("total_ms", 0)
    extra = f", {tps[-1]:.0f} tok/s" if tps else ""
    return f"  [{' | '.join(parts)} | total {total:.0f}ms{extra}]"


def run_chat(
    assistant: Assistant,
    show_timing: bool = False,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
) -> int:
    name = assistant.name
    write(f"{name} (text mode, model {assistant.llm.model_id}). {HELP}")
    while True:
        try:
            text = read("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            write("")
            return 0
        if not text:
            continue
        if text in ("/quit", "/exit"):
            return 0
        if text == "/reset":
            assistant.reset()
            write("(conversation cleared)")
            continue
        if text == "/tools":
            write(", ".join(assistant.tools.names) or "(no tools enabled)")
            continue
        try:
            reply = assistant.handle_text(text)
        except OllamaError as exc:
            write(f"[error] {exc}")
            continue
        for call in reply.tool_calls:
            status = "ok" if call["ok"] else "failed"
            write(f"  -> {call['name']}({call['arguments']}) {status}")
        write(f"{name}> {reply.text}")
        if show_timing:
            write(format_timing(reply))


def run_doctor(settings: Settings) -> int:
    ok = True
    print(f"Assistant: {settings.assistant.name}  |  profile: {settings.profile.name}")
    print(f"Root: {settings.paths.root}")
    res = snapshot_resources()
    vram = f"{res.vram_used_mb:.0f}/{res.vram_total_mb:.0f} MB" if res.vram_total_mb else "n/a"
    print(f"RAM used: {res.ram_used_mb:.0f}/{res.ram_total_mb:.0f} MB  |  VRAM: {vram}")

    llm = make_llm(settings)
    try:
        models = llm.list_models()
        print(f"[ok]   Ollama at {llm.host} ({len(models)} models)")
        cfg = settings.profile.llm
        for model in filter(None, [cfg.model, cfg.small_model]):
            present = model in models or f"{model}:latest" in models
            ok &= present or model != cfg.model
            hint = "" if present else f"  -> run: ollama pull {model}"
            print(f"[{'ok' if present else 'miss'}] model {model}{hint}")
    except Exception as exc:
        ok = False
        print(f"[fail] Ollama at {llm.host}: {type(exc).__name__}: {exc}")

    secrets, ha_cfg = settings.secrets, settings.tools.home_assistant
    if not ha_cfg.enabled:
        print("[skip] Home Assistant disabled in tools.yaml")
    elif not (secrets.ha_url and secrets.ha_token):
        print("[skip] Home Assistant: set HA_URL and HA_TOKEN in .env")
    else:
        try:
            ha = HomeAssistantClient(secrets.ha_url, secrets.ha_token, ha_cfg)
            msg = ha.ping()
            n = len(ha.list_devices())
            print(f"[ok]   Home Assistant at {secrets.ha_url}: {msg} ({n} exposed devices)")
        except Exception as exc:
            ok = False
            print(f"[fail] Home Assistant at {secrets.ha_url}: {exc}")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sasha", description="Local voice assistant (text mode).")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--profile", help="Hardware profile name (default: $SASHA_PROFILE or pc)")
    sub = p.add_subparsers(dest="command")
    chat = sub.add_parser("chat", help="Text-only conversation loop (default)")
    chat.add_argument("--timing", action="store_true", help="Print per-stage latency")
    chat.add_argument("--small", action="store_true", help="Use the profile's small_model")
    sub.add_parser("doctor", help="Check Ollama and Home Assistant connectivity")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(profile=args.profile)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[config error] {exc}", file=sys.stderr)
        return 2
    if args.command == "doctor":
        return run_doctor(settings)
    assistant, warnings = make_assistant(settings, small=getattr(args, "small", False))
    for w in warnings:
        print(f"[warn] {w}")
    return run_chat(assistant, show_timing=getattr(args, "timing", False))
