"""Timers in the brain server: routes (D5), socket frames, D3, wake turns (D4, truthful
replies) and the listener's stop window (D1). Fake clock, invented zone (ToyZone)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from sasha.config import Settings
from sasha.pipeline.llm import ToolCall
from sasha.server.timers.model import TimerItem
from sasha.server.timers.service import TimerService
from tests.server_fakes import WS, FakeConnector, FakeTTS, Harness, resp
from tests.timer_fakes import START, FakeClock, make_service
from tests.wake_fakes import _fields, frames, listen, make, segment, turn_frames

FOREIGN = {"Origin": "http://evil.example"}
JSON = {"Content-Type": "application/json"}


def with_timers(h: Harness, **config: Any) -> tuple[TimerService, FakeClock]:
    service, _, clock = make_service(
        h.settings.paths.state_dir, telemetry=h.deps.telemetry, **config
    )
    h.deps.timers = service  # the app builds its own hub around it
    return service, clock


def timer(service: TimerService, seconds: float, by: str = "chat", **kw: Any) -> TimerItem:
    return service.add(TimerItem("", "timer", service.clock() + seconds, created_by=by, **kw))


def types(items: list[Any]) -> list[str]:
    return [f["type"] for f in items if isinstance(f, dict)]


def first(items: list[Any], kind: str) -> dict[str, Any]:
    return next(f for f in items if isinstance(f, dict) and f["type"] == kind)


# --- routes (D5) ------------------------------------------------------------------------


def test_routes(settings: Settings, tmp_path: Path) -> None:
    h = Harness(settings, tmp_path)
    service, clock = with_timers(h)
    client = h.client()
    scheduled, waiting = timer(service, 600, label="tea"), timer(service, 5)
    clock.advance(5)
    service.tick()  # nothing connected: it waits as pending
    listed = client.get("/api/timers").json()
    assert listed["now"].endswith("+02:30") and len(listed["timers"]) == 2
    assert client.delete(f"/api/timers/{waiting.id}").status_code == 409  # use dismiss
    assert client.delete("/api/timers/nope").status_code == 404
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    assert (
        client.post(f"/api/timers/{waiting.id}/dismiss", content="a=1", headers=form).status_code
        == 415
    )
    r = client.post(f"/api/timers/{waiting.id}/dismiss")  # no body: how the Home card sends it
    assert r.status_code == 200 and r.json()["state"] == "done"
    assert client.delete(f"/api/timers/{scheduled.id}").json() == {"ok": True}
    assert service.items[scheduled.id].state == "done"
    assert client.post(f"/api/timers/{scheduled.id}/snooze", json={"minutes": 0}).status_code == 400
    assert client.post(f"/api/timers/{scheduled.id}/dismiss", content="{}").status_code == 415
    assert client.post(f"/api/timers/{scheduled.id}/say").status_code == 409  # nothing connected


@pytest.mark.parametrize(
    ("method", "path"),
    [("delete", ""), ("post", "/dismiss"), ("post", "/snooze"), ("post", "/say")],
)
def test_foreign_origin_is_refused(
    settings: Settings, tmp_path: Path, method: str, path: str
) -> None:
    h = Harness(settings, tmp_path)
    service, _ = with_timers(h)
    item = timer(service, 600)
    kwargs: dict[str, Any] = {"headers": {**FOREIGN, **JSON}}
    if method == "post":
        kwargs["content"] = '{"minutes": 5}'
    r = getattr(h.client(), method)(f"/api/timers/{item.id}{path}", **kwargs)
    assert r.status_code == 403 and service.items[item.id].state == "scheduled"


# --- sockets ----------------------------------------------------------------------------------


def test_home_socket_speaks_the_ring_and_takes_dismiss(settings: Settings, tmp_path: Path) -> None:
    tts = FakeTTS()
    h = Harness(settings, tmp_path, tts=tts)
    service, clock = with_timers(h)
    item = timer(service, 30, label="tea")
    with h.client().websocket_connect(f"{WS}/ws?speak=1") as ws:
        assert ws.receive_json() == {"type": "state", "state": "idle"}
        assert ws.receive_json()["type"] == "timers"  # the snapshot on connect
        clock.advance(30)
        service.tick()
        got = frames(ws, "tts_end")
        ringing = next(f for f in got if isinstance(f, dict) and f["type"] == "timer_ringing")
        assert ringing["say"] == "Timer done: tea." and tts.spoken == [("Timer done: tea.", "en")]
        assert types(got).index("timer_ringing") < types(got).index("tts_start")
        ws.send_json({"type": "timer_dismiss", "id": item.id})
        stopped = frames(ws, "timer_stopped")
        assert stopped[-1] == {"type": "timer_stopped", "id": item.id, "how": "dismissed"}
        assert "error" not in types(stopped)
        assert frames(ws, "timers")[-1]["timers"][0]["state"] == "done"  # snapshot follows
    assert service.items[item.id].state == "done"


def test_d3_no_timer_tools_with_a_browser_connector(settings: Settings, tmp_path: Path) -> None:
    class BrowserConnector(FakeConnector):
        kind = "browser"

    h = Harness(settings, tmp_path)
    with_timers(h)
    h.connectors.register(BrowserConnector("web"))
    h.llm("big-model").responses = [resp("ok"), resp("ok")]
    client = h.client()
    with client.websocket_connect(f"{WS}/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "text", "text": "hello"})
        turn_frames(ws)
    h.bots.update("main", _fields({"connectors": ["web"]}))  # B4: the browser alone
    with client.websocket_connect(f"{WS}/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "text", "text": "hello"})
        turn_frames(ws)
    before, after = h.llm("big-model").offered
    assert "set_timer" in before and "get_current_time" in before
    assert not any(n.endswith("_timer") or n.startswith("set_") for n in after)


def test_d3_agrees_with_the_browser_allowlist(settings: Settings, tmp_path: Path) -> None:
    """Same test as browser/policy.py: the kind, or an id that looks like a browser
    (also when the connector is gone and its kind unknown)."""
    from sasha.bots import Bot

    h = Harness(settings, tmp_path)
    with_timers(h)
    main = h.bots.get("main")
    assert not h.deps.has_browser(main)
    assert h.deps.has_browser(Bot(**{**main.model_dump(), "connectors": ["browser:work"]}))


# --- wake turns -----------------------------------------------------------------------


def test_wake_set_timer_is_voice_made_and_its_reply_stands(
    settings: Settings, tmp_path: Path
) -> None:
    name = settings.assistant.wake_word.phrase
    h, stt = make(settings, tmp_path)
    service, _ = with_timers(h)
    stt.say(f"{name}, set a timer for ten minutes")
    h.llm("big-model").responses = [
        resp("", [ToolCall("set_timer", {"minutes": 10})]),
        resp("I've set a timer for 10 minutes."),
    ]
    with h.client().websocket_connect(f"{WS}/ws?wake=1") as ws:
        listen(ws)
        segment(ws)
        got = turn_frames(ws)
    reply = next(f for f in got if isinstance(f, dict) and f["type"] == "reply")
    assert reply["text"] == "I've set a timer for 10 minutes."  # a real change: no override
    (item,) = service.items.values()
    assert item.created_by == "wake" and item.due == START + 600
    assert h.deps.timers is not None
    assert "set_timer" in h.llm("big-model").offered[0]  # no device-control opt-in needed


def test_wake_unclear_time_lets_the_model_ask(settings: Settings, tmp_path: Path) -> None:
    name = settings.assistant.wake_word.phrase
    h, stt = make(settings, tmp_path)
    service, _ = with_timers(h)
    stt.say(f"{name}, set an alarm for seven")
    h.llm("big-model").responses = [
        resp("", [ToolCall("set_alarm", {"hour": 7})]),
        resp("Seven in the morning or in the evening?"),
    ]
    with h.client().websocket_connect(f"{WS}/ws?wake=1") as ws:
        listen(ws)
        segment(ws)
        got = turn_frames(ws)
    reply = next(f for f in got if isinstance(f, dict) and f["type"] == "reply")
    assert reply["text"] == "Seven in the morning or in the evening?" and not service.items


def test_voice_made_items_say_so(tmp_path: Path) -> None:
    service, hub, _ = make_service(tmp_path)
    item = timer(service, 60, by="wake")
    assert hub.line(item) == "Voice reminder: Timer done."


# --- the stop window (D1) -------------------------------------------------------------


def ring_on_listener(
    settings: Settings, tmp_path: Path, heard: str
) -> tuple[list[Any], TimerItem, TimerService]:
    h, stt = make(settings, tmp_path, tts=FakeTTS(), speaking_tail_s=0.05)
    service, clock = with_timers(h)
    item = timer(service, 5, label="tea")
    with h.client().websocket_connect(f"{WS}/ws?wake=1&speak=1") as ws:
        listen(ws)
        time.sleep(0.1)  # the listener_state frame is handled
        clock.advance(5)
        service.tick()
        got = frames(ws, "stop_window")
        stt.say(heard)
        segment(ws)
        got += frames(ws, "stop_result")
        if got[-1]["accepted"]:
            got += frames(ws, "timers")
        else:
            got += frames(ws, "wake")
    return got, item, service


def test_stop_word_in_the_window_dismisses(settings: Settings, tmp_path: Path) -> None:
    got, item, service = ring_on_listener(settings, tmp_path, "Stop.")
    order = types(got)
    assert order.index("tts_end") < order.index("resume_capture") < order.index("stop_window")
    window = next(f for f in got if isinstance(f, dict) and f["type"] == "stop_window")
    assert window == {"type": "stop_window", "id": item.id, "timeout_s": 8}
    result = next(f for f in got if isinstance(f, dict) and f["type"] == "stop_result")
    assert result == {"type": "stop_result", "id": item.id, "accepted": True, "action": "dismiss"}
    assert service.items[item.id].state == "done"
    assert "Stop" not in str([f for f in got if isinstance(f, dict)])  # text never sent


def test_snooze_word_snoozes(settings: Settings, tmp_path: Path) -> None:
    got, item, service = ring_on_listener(settings, tmp_path, "snooze")
    assert first(got, "stop_result")["action"] == "snooze"
    assert service.items[item.id].state == "scheduled" and service.items[item.id].bare_snoozes == 1


@pytest.mark.parametrize("heard", ["okay", "stop the music now", "okay stop"])
def test_other_segments_do_not_stop_it(settings: Settings, tmp_path: Path, heard: str) -> None:
    got, item, service = ring_on_listener(settings, tmp_path, heard)
    result = first(got, "stop_result")
    assert result == {"type": "stop_result", "id": item.id, "accepted": False, "action": None}
    assert service.items[item.id].state == "ringing"
    assert got[-1] == {"type": "wake", "heard": False, "command": False}  # the normal check


def open_window(settings: Settings, tmp_path: Path) -> tuple[Any, Any, TimerService, TimerItem]:
    h, stt = make(settings, tmp_path, tts=FakeTTS(), speaking_tail_s=0.05)
    service, clock = with_timers(h)
    item = timer(service, 5, label="tea")
    clock.advance(5)
    return h, stt, service, item


def test_t6_card_dismiss_closes_the_stop_window(settings: Settings, tmp_path: Path) -> None:
    h, stt, service, item = open_window(settings, tmp_path)
    client = h.client()
    with client.websocket_connect(f"{WS}/ws?wake=1&speak=1") as ws:
        listen(ws)
        time.sleep(0.1)
        service.tick()
        frames(ws, "stop_window")
        assert client.post(f"/api/timers/{item.id}/dismiss").status_code == 200
        frames(ws, "timer_stopped")
        time.sleep(0.1)  # the stop reaches the socket's loop
        stt.say("stop")
        segment(ws)
        got = frames(ws, "wake")  # a normal wake check, not a stop answer
    assert "stop_result" not in types(got) and got[-1]["heard"] is False


def test_t7_fall_through_is_dropped_while_speaking(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sasha.server.speaking import SpeakingTracker

    name = settings.assistant.wake_word.phrase
    h, stt, service, _ = open_window(settings, tmp_path)
    with h.client().websocket_connect(f"{WS}/ws?wake=1&speak=1") as ws:
        listen(ws)
        time.sleep(0.1)
        service.tick()
        frames(ws, "stop_window")
        monkeypatch.setattr(SpeakingTracker, "speaking", lambda self: True)  # Home speaks
        stt.say(f"{name}, what time is it")
        segment(ws)
        got = frames(ws, "wake")
    assert first(got, "stop_result")["accepted"] is False
    assert got[-1]["heard"] is False and h.llm("big-model").seen == []  # no wake turn
