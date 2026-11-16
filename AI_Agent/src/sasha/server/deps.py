"""Everything the server needs, built once at startup. Tests build their own with fakes."""

from __future__ import annotations

import functools
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sasha.bots import MAIN_ID, Bot, BotStore
from sasha.browser.helper import BROWSE_TOOL, BrowseHelper, browse_tool
from sasha.browser.policy import BROWSER
from sasha.config import Settings, reload_secrets
from sasha.connectors import (
    ConnectorError,
    ConnectorRegistry,
    HomeAssistantConnector,
    build_connectors,
)
from sasha.connectors.browser import BrowserConnector
from sasha.conversation import building_for_bot, current_turn
from sasha.orchestrator import Assistant
from sasha.pipeline.llm import LLM, OllamaLLM
from sasha.pipeline.stt import SpeechToText
from sasha.pipeline.tts import TextToSpeech
from sasha.prompting import build_prompt, prompt_lines
from sasha.said import SaidGuard
from sasha.server.speaking import ListenerPresence
from sasha.server.timers.service import TimerService
from sasha.server.timers.tools import add_timer_tools
from sasha.server.voice import load_voice_stages
from sasha.server.wake_policy import RateLimiter
from sasha.server.wake_truth import wake_control_allowed
from sasha.server.warmup import (
    LLMPlacement,
    PlacementTelemetry,
    gpu_share,
    gpu_share_warning,
    share_label,
    warm_up_llm,
)
from sasha.server.window_token import WindowToken
from sasha.state import JsonStore
from sasha.telemetry import TelemetryWriter
from sasha.tools.clock import CLOCK_TOOL, clock_tool

log = logging.getLogger(__name__)


@dataclass
class ServerDeps:
    settings: Settings
    bots: BotStore
    connectors: ConnectorRegistry
    make_llm: Callable[[str], LLM]  # model name -> LLM
    list_models: Callable[[], list[dict[str, Any]]]  # [{name, size_gb}]
    telemetry: TelemetryWriter
    stt: SpeechToText | None = None
    tts: TextToSpeech | None = None
    warnings: list[str] = field(default_factory=list)
    # Wake word: is a listener on (keep_alive), the brain-wide device-change budget,
    # and a GPU-placement re-check to run after resume or an Ollama reload.
    presence: ListenerPresence = field(default_factory=ListenerPresence)
    wake_limiter: RateLimiter | None = None
    recheck_placement: Callable[[], None] = lambda: None
    window_token: WindowToken = field(default_factory=WindowToken)  # per launch (9.1, C4)
    timers: TimerService | None = None  # None: no timer tools, routes or frames

    def __post_init__(self) -> None:
        if self.wake_limiter is None:
            self.wake_limiter = RateLimiter(self.settings.profile.wake.max_calls_per_minute)

    def make_assistant(self, bot: Bot, spoken: bool = False) -> tuple[Assistant, list[str]]:
        """A fresh conversation for `bot`, with its connectors' tools, the clock, the
        timer tools and (the main assistant, browser on) web lookups. Its prompt says what
        those tools can really do; `spoken` adds the spoken style."""
        llm = self.make_llm(bot.model)
        token = building_for_bot.set(bot.id)  # the browser notes whose conversation it is
        try:
            tools, warnings = self.connectors.tools_for(bot.connectors)
            if (helper := self._browse_helper(bot, llm)) is not None:
                tools.register(browse_tool(helper))
        finally:
            building_for_bot.reset(token)
        if CLOCK_TOOL not in tools:  # every bot: read-only, so no guessed times
            # The timers' clock and zone, so "now" and a resolved alarm time agree.
            tools.register(clock_tool(self.timers.now_local) if self.timers else clock_tool())
        if self.timers is not None and not self.has_browser(bot):  # D3: never with a browser
            # Before the prompt, so the generated abilities list the timer tools too.
            tools = add_timer_tools(tools, self.timers, said=current_turn.get)
        lister = tools.get("list_devices").handler if "list_devices" in tools else None
        assistant = Assistant(
            self.settings,
            llm,
            tools,
            self.telemetry,
            system_prompt=self.prompt_for(bot, tools.names, spoken),
            # P1: changes after a web lookup; negations in the bot's language (G1)
            change_guard=SaidGuard(lister, tuple(prompt_lines(self.settings).negations)),
        )
        return assistant, warnings

    def _browse_source(self, bot: Bot) -> tuple[BrowserConnector, dict[str, Any]] | None:
        """The browser connector and its settings if `bot` gets web lookups: the main
        assistant while the connector is on and set up (design 10.1). A bot with the
        direct browser has the browser only (B4), so no helper."""
        kinds = self.connectors.kinds
        if bot.id != MAIN_ID or any(kinds.get(c) == "browser" for c in bot.connectors):
            return None
        browser = next((c for c, k in kinds.items() if k == "browser"), None)
        settings = self.connectors.ready_settings(browser) if browser else None
        if browser is None or settings is None:
            return None
        connector = self.connectors.get(browser)
        if not isinstance(connector, BrowserConnector):
            return None
        return connector, settings

    def connector_view(self, view: dict[str, Any]) -> dict[str, Any]:
        """The API's Connector, plus `browse` in the browser connector's `tools` while
        the main assistant is offered web lookups (contract v0.5.1)."""
        if view.get("kind") != "browser":
            return view
        try:
            offered = self._browse_source(self.bots.get(MAIN_ID)) is not None
        except Exception:  # never break the connectors list
            offered = False
        if offered and BROWSE_TOOL not in view["tools"]:
            return {**view, "tools": [*view["tools"], BROWSE_TOOL]}
        return view

    def _browse_helper(self, bot: Bot, llm: LLM) -> BrowseHelper | None:
        if (source := self._browse_source(bot)) is None:
            return None
        connector, settings = source
        conv = connector.conversation(settings, helper=True)  # one per conversation (P6)
        lines = prompt_lines(self.settings)
        return BrowseHelper(conv, self.settings, llm, self.telemetry, lines)

    def prompt_for(self, bot: Bot, tool_names: list[str], spoken: bool = False) -> str:
        """The bot's own prompt plus the facts about these tools (prompting.py)."""
        lines = prompt_lines(self.settings)
        return build_prompt(self.bots.prompt_for(bot), tool_names, lines, spoken)

    def has_browser(self, bot: Bot) -> bool:
        """D3: a bot with any `browser` connector gets no timer tools (web pages could
        otherwise set things that speak in the house). The same test as the browser's
        own allowlist (browser/policy.py): its kind, or an id that looks like one."""
        for cid in bot.connectors:
            try:
                kind: str | None = self.connectors.get(cid).kind
            except ConnectorError:
                kind = None  # gone or unknown: judged by its id, like the allowlist
            if kind == BROWSER or cid.split(":", 1)[0] == BROWSER:
                return True
        return False


