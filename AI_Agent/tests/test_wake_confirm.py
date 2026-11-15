"""Wake turns end to end: the voice bot, device opt-in, spoken confirmation, keep-alive."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sasha.config import LLMConfig, Settings
from sasha.pipeline.llm import OllamaLLM, ToolCall
from sasha.server.speaking import ListenerPresence
from tests.server_fakes import WS, resp
from tests.wake_fakes import (
    frames,
    listen,
    make,
    segment,
    telemetry_lines,
    turn_frames,
    with_devices,
)

LISTENER = f"{WS}/ws?wake=1"
OFF_ALL = ToolCall("call_service", {"domain": "light", "service": "turn_off", "entity_id": "all"})


@pytest.fixture
def name(settings: Settings) -> str:
    return settings.assistant.wake_word.phrase


def _tools_offered(h: Any) -> list[str]:
    for _messages, offered in h.llm("big-model").seen:
        assert offered is not None
    return []


def test_voice_bot_api(settings: Settings, tmp_path: Path) -> None:
    h, _ = make(settings, tmp_path)
    client = h.client()
    voice = next(b for b in client.get("/api/bots").json()["bots"] if b["id"] == "voice")
    assert voice["builtin"] is True and voice["wake_device_control"] is False
    assert voice["name"] == f"{h.settings.assistant.name} (wake word)"
    assert voice["connectors"] == ["fake"]  # defaults to main's
    r = client.patch("/api/bots/voice", json={"wake_device_control": True})
    assert r.status_code == 200 and r.json()["wake_device_control"] is True
    assert client.patch("/api/bots/main", json={"wake_device_control": True}).status_code == 400
    r = client.post("/api/bots", json={"name": "X", "wake_device_control": True})
    assert r.status_code == 400
    assert client.delete("/api/bots/voice").status_code == 400
    assert client.patch("/api/bots/voice", json={"name": "Other"}).status_code == 400


def test_read_only_by_default_through_the_socket(
    settings: Settings, tmp_path: Path, name: str
) -> None:
    h, stt = make(settings, tmp_path)
    dev = with_devices(h, control=False)
    stt.say(f"{name}, turn off the kitchen light")
    single = ToolCall(
        "call_service", {"domain": "light", "service": "turn_off", "entity_id": "light.k"}
    )
    h.llm("big-model").responses = [resp("", [single]), resp("I can't do that by voice")]
    with h.client().websocket_connect(LISTENER) as ws:
        listen(ws)
        segment(ws)
        got = turn_frames(ws)
    assert {"type": "tool", "name": "call_service", "ok": False} in got
    assert dev.calls == []
    decisions = [r["decision"] for r in telemetry_lines(h) if r.get("kind") == "wake_tool"]
    assert decisions == ["refused_read_only"]


def _confirm_turn(settings: Settings, tmp_path: Path, name: str, answer: str | None, **kw: Any):
    h, stt = make(settings, tmp_path, **kw)
    dev = with_devices(h)
    stt.say(f"{name}, turn off all the lights")
    if answer is not None:
        stt.say(answer)
    h.llm("big-model").responses = [resp("", [OFF_ALL]), resp("Done")]
    with h.client().websocket_connect(LISTENER) as ws:
        listen(ws)
        segment(ws)
        asked = frames(ws, "confirm")
        if answer is not None:
            segment(ws)
        rest = turn_frames(ws)
    return h, dev, asked, rest


def test_confirm_yes_runs_the_change(settings: Settings, tmp_path: Path, name: str) -> None:
    h, dev, asked, rest = _confirm_turn(settings, tmp_path, name, "Yes, do it")
    assert asked[-1] == {
        "type": "confirm",
        "action": "turn off all light devices",
        "timeout_s": 5.0,
    }
    assert dev.calls == [{"domain": "light", "service": "turn_off", "entity_id": "all"}]
    assert rest[0] == {"type": "confirm_result", "accepted": True, "reason": "yes"}
    assert {"type": "tool", "name": "call_service", "ok": True} in rest
    assert not any(
        f.get("type") == "wake" for f in rest if isinstance(f, dict)
    )  # answer isn't a wake
    decisions = [r["decision"] for r in telemetry_lines(h) if r.get("kind") == "wake_tool"]
    assert decisions == ["confirm_asked", "confirm_yes", "allowed"]


@pytest.mark.parametrize(
    ("answer", "reason"),
    [("no", "no"), ("Nope.", "no"), ("turn on the fan instead", "unclear"), ("", "unclear")],
)
def test_confirm_anything_but_yes_refuses(
    settings: Settings, tmp_path: Path, name: str, answer: str, reason: str
) -> None:
    _, dev, _, rest = _confirm_turn(settings, tmp_path, name, answer)
    assert dev.calls == []
    assert rest[0] == {"type": "confirm_result", "accepted": False, "reason": reason}
    assert {"type": "tool", "name": "call_service", "ok": False} in rest


def test_confirm_timeout_refuses(settings: Settings, tmp_path: Path, name: str) -> None:
    h, dev, _, rest = _confirm_turn(settings, tmp_path, name, None, confirm_window_s=0.2)
    assert dev.calls == []
    assert rest[0] == {"type": "confirm_result", "accepted": False, "reason": "timeout"}
    decisions = [r["decision"] for r in telemetry_lines(h) if r.get("kind") == "wake_tool"]
    assert decisions == ["confirm_asked", "confirm_timeout", "confirm_no"]


def test_c1_four_single_calls_ask_before_the_fourth(
    settings: Settings, tmp_path: Path, name: str
) -> None:
    h, stt = make(settings, tmp_path)
    dev = with_devices(h)
    stt.say(f"{name}, lights off in every room", "yes")
    calls = [
        ToolCall(
            "call_service", {"domain": "light", "service": "turn_off", "entity_id": f"light.{r}"}
        )
        for r in ("a", "b", "c", "d")
    ]
    h.llm("big-model").responses = [resp("", calls), resp("All off")]
    with h.client().websocket_connect(LISTENER) as ws:
        listen(ws)
        segment(ws)
        asked = frames(ws, "confirm")
        assert len(dev.calls) == 3  # held before the 4th
        segment(ws)
        turn_frames(ws)
    assert asked[-1]["action"] == "turn off d"
    assert len(dev.calls) == 4


def test_ordinary_sockets_are_not_limited(settings: Settings, tmp_path: Path) -> None:
    """The policy is the wake path's: a typed chat with the same bot is unchanged."""
    h, _ = make(settings, tmp_path)
    dev = with_devices(h, control=False)
    h.llm("big-model").responses = [resp("", [OFF_ALL]), resp("Done")]
    with h.client().websocket_connect(f"{WS}/ws?bot=voice") as ws:
        assert ws.receive_json()["state"] == "idle"
        ws.send_json({"type": "text", "text": "turn off all the lights"})
        turn_frames(ws)
    assert len(dev.calls) == 1


