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
