from __future__ import annotations

import json
from pathlib import Path

import pytest

from sasha.telemetry import TelemetryWriter, snapshot_resources


def _writer(tmp_path: Path, **kw: bool) -> TelemetryWriter:
    return TelemetryWriter(tmp_path, assistant="Test", profile="pc", **kw)


def test_trace_writes_one_json_line(tmp_path: Path) -> None:
    w = _writer(tmp_path)
    trace = w.start()
    trace.models["llm"] = "m@abc"
    with trace.stage("llm", round=0) as meta:
        meta["eval_count"] = 5
    record = trace.finish(success=True)

    lines = w.path_for_today().read_text().splitlines()
    assert len(lines) == 1
    saved = json.loads(lines[0])
    assert saved["request_id"] == record["request_id"]
    assert saved["success"] is True
    assert saved["models"] == {"llm": "m@abc"}
    assert saved["stages"][0]["name"] == "llm"
    assert saved["stages"][0]["meta"] == {"round": 0, "eval_count": 5}
    assert saved["stages"][0]["latency_ms"] >= 0
    assert saved["resources"]["ram_total_mb"] > 0
    assert "text" not in saved


def test_failed_stage_is_recorded_and_reraised(tmp_path: Path) -> None:
    trace = _writer(tmp_path).start()
    with pytest.raises(ValueError), trace.stage("tool:x"):
        raise ValueError("boom")
    record = trace.finish(success=False, error="boom")
    assert record["stages"][0]["ok"] is False
    assert "boom" in record["stages"][0]["error"]


def test_text_only_logged_when_enabled(tmp_path: Path) -> None:
    off = _writer(tmp_path / "a").start()
    off.record_text("user", "secret")
    assert "text" not in off.finish(success=True)

    on = _writer(tmp_path / "b", log_text=True).start()
    on.record_text("user", "hello")
    assert on.finish(success=True)["text"] == {"user": "hello"}


def test_disabled_writer_writes_nothing(tmp_path: Path) -> None:
    w = _writer(tmp_path, enabled=False)
    w.start().finish(success=True)
    assert not list(tmp_path.iterdir())


def test_finish_twice_raises(tmp_path: Path) -> None:
    trace = _writer(tmp_path).start()
    trace.finish(success=True)
    with pytest.raises(RuntimeError):
        trace.finish(success=True)


def test_resource_snapshot_without_gpu() -> None:
    snap = snapshot_resources(include_gpu=False)
    assert snap.vram_used_mb is None
    assert snap.process_rss_mb > 0