def test_keep_alive_override_only_while_listening() -> None:
    presence = ListenerPresence(-1)
    llm = OllamaLLM(LLMConfig(model="m", keep_alive="30m"))
    llm.keep_alive_override = presence.keep_alive
    assert llm.build_payload([])["keep_alive"] == "30m"
    presence.set(1, True)
    assert llm.build_payload([])["keep_alive"] == -1
    presence.set(1, False)
    assert llm.build_payload([])["keep_alive"] == "30m"  # normal value again on the next request


def test_keep_alive_restored_when_the_last_listener_goes() -> None:
    """S4: Ollama keeps the last keep_alive it was sent, so going idle resends normal."""
    presence = ListenerPresence(-1)
    restored: list[int] = []
    presence.on_idle = lambda: restored.append(1)
    presence.set(1, True)
    presence.set(2, True)
    presence.set(1, False)
    assert restored == []  # one listener still on
    presence.set(2, False)
    assert restored == [1]
    presence.set(2, False)  # already idle: not sent again
    assert restored == [1]

    def boom() -> None:
        raise RuntimeError("ollama down")

    presence.on_idle = boom
    presence.set(3, True)
    presence.set(3, False)  # never raises into the socket


def test_restore_keep_alive_sends_a_no_prompt_generate(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from sasha.server import deps as deps_module

    sent: list[tuple[str, str]] = []
    done = threading.Event()

    def fake_warm_up(cfg: Any, host: str, model: str, client: Any = None) -> str | None:
        sent.append((model, str(cfg.keep_alive)))
        done.set()
        return None

    monkeypatch.setattr(deps_module, "warm_up_llm", fake_warm_up)
    monkeypatch.setattr(deps_module, "load_voice_stages", lambda s: (None, None, []))
    monkeypatch.setattr(deps_module, "gpu_share", lambda host, model: None)
    paths = settings.paths.model_copy(update={"root": tmp_path, "data_dir": tmp_path / "Data"})
    deps = deps_module.build_deps(settings.model_copy(update={"paths": paths}), reload_env=False)
    sent.clear()  # the startup warm-up
    done.clear()
    deps.presence.set(7, True)
    main_model = settings.profile.llm.model
    for model in (main_model, "other-bot-model"):  # two bots' models got the override
        llm = deps.make_llm(model)
        assert llm.build_payload([])["keep_alive"] == -1  # type: ignore[attr-defined]
    deps.presence.set(7, False)
    for _ in range(50):
        if len(sent) == 2:
            break
        done.wait(0.1)
    normal = str(settings.profile.llm.keep_alive)
    assert sorted(sent) == sorted([(main_model, normal), ("other-bot-model", normal)])
    sent.clear()
    deps.presence.set(8, True)
    deps.presence.set(8, False)  # no model was sent the override this time: nothing to restore
    done.wait(0.3)
    assert sent == []


def test_listener_connect_rechecks_placement(settings: Settings, tmp_path: Path) -> None:
    h, _ = make(settings, tmp_path)
    checks: list[int] = []
    h.deps.recheck_placement = lambda: checks.append(1)
    with h.client().websocket_connect(LISTENER) as ws:
        listen(ws)
        ws.send_json({"type": "listener_stats", "gate_drops": 0})
    assert checks == [1]
