"""Everything the server needs, built once at startup. Tests build their own with fakes."""

from __future__ import annotations

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
from sasha.server.voice import load_voice_stages
from sasha.state import JsonStore
from sasha.telemetry import TelemetryWriter


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


def make_telemetry(settings: Settings) -> TelemetryWriter:
    return TelemetryWriter(
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

    def make_llm(model: str) -> LLM:
        with lock:  # one shared HTTP client per model; httpx.Client is thread-safe
            if model not in llms:
                cfg = settings.profile.llm
                llms[model] = OllamaLLM(cfg, host=settings.secrets.ollama_host, model=model)
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
    stt, tts, warnings = load_voice_stages(settings)
    return ServerDeps(
        settings=settings,
        bots=bots,
        connectors=connectors,
        make_llm=make_llm,
        list_models=list_models,
        telemetry=make_telemetry(settings),
        stt=stt,
        tts=tts,
        warnings=warnings,
    )