def make_timers(settings: Settings, telemetry: TelemetryWriter) -> TimerService:
    store = JsonStore(settings.paths.state_dir / "timers.json")
    return TimerService(settings.profile.timers, store, telemetry)


def make_telemetry(settings: Settings) -> PlacementTelemetry:
    return PlacementTelemetry(
        settings.paths.telemetry_dir,
        assistant=settings.assistant.name,
        profile=settings.profile.name,
        enabled=settings.profile.telemetry.enabled,
        log_text=settings.profile.telemetry.log_text,
    )


def build_deps(settings: Settings, reload_env: bool = True) -> ServerDeps:
    """Real dependencies: Ollama, connectors from config, stores under `Data/state/`."""
    root = settings.paths.root

    def secrets() -> Any:
        return reload_secrets(root) if reload_env else settings.secrets

    connectors = build_connectors(settings, secrets)
    llms: dict[str, OllamaLLM] = {}
    lock = threading.Lock()
    presence = ListenerPresence(settings.profile.wake.keep_alive_while_listening)

    def make_llm(model: str) -> LLM:
        with lock:  # one shared HTTP client per model; httpx.Client is thread-safe
            if model not in llms:
                cfg = settings.profile.llm
                llm = OllamaLLM(cfg, host=settings.secrets.ollama_host, model=model)
                llm.keep_alive_override = functools.partial(presence.keep_alive_for, model)
                llms[model] = llm
            return llms[model]

    def list_models() -> list[dict[str, Any]]:
        lister = make_llm(settings.profile.llm.model)
        assert isinstance(lister, OllamaLLM)
        return lister.list_model_info()

    bots = BotStore(
        JsonStore(settings.paths.state_dir / "bots.json"),
        main_name=settings.assistant.name,
        main_prompt=settings.assistant.render_system_prompt(),
        default_model=settings.profile.llm.model,
        connector_ids=lambda: connectors.ids,
        connector_kinds=lambda: connectors.kinds,
        main_connectors=[HomeAssistantConnector.id],
        installed_models=lambda: [m["name"] for m in list_models()],
        wake_control_allowed=lambda: wake_control_allowed(settings.profile.wake),
    )
    # LLM first, then the voice stages: otherwise Ollama may put part of the LLM on the
    # CPU because speech-to-text already holds GPU memory (see server/warmup.py).
    warnings: list[str] = []
    host, model = settings.secrets.ollama_host, bots.get("main").model
    if warning := warm_up_llm(settings.profile.llm, host, model):
        warnings.append(warning)
    stt, tts, voice_warnings = load_voice_stages(settings)
    warnings += voice_warnings
    placement = LLMPlacement(model=model, gpu_share=gpu_share(host, model))
    log.info("LLM %s on GPU: %s", model, share_label(placement.gpu_share))
    if warning := gpu_share_warning(model, placement.gpu_share):
        log.warning(warning)
        warnings.append(warning)
    telemetry = make_telemetry(settings)
    telemetry.placement = placement
    telemetry.voice_models = {
        name: stage.model_version for name, stage in (("stt", stt), ("tts", tts)) if stage
    }

    def restore_keep_alive() -> None:
        """Last listener gone: one no-prompt generate with the profile's keep_alive for
        every model that was sent the never-unload override, so Ollama stops keeping
        them forever. In a thread, off the event loop."""

        def restore() -> None:
            for overridden in presence.take_overridden():
                try:
                    if warning := warm_up_llm(settings.profile.llm, host, overridden):
                        log.warning("keep_alive restore: %s", warning)
                except Exception as exc:  # never raises
                    log.warning("keep_alive restore failed: %s", type(exc).__name__)

        threading.Thread(target=restore, name="keep-alive-restore", daemon=True).start()

    presence.on_idle = restore_keep_alive

    def recheck_placement() -> None:
        """Re-measure how much of the LLM is on the GPU (after resume or a reload)."""
        current = bots.get("main").model
        share = gpu_share(host, current)
        telemetry.placement = LLMPlacement(model=current, gpu_share=share)
        if warning := gpu_share_warning(current, share):
            log.warning(warning)

    return ServerDeps(
        settings=settings,
        bots=bots,
        connectors=connectors,
        make_llm=make_llm,
        list_models=list_models,
        telemetry=telemetry,
        stt=stt,
        tts=tts,
        warnings=warnings,
        presence=presence,
        recheck_placement=recheck_placement,
        timers=make_timers(settings, telemetry),
    )
