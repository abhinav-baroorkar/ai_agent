"""Helpers for the listener-socket tests: scripted STT, a device connector, a harness."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from starlette.testclient import WebSocketTestSession

from sasha.config import Settings
from sasha.connectors import SetupCheck, TestResult
from sasha.pipeline.stt import Transcript
from sasha.tools import Tool
from tests.server_fakes import FakeTTS, Harness

SEGMENT = b"\x01\x00" * 3200  # 0.2 s of 16 kHz audio
SENTINEL = "zq-sentinel-household-talk-7731"  # must never leave the wake path
# The fake house's exposed devices, with their real names (what list_devices returns).
HOUSE = [
    {"entity_id": "light.kitchen", "name": "Kitchen light", "state": "on"},
    {"entity_id": "light.living_room", "name": "Living room light", "state": "on"},
    {"entity_id": "light.bedroom", "name": "Bedroom light", "state": "on"},
    {"entity_id": "light.hallway", "name": "Hallway light", "state": "on"},
]


class ScriptedSTT:
    """Returns queued transcripts in order; counts calls."""

    def __init__(self) -> None:
        self.queue: list[tuple[str, float]] = []
        self.calls = 0

    def say(self, *texts: str, confidence: float = 0.9) -> None:
        self.queue += [(t, confidence) for t in texts]

    def model_version(self) -> str:
        return "stt@scripted"

    def transcribe(self, audio: bytes, sample_rate: int) -> Transcript:
        self.calls += 1
        text, confidence = self.queue.pop(0) if self.queue else ("", 0.0)
        return Transcript(text, "en", confidence)


class DeviceConnector:
    """A Home-Assistant-like connector with the three device tools."""

    kind = "fake_devices"

    def __init__(self) -> None:
        self.id = "devices"
        self.name = "Devices"
        self.secret_env: list[str] = []
        self.calls: list[dict[str, Any]] = []

    def default_enabled(self) -> bool:
        return True

    def default_settings(self) -> dict[str, Any]:
        return {}

    def validate_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        return {}

    def setup(self) -> SetupCheck:
        return SetupCheck(True, "ready")

    def tool_names(self, settings: dict[str, Any]) -> list[str]:
        return ["list_devices", "get_state", "call_service"]

    def build_tools(self, settings: dict[str, Any]) -> list[Tool]:
        obj: dict[str, Any] = {"type": "object", "properties": {}}
        params = {
            "type": "object",
            "properties": {k: {"type": "string"} for k in ("domain", "service", "entity_id")},
            "required": ["domain", "service", "entity_id"],
        }
        params["properties"]["data"] = {"type": "object"}

        def call_service(domain: str, service: str, entity_id: Any, data: Any = None) -> Any:
            self.calls.append({"domain": domain, "service": service, "entity_id": entity_id})
            return {"ok": True}

        return [
            Tool("list_devices", "list", obj, lambda: list(HOUSE)),
            Tool("get_state", "state", obj, lambda **_: {}),
            Tool("call_service", "call", params, call_service),
        ]

    def test(self, settings: dict[str, Any]) -> TestResult:
        return TestResult(True, "ok")


def wake_settings(settings: Settings, log_text: bool = False, **wake: Any) -> Settings:
    profile = settings.profile
    wake_cfg = profile.wake.model_copy(update={"cooldown_s": 0.0, **wake})
    telemetry = profile.telemetry.model_copy(update={"log_text": log_text})
    profile = profile.model_copy(update={"wake": wake_cfg, "telemetry": telemetry})
    return settings.model_copy(update={"profile": profile})


def make(settings: Settings, tmp: Path, tts: Any = None, **kw: Any) -> tuple[Harness, ScriptedSTT]:
    stt = ScriptedSTT()
    log_text = bool(kw.pop("log_text", False))
    h = Harness(wake_settings(settings, log_text=log_text, **kw), tmp, stt=stt, tts=tts)
    return h, stt


def with_devices(h: Harness, control: bool = True) -> DeviceConnector:
    dev = DeviceConnector()
    h.connectors.register(dev)
    h.bots.update("voice", _fields({"connectors": ["devices"], "wake_device_control": control}))
    return dev


def _fields(raw: dict[str, Any]) -> Any:
    from sasha.bots import parse_fields

    return parse_fields(raw)


def listen(ws: WebSocketTestSession, paused: bool = False) -> None:
    assert ws.receive_json() == {"type": "state", "state": "idle"}
    ws.send_json({"type": "listener_state", "paused": paused})


def segment(ws: WebSocketTestSession, audio: bytes = SEGMENT) -> None:
    ws.send_json({"type": "audio_start", "sample_rate": 16000, "wake": True})
    ws.send_bytes(audio)
    ws.send_json({"type": "audio_stop"})


def frames(ws: WebSocketTestSession, until: str, state: str | None = None) -> list[Any]:
    """Collect frames until one of type `until` (and, for `state`, that state)."""
    out: list[Any] = []
    while True:
        msg = ws.receive()
        frame: Any = msg["bytes"] if msg.get("bytes") is not None else json.loads(msg["text"])
        out.append(frame)
        is_match = isinstance(frame, dict) and frame.get("type") == until
        if is_match and (state is None or frame.get("state") == state):
            return out


def turn_frames(ws: WebSocketTestSession) -> list[Any]:
    """Frames of a full turn, up to its final idle state."""
    return frames(ws, "state", "idle")


def text_of(items: list[Any]) -> str:
    return "\n".join(json.dumps(f) for f in items if isinstance(f, dict))


def telemetry_lines(h: Harness) -> Iterator[dict[str, Any]]:
    for path in sorted(h.settings.paths.telemetry_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            yield json.loads(line)


__all__ = ["FakeTTS"]
