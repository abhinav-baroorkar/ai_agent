"""Request traces written as one JSON line per request.

Usage:
    trace = writer.start(input_mode="text")
    with trace.stage("llm", model="qwen3:8b") as meta:
        ...
        meta["tokens"] = 42
    trace.finish(success=True)
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sasha.telemetry.resources import ResourceSnapshot, snapshot_resources

SCHEMA_VERSION = 1


@dataclass
class StageRecord:
    name: str
    latency_ms: float
    ok: bool
    meta: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class RequestTrace:
    assistant: str
    profile: str
    input_mode: str
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    language: str | None = None
    models: dict[str, str] = field(default_factory=dict)
    stages: list[StageRecord] = field(default_factory=list)
    text: dict[str, str] = field(default_factory=dict)
    _t0: float = field(default_factory=time.perf_counter, repr=False)
    _on_finish: Callable[[dict[str, Any]], None] | None = field(default=None, repr=False)
    _log_text: bool = field(default=False, repr=False)
    _finished: bool = field(default=False, repr=False)

    @contextmanager
    def stage(self, name: str, **meta: Any) -> Iterator[dict[str, Any]]:
        """Time a stage. Exceptions are recorded and re-raised."""
        record_meta: dict[str, Any] = dict(meta)
        t0 = time.perf_counter()
        try:
            yield record_meta
        except Exception as exc:
            self._add(name, t0, ok=False, meta=record_meta, error=f"{type(exc).__name__}: {exc}")
            raise
        self._add(name, t0, ok=True, meta=record_meta)

    def _add(
        self, name: str, t0: float, ok: bool, meta: dict[str, Any], error: str | None = None
    ) -> None:
        latency = round((time.perf_counter() - t0) * 1000, 2)
        self.stages.append(StageRecord(name, latency, ok, meta, error))

    def record_text(self, key: str, value: str) -> None:
        """Store user/assistant text only if the profile allows it."""
        if self._log_text:
            self.text[key] = value

    def to_dict(
        self, success: bool, error: str | None, resources: ResourceSnapshot | None
    ) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "request_id": self.request_id,
            "started_at": self.started_at,
            "assistant": self.assistant,
            "profile": self.profile,
            "input_mode": self.input_mode,
            "language": self.language,
            "models": self.models,
            "stages": [asdict(s) for s in self.stages],
            "total_ms": round((time.perf_counter() - self._t0) * 1000, 2),
            "resources": resources.to_dict() if resources else None,
            "success": success,
            "error": error,
        }
        if self.text:
            out["text"] = self.text
        return out

    def finish(self, success: bool, error: str | None = None) -> dict[str, Any]:
        if self._finished:
            raise RuntimeError("trace already finished")
        self._finished = True
        record = self.to_dict(success, error, snapshot_resources())
        if self._on_finish:
            self._on_finish(record)
        return record


class TelemetryWriter:
    """Appends finished traces to `<dir>/YYYY-MM-DD.jsonl` (UTC date)."""

    def __init__(
        self,
        directory: Path,
        assistant: str,
        profile: str,
        enabled: bool = True,
        log_text: bool = False,
    ) -> None:
        self.directory = directory
        self.assistant = assistant
        self.profile = profile
        self.enabled = enabled
        self.log_text = log_text

    def start(self, input_mode: str = "text") -> RequestTrace:
        return RequestTrace(
            assistant=self.assistant,
            profile=self.profile,
            input_mode=input_mode,
            _on_finish=self.write if self.enabled else None,
            _log_text=self.log_text,
        )

    def path_for_today(self) -> Path:
        return self.directory / f"{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"

    def write(self, record: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.path_for_today().open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
