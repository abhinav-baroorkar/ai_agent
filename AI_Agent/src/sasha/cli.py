"""Command line: `sasha chat` (text loop), `doctor`, `serve` and `window` (brain server),
`listen` (wake word listener) and `autostart` (start them at login)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from typing import Any

import yaml

from sasha import __version__
from sasha.config import Settings, load_settings
from sasha.orchestrator import Assistant, Reply, RequestFailed
from sasha.pipeline.llm import OllamaError, OllamaLLM
from sasha.prompting import build_prompt, prompt_lines
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
    # Text only, so no spoken style; the prompt says what these tools can really do.
    prompt = build_prompt(
        settings.assistant.render_system_prompt(), tools.names, prompt_lines(settings)
    )
    llm = make_llm(settings, small)
    return Assistant(settings, llm, tools, telemetry, system_prompt=prompt), warnings


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
        except KeyboardInterrupt:
            _write_tool_calls(assistant.last_tool_calls, write)
            write("(interrupted)")
            continue
        except RequestFailed as failed:
            # Tools may already have changed something; say so before the error.
            _write_tool_calls(failed.tool_calls, write)
            cause = failed.__cause__
            if isinstance(cause, OllamaError):
                write(f"[error] {cause}")
            else:
                write(f"[error] {type(cause).__name__}: {cause}")
            continue
        _write_tool_calls(reply.tool_calls, write)
        write(f"{name}> {reply.text}")
        if show_timing:
            write(format_timing(reply))


def _write_tool_calls(calls: list[dict[str, Any]], write: Callable[[str], None]) -> None:
    for call in calls:
        status = "ok" if call["ok"] else "failed"
        write(f"  -> {call['name']}({call['arguments']}) {status}")


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
            ha = HomeAssistantClient(secrets.ha_url, secrets.ha_token.get_secret_value(), ha_cfg)
            msg = ha.ping()
            n = len(ha.list_devices())
            print(f"[ok]   Home Assistant at {secrets.ha_url}: {msg} ({n} exposed devices)")
        except Exception as exc:
            ok = False
            print(f"[fail] Home Assistant at {secrets.ha_url}: {exc}")
    print(app_window_line())
    return 0 if ok else 1


def app_window_line() -> str:
    """`sasha doctor`: can the assistant's own window start? (A warning only.)"""
    from sasha.app.launcher import available
    from sasha.app.start_check import APT_HINT, cursor_library_present

    if not available():
        return "[info] App window: not installed (the Chrome window is used); uv sync --extra app"
    if not cursor_library_present():
        return f"[warn] App window: a system package is missing: libxcb-cursor0  -> run: {APT_HINT}"
    return "[ok]   App window: ready"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sasha", description="Local voice assistant (text mode).")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--profile", help="Hardware profile name (default: $SASHA_PROFILE or laptop)")
    sub = p.add_subparsers(dest="command")
    chat = sub.add_parser("chat", help="Text-only conversation loop (default)")
    chat.add_argument("--timing", action="store_true", help="Print per-stage latency")
    chat.add_argument("--small", action="store_true", help="Use the profile's small_model")
    sub.add_parser("doctor", help="Check Ollama and Home Assistant connectivity")
    for name, text in (
        ("serve", "Run the brain server (HTTP + WebSocket + web UI)"),
        ("window", "Open the assistant in an app window (starts the server if needed)"),
    ):
        cmd = sub.add_parser(name, help=text)
        cmd.add_argument("--host", help="Loopback address to use (default from the profile)")
        cmd.add_argument("--port", type=int, help="Port (default from the profile)")
        if name == "window":
            cmd.add_argument(
                "--chrome", action="store_true", help="Use the Chrome window, not the app shell"
            )
            cmd.add_argument(
                "--attach",
                action="store_true",
                help="Never start the server: wait for the running one (start at login)",
            )
    listen = sub.add_parser("listen", help="Wake word listener (microphone; foreground)")
    listen.add_argument(
        "--debug-dump-audio",
        action="store_true",
        help="Save every segment sent to the brain under Data/debug-audio/ (this run only)",
    )
    listen.add_argument(
        "--no-pause-on-battery", action="store_true", help="Keep listening on battery power"
    )
    listen.add_argument(
        "--end-silence-ms", type=int, default=None, help="Silence that ends a command (600)"
    )
    auto = sub.add_parser(
        "autostart", help="Start brain, listener and window at login: on|off|show"
    )
    auto.add_argument("action", choices=["on", "off", "show"])
    auto.add_argument("--yes", action="store_true", help="With `on`: really link and enable")
    menu = sub.add_parser("app-menu", help="The assistant in the applications menu: on|off|show")
    menu.add_argument("action", choices=["on", "off", "show"])
    menu.add_argument("--yes", action="store_true", help="With `on`: really write the entry")
    return p


def run_listen_command(settings: Settings, args: argparse.Namespace) -> int:
    from sasha.listener.app import ListenOptions, run_listen
    from sasha.listener.gate import END_SILENCE_MS

    options = ListenOptions(
        debug_dump_audio=args.debug_dump_audio,
        pause_on_battery=not args.no_pause_on_battery,
        end_silence_ms=args.end_silence_ms or END_SILENCE_MS,
    )
    return run_listen(settings, options)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(profile=args.profile)
    except (FileNotFoundError, ValueError, yaml.YAMLError) as exc:
        print(f"[config error] {exc}", file=sys.stderr)
        return 2
    if args.command == "doctor":
        return run_doctor(settings)
    if args.command == "listen":
        return run_listen_command(settings, args)
    if args.command == "autostart":
        from sasha.listener.autostart import autostart
        from sasha.pipeline.model_cache import project_root

        return autostart(
            args.action, project_root(settings.paths.root), yes=args.yes, settings=settings
        )
    if args.command == "app-menu":
        from sasha.app.menu import app_menu
        from sasha.pipeline.model_cache import project_root

        return app_menu(args.action, settings, project_root(settings.paths.root), yes=args.yes)
    if args.command in ("serve", "window"):
        from sasha.server.launch import open_window, run_server  # FastAPI only when needed

        if args.command == "window":
            return open_window(
                settings, host=args.host, port=args.port, chrome=args.chrome, attach=args.attach
            )
        return run_server(settings, host=args.host, port=args.port)
    assistant, warnings = make_assistant(settings, small=getattr(args, "small", False))
    for w in warnings:
        print(f"[warn] {w}")
    return run_chat(assistant, show_timing=getattr(args, "timing", False))
