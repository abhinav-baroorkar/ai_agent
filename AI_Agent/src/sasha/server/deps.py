"""Everything the server needs, built once at startup. Tests build their own with fakes."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sasha.bots import Bot, BotStore
from sasha.config import Settings, reload_secrets
from sasha.connectors import ConnectorRegistry, HomeAssistantConnector, build_connectors
from sasha.orchestrator import Assistant
from sasha.pipeline.llm import LLM, OllamaLLM
from sasha.pipeline.stt import SpeechToText
from sasha.pipeline.tts import TextToSpeech
from sasha.server.speaking import ListenerPresence
from sasha.server.voice import load_voice_stages
from sasha.server.wake_policy import RateLimiter
from sasha.server.warmup import (
    LLMPlacement,
    PlacementTelemetry,
    gpu_share,
    gpu_share_warning,
    share_label,
    warm_up_llm,
)
from sasha.state import JsonStore
from sasha.telemetry import TelemetryWriter

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

    def __post_init__(self) -> None:
        if self.wake_limiter is None:
            self.wake_limiter = RateLimiter(self.settings.profile.wake.max_calls_per_minute)

    def make_assistant(self, bot: Bot) -> tuple[Assistant, list[str]]:
        """A fresh conversation for `bot`, with only its connectors' tools."""
        tools, warnings = self.connectors.tools_for(bot.connectors)
        assistant = Assistant(
            self.settings,
            self.make_llm(bot.model),
            tools,
            self.telemetry,
            system_prompt=self.bots.prompt_for(bot),
        )
        return assistant, warnings


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
                llm.keep_alive_override = presence.keep_alive
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
        main_connectors=[HomeAssistantConnector.id],
        installed_models=lambda: [m["name"] for m in list_models()],
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
    )
