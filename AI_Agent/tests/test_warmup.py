"""LLM warm-up before the voice stages, and the GPU-share check."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from sasha.config import LLMConfig, Settings
from sasha.server import deps as deps_module
from sasha.server.warmup import (
    LLMPlacement,
    PlacementTelemetry,
    gpu_share,
    gpu_share_warning,
    warm_up_llm,
)

CFG = LLMConfig(model="m:9b", num_ctx=8192, keep_alive="24h")


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))


def test_warm_up_loads_with_profile_context_and_keep_alive() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"path": request.url.path, **json.loads(request.content)})
        return httpx.Response(200, json={"done": True})

    assert warm_up_llm(CFG, "http://ollama.test", "m:9b", client=_client(handler)) is None
    assert seen == [
        {
            "path": "/api/generate",
            "model": "m:9b",
            "stream": False,
            "options": {"num_ctx": 8192},
            "keep_alive": "24h",
        }
    ]


@pytest.mark.parametrize(
    ("handler_result", "expected"),
    [
        (httpx.ConnectError("refused"), "not reachable (ConnectError)"),
        (httpx.ReadTimeout("slow"), "did not load m:9b within"),
        (httpx.ConnectTimeout("silent"), "not reachable (no answer within 3 s)"),
        (httpx.Response(404, json={"error": "model not found"}), "HTTP 404"),
    ],
)
def test_warm_up_failures_are_plain_warnings(handler_result: Any, expected: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(handler_result, Exception):
            raise handler_result
        return handler_result  # type: ignore[no-any-return]

    warning = warm_up_llm(CFG, "http://ollama.test", "m:9b", client=_client(handler))
    assert warning is not None and expected in warning


def test_connect_timeout_stays_short_for_both_calls() -> None:
    """A per-request bare float would also set connect to 45 s / 5 s."""
    seen: dict[str, dict[str, Any]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = request.extensions["timeout"]
        return httpx.Response(200, json={"models": []})

    client = _client(handler)
    warm_up_llm(CFG, "http://ollama.test", "m:9b", client=client)
    gpu_share("http://ollama.test", "m:9b", client=client)
    assert seen["/api/generate"]["connect"] == 3.0
    assert seen["/api/generate"]["read"] == 45.0
    assert seen["/api/ps"]["connect"] == 3.0
    assert seen["/api/ps"]["read"] == 5.0


def _ps(models: Any) -> httpx.Client:
    return _client(lambda r: httpx.Response(200, json={"models": models}))


def test_gpu_share_from_process_list() -> None:
    full = [{"name": "m:9b", "model": "m:9b", "size": 1000, "size_vram": 1000}]
    split = [{"name": "other:1b", "size": 10, "size_vram": 10}, {**full[0], "size_vram": 830}]
    assert gpu_share("h", "m:9b", client=_ps(full)) == 1.0
    assert gpu_share("h", "m:9b", client=_ps(split)) == pytest.approx(0.83)
    latest = [{"name": "m:latest", "size": 4, "size_vram": 2}]
    assert gpu_share("h", "m", client=_ps(latest)) == 0.5
    assert gpu_share("h", "m:9b", client=_ps([])) is None
    assert gpu_share("h", "m:9b", client=_ps("garbage")) is None
    broken = _client(lambda r: httpx.Response(200, content=b"not json"))
    assert gpu_share("h", "m:9b", client=broken) is None


def test_gpu_share_warning_only_when_split() -> None:
    assert gpu_share_warning("m:9b", None) is None
    assert gpu_share_warning("m:9b", 1.0) is None
    warning = gpu_share_warning("m:9b", 0.83)
    assert warning is not None and "83% on the GPU (17% on the CPU)" in warning


def test_telemetry_records_startup_gpu_share(settings: Settings, tmp_path: Any) -> None:
    writer = PlacementTelemetry(tmp_path, "a", "p", enabled=False)
    assert "llm_gpu_share_at_start" not in writer.start().models
    writer.placement = LLMPlacement(model="m:9b", gpu_share=0.83)
    assert writer.start().models["llm_gpu_share_at_start"] == "m:9b:83%"


def test_telemetry_records_voice_models_on_voice_turns_only(tmp_path: Any) -> None:
    writer = PlacementTelemetry(tmp_path, "a", "p", enabled=False)
    writer.voice_models = {"stt": lambda: "whisper@x", "tts": lambda: "kokoro@y"}
    voice = writer.start(input_mode="voice").models
    assert voice["stt"] == "whisper@x" and voice["tts"] == "kokoro@y"
    assert "stt" not in writer.start(input_mode="text").models


def test_build_deps_warms_llm_before_voice_and_warns_on_split(
    settings: Settings, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    monkeypatch.setattr(
        deps_module, "warm_up_llm", lambda *a, **k: order.append("warm") or "warm-up failed"
    )

    def voice(s: Settings) -> tuple[None, None, list[str]]:
        order.append("voice")
        return None, None, ["voice off"]

    monkeypatch.setattr(deps_module, "load_voice_stages", voice)
    monkeypatch.setattr(deps_module, "gpu_share", lambda *a, **k: order.append("ps") or 0.5)
    paths = settings.paths.model_copy(update={"root": tmp_path, "data_dir": tmp_path / "Data"})
    deps = deps_module.build_deps(settings.model_copy(update={"paths": paths}))
    assert order == ["warm", "voice", "ps"]
    assert deps.warnings[:2] == ["warm-up failed", "voice off"]
    assert "50% on the GPU" in deps.warnings[2]
    assert deps.telemetry.start().models["llm_gpu_share_at_start"].endswith(":50%")


def test_build_deps_puts_voice_versions_in_voice_telemetry(
    settings: Settings, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sasha.pipeline.fakes import FakeSTT, FakeTTS

    monkeypatch.setattr(deps_module, "warm_up_llm", lambda *a, **k: None)
    monkeypatch.setattr(deps_module, "gpu_share", lambda *a, **k: 1.0)
    monkeypatch.setattr(deps_module, "load_voice_stages", lambda s: (FakeSTT(), FakeTTS(), []))
    paths = settings.paths.model_copy(update={"root": tmp_path, "data_dir": tmp_path / "Data"})
    deps = deps_module.build_deps(settings.model_copy(update={"paths": paths}))
    models = deps.telemetry.start(input_mode="voice").models
    assert (models["stt"], models["tts"]) == ("fake-stt", "fake-tts")
    assert deps.warnings == []


class _ChangingSTT:
    """A stage whose version changes between turns, like a GPU -> CPU fallback."""

    def __init__(self) -> None:
        self.used = "cuda:float16"

    def model_version(self) -> str:
        return f"whisper@x:{self.used}"


def test_voice_telemetry_reads_the_version_at_each_turn(tmp_path: Any) -> None:
    stt = _ChangingSTT()
    writer = PlacementTelemetry(tmp_path, "a", "p", enabled=False)
    writer.voice_models = {"stt": stt.model_version}
    assert writer.start(input_mode="voice").models["stt"] == "whisper@x:cuda:float16"
    stt.used = "cpu:int8:fallback"
    assert writer.start(input_mode="voice").models["stt"] == "whisper@x:cpu:int8:fallback"
    stt.used = "cuda:float16"
    assert writer.start(input_mode="voice").models["stt"] == "whisper@x:cuda:float16"


def test_a_failing_version_call_never_breaks_a_turn(tmp_path: Any) -> None:
    def broken() -> str:
        raise RuntimeError("boom")

    writer = PlacementTelemetry(tmp_path, "a", "p", enabled=False)
    writer.voice_models = {"stt": broken}
    assert writer.start(input_mode="voice").models["stt"] == "unknown"
