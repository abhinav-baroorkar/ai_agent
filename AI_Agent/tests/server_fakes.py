"""Fakes for the server tests: LLM, connector, STT, TTS. No GPU, Ollama or network."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from sasha.bots import BotStore
from sasha.config import Settings
from sasha.connectors import ConnectorRegistry, SetupCheck, TestResult
from sasha.pipeline.llm import LLMCancelled, LLMResponse, Message, ToolCall
from sasha.pipeline.stt import Transcript
from sasha.pipeline.tts import SpeechAudio
from sasha.server.app import create_app
from sasha.server.deps import ServerDeps, make_telemetry
from sasha.server.wake_truth import wake_control_allowed
from sasha.state import JsonStore
from sasha.tools import Tool

BASE_URL = "http://127.0.0.1:8765"
OWN_ORIGIN = "http://127.0.0.1:8765"
WS = "ws://127.0.0.1:8765"  # the test client's default Host would be refused


def resp(content: str = "", calls: list[ToolCall] | None = None) -> LLMResponse:
    raw: dict[str, Any] = {"role": "assistant", "content": content}
    stats = {"eval_count": 10, "eval_ms": 100.0, "tokens_per_s": 100.0}
    return LLMResponse(content, calls or [], "fake", raw, stats)


class FakeLLM:
    """Streams queued responses word by word. `hold` makes it wait for a cancel."""

    def __init__(self, model: str, responses: list[LLMResponse] | None = None) -> None:
        self.model = model
        self.responses = responses if responses is not None else []
        self.seen: list[tuple[list[Message], bool]] = []
        self.offered: list[list[str]] = []  # tool names offered on each call
        self.hold = False
        self.started = threading.Event()

    @property
    def model_id(self) -> str:
        return self.model

    def model_version(self) -> str:
        return f"{self.model}@fake"

    def _next(self) -> LLMResponse:
        return self.responses.pop(0) if self.responses else resp("Hello there")

    def chat(
        self, messages: Sequence[Message], tools: Sequence[dict[str, Any]] | None = None
    ) -> LLMResponse:
        self.seen.append((list(messages), tools is not None))
        return self._next()

    def chat_stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]] | None,
        on_delta: Callable[[str], None],
        should_stop: Callable[[], bool] | None = None,
    ) -> LLMResponse:
        self.seen.append((list(messages), tools is not None))
        self.offered.append([t["function"]["name"] for t in tools or []])
        self.started.set()
        while self.hold:
            if should_stop and should_stop():
                raise LLMCancelled
            threading.Event().wait(0.01)
        out = self._next()
        for i, word in enumerate(out.content.split(" ")):
            on_delta(word if i == 0 else f" {word}")
        return out


class FakeConnector:
    kind = "fake"

    def __init__(self, cid: str = "fake", ready: bool = True) -> None:
        self.id = cid
        self.name = f"Fake {cid}"
        self.secret_env = ["FAKE_TOKEN"]
        self.ready = ready
        self.calls: list[str] = []

    def default_enabled(self) -> bool:
        return True

    def default_settings(self) -> dict[str, Any]:
        return {"greeting": "hi"}

    def validate_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        from sasha.connectors import ConnectorError

        if set(settings) - {"greeting"}:
            raise ConnectorError("Unknown setting")
        return {**self.default_settings(), **settings}

    def setup(self) -> SetupCheck:
        return SetupCheck(self.ready, "ready" if self.ready else "Set FAKE_TOKEN in .env")

    def tool_names(self, settings: dict[str, Any]) -> list[str]:
        return [f"{self.id}_echo"]

    def build_tools(self, settings: dict[str, Any]) -> list[Tool]:
        def echo(text: str) -> dict[str, Any]:
            self.calls.append(text)
            return {"echo": text, "greeting": settings["greeting"]}

        schema = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        }
        return [Tool(f"{self.id}_echo", "Echo text", schema, echo)]

    def test(self, settings: dict[str, Any]) -> TestResult:
        return TestResult(self.ready, "pong" if self.ready else "not set up")


class FakeSTT:
    def __init__(self, text: str = "turn on the light", language: str = "en") -> None:
        self.text = text
        self.language = language
        self.heard: list[bytes] = []

    def model_version(self) -> str:
        return "stt@fake"

    def transcribe(
        self, audio: bytes, sample_rate: int, *, hotwords: str | None = None
    ) -> Transcript:
        if sample_rate != 16000:
            raise ValueError("sample_rate must be 16000")
        self.heard.append(audio)
        return Transcript(self.text, self.language, 0.9)


class FakeTTS:
    sample_rate = 24000

    def __init__(self, languages: list[str] | None = None) -> None:
        self.spoken: list[tuple[str, str]] = []
        self.languages = ["en"] if languages is None else languages
        self.hold = threading.Event()  # set to make stream() wait until stop/release
        self.release = threading.Event()

    def model_version(self) -> str:
        return "tts@fake"

    def synthesize(self, text: str, language: str) -> SpeechAudio:
        return SpeechAudio(b"\x01\x00" * 480, self.sample_rate)

    def stream(self, text: str, language: str) -> Iterator[SpeechAudio]:
        self.spoken.append((text, language))
        for _ in range(2):  # two chunks of 30 ms each
            if self.hold.is_set():
                self.release.wait(5)
            yield SpeechAudio(b"\x01\x00" * 720, self.sample_rate)


class Harness:
    def __init__(self, settings: Settings, tmp: Path, stt: Any = None, tts: Any = None) -> None:
        paths = settings.paths.model_copy(update={"data_dir": tmp / "Data"})
        self.settings = settings.model_copy(update={"paths": paths})
        self.connector = FakeConnector()
        self.connectors = ConnectorRegistry(
            [self.connector, FakeConnector("other", ready=False)],
            JsonStore(paths.state_dir / "connectors.json"),
        )
        self.bots = BotStore(
            JsonStore(paths.state_dir / "bots.json"),
            main_name=self.settings.assistant.name,
            main_prompt=self.settings.assistant.render_system_prompt(),
            default_model="big-model",
            connector_ids=lambda: self.connectors.ids,
            connector_kinds=lambda: self.connectors.kinds,
            main_connectors=["fake"],
            installed_models=lambda: [str(m["name"]) for m in self.models],
            wake_control_allowed=lambda: wake_control_allowed(self.settings.profile.wake),
        )
        self.llms: dict[str, FakeLLM] = {}
        self.models: list[dict[str, Any]] = [{"name": "big-model", "size_gb": 6.6}]
        self.models += [{"name": n, "size_gb": 1.0} for n in ("other", "small:latest", "m2")]
        self.deps = ServerDeps(
            settings=self.settings,
            bots=self.bots,
            connectors=self.connectors,
            make_llm=self.llm,
            list_models=lambda: self.models,
            telemetry=make_telemetry(self.settings),
            stt=stt,
            tts=tts,
        )

    def llm(self, model: str) -> FakeLLM:
        return self.llms.setdefault(model, FakeLLM(model))

    def client(self, web_dir: Path | None = None) -> TestClient:
        return TestClient(create_app(self.deps, web_dir=web_dir), base_url=BASE_URL)
