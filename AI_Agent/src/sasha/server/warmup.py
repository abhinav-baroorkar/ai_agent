"""Load the LLM before the voice stages, and check that it really sits on the GPU.

Why the order matters: the GPU on a dev laptop is shared. If speech-to-text takes its
~0.8 GB first, Ollama sees too little free memory for the LLM at the profile's
`num_ctx` and quietly puts part of the model on the CPU. On the 8 GB laptop that
measured 36 tokens/s instead of 58. So the server asks Ollama to load the default
bot's model (same `num_ctx` and `keep_alive` as real chats, so the first chat doesn't
reload it) before it builds the voice stages, then reads Ollama's process list once
and warns if the model is not fully on the GPU.

Known limit: when `keep_alive` expires, Ollama unloads the model, and the next chat
reloads it while the voice stages already hold their GPU memory, so it can split
again. There is no re-warm loop; the profile sets a long `keep_alive` instead.

Nothing here raises or blocks startup for long: every failure becomes a plain warning.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from sasha.config import LLMConfig
from sasha.telemetry import RequestTrace, TelemetryWriter

log = logging.getLogger(__name__)

WARMUP_TIMEOUT_S = 45.0  # a cold 9B load from disk takes ~8 s on the laptop
PS_TIMEOUT_S = 5.0
CONNECT_TIMEOUT_S = 3.0  # an Ollama address that doesn't answer fails fast
FULL_GPU = 0.995  # rounding in Ollama's byte counts


def _timeout(seconds: float) -> httpx.Timeout:
    """`seconds` for reading the answer, but only CONNECT_TIMEOUT_S to connect. (A bare
    float passed per request would apply to every phase, connect included.)"""
    return httpx.Timeout(seconds, connect=CONNECT_TIMEOUT_S)


def _client(host: str, client: httpx.Client | None, seconds: float) -> httpx.Client:
    return client or httpx.Client(base_url=host, timeout=_timeout(seconds))


def warm_up_llm(
    config: LLMConfig, host: str, model: str, client: httpx.Client | None = None
) -> str | None:
    """Ask Ollama to load `model` now. Returns a warning, or None when it loaded."""
    payload: dict[str, Any] = {
        "model": model,  # no prompt: Ollama just loads the model and answers at once
        "stream": False,
        "options": {"num_ctx": config.num_ctx},
    }
    if config.keep_alive:
        payload["keep_alive"] = config.keep_alive
    http = _client(host, client, WARMUP_TIMEOUT_S)
    try:
        resp = http.post("/api/generate", json=payload, timeout=_timeout(WARMUP_TIMEOUT_S))
        resp.raise_for_status()
    except httpx.ConnectTimeout:  # before TimeoutException: it is a subclass
        return f"LLM warm-up: Ollama not reachable (no answer within {CONNECT_TIMEOUT_S:.0f} s)"
    except httpx.TimeoutException:
        return f"LLM warm-up: Ollama did not load {model} within {WARMUP_TIMEOUT_S:.0f} s"
    except httpx.HTTPStatusError as e:
        return f"LLM warm-up: Ollama answered HTTP {e.response.status_code} for {model}"
    except httpx.HTTPError as e:
        return f"LLM warm-up: Ollama not reachable ({type(e).__name__})"
    finally:
        if client is None:
            http.close()
    return None


def gpu_share(host: str, model: str, client: httpx.Client | None = None) -> float | None:
    """Fraction of `model` in GPU memory per Ollama's process list; None if unknown."""
    http = _client(host, client, PS_TIMEOUT_S)
    try:
        resp = http.get("/api/ps", timeout=_timeout(PS_TIMEOUT_S))
        resp.raise_for_status()
        running = resp.json().get("models", [])
    except (httpx.HTTPError, ValueError, AttributeError):
        return None
    finally:
        if client is None:
            http.close()
    for entry in running if isinstance(running, list) else []:
        names = {entry.get("name"), entry.get("model")} if isinstance(entry, dict) else set()
        if not names & {model, f"{model}:latest"}:
            continue
        size, vram = entry.get("size"), entry.get("size_vram")
        if isinstance(size, int) and isinstance(vram, int) and size > 0:
            return min(1.0, vram / size)
    return None


def gpu_share_warning(model: str, share: float | None) -> str | None:
    if share is None or share >= FULL_GPU:
        return None
    on_gpu = round(share * 100)
    return (
        f"LLM {model} is only {on_gpu}% on the GPU ({100 - on_gpu}% on the CPU), so replies "
        "are much slower (58 vs 36 tokens/s measured on the laptop). Free GPU memory "
        "(for example run speech-to-text on the CPU or lower num_ctx) and restart the server."
    )


def share_label(share: float | None) -> str:
    return "unknown" if share is None else f"{round(share * 100)}%"


@dataclass
class LLMPlacement:
    """What startup measured, for logs and telemetry."""

    model: str
    gpu_share: float | None = None


class PlacementTelemetry(TelemetryWriter):
    """A TelemetryWriter that adds server-wide model facts to each request's `models`:
    - `llm_gpu_share_at_start` on every request, so a CPU split shows up in research
      data. A startup snapshot: a later reload by Ollama is not re-measured.
    - `voice_models` (`stt`, `tts` model versions) on voice turns (`input_mode ==
      "voice"`). `tts` names the server's loaded stage; whether that socket spoke the
      reply (`speak=1`) is not recorded here."""

    placement: LLMPlacement | None = None
    voice_models: dict[str, str] | None = None

    def start(self, input_mode: str = "text") -> RequestTrace:
        trace = super().start(input_mode)
        if self.placement is not None:
            trace.models["llm_gpu_share_at_start"] = (
                f"{self.placement.model}:{share_label(self.placement.gpu_share)}"
            )
        if input_mode == "voice" and self.voice_models:
            trace.models.update(self.voice_models)
        return trace
