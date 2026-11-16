"""Re-review of 32fa818: T1 (the typed-path "Nothing was set." line) and the start/stop
hooks of the timers and the browser. Fakes only; fake clock, invented zone."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sasha.browser.tools import BrowserBridge
from sasha.config import Settings
from sasha.config_timers import TimersConfig
from sasha.connectors import ConnectorRegistry
from sasha.connectors.browser import BrowserConnector
from sasha.pipeline.llm import ToolCall
from sasha.server.app import create_app
from sasha.server.timers.truth import TimerTruth, asks_for_timer, claims_timer
from sasha.state import JsonStore
from tests.server_fakes import BASE_URL, WS, FakeTTS, Harness, resp
from tests.test_timers_server import with_timers
from tests.wake_fakes import frames, turn_frames

# Re-review of ea0b6e8: requests the old rule (timer word AND request verb) missed.
REVIEW_ROWS = [
    ("wake me up at 7", "Sure, I'll wake you at 7 tomorrow."),
    ("wake me up at 7", "Done, see you at 7."),
    ("can you time 10 minutes", "Okay, I'll let you know in 10 minutes."),
    ("I need an alarm for 6", "You got it, 6 am it is."),
    ("snooze it", "Snoozed for 5 minutes."),
    ("snooze it", "Okay, I'll ring again in 5 minutes."),
    ("ten minute timer please", "Your timer is running."),
    ("give me a nudge at 3", "Sure, I'll nudge you at 3."),
    ("ping me in 20 minutes", "I'll ping you in 20."),
    ("turn off the alarm", "The alarm is off."),
]

FALSE_CLAIMS = [
    "No problem, alarm set for 7.",
    "Done! Your timer is set, don't forget it.",
    "Alarm's set for 7.",
    "I set it for 7, your alarm will ring then.",
]


@pytest.mark.parametrize(
    ("text", "claims"),
    [
        *((c, True) for c in FALSE_CLAIMS),
        ("I've set an alarm for 7:00 AM today.", True),
        ("Okay, I've scheduled a reminder.", True),
        ("Should I set an alarm?", False),
        ("I can't set alarms yet.", False),
        # The negation is 3 words before "set": past the reach, so a claim. The safe side:
        # the worst case is an extra "Nothing was set.", never a missed one.
        ("I don't think I set a reminder.", True),
        ("The weather is nice today.", False),
    ],
)
def test_detector(text: str, claims: bool) -> None:
    assert claims_timer(text, TimersConfig()) is claims


@pytest.mark.parametrize(
    ("command", "asks"),
    [
        ("set an alarm for 7", True),
        ("remind me to call mom at 5", True),
        ("cancel my timer", True),
        *((command, True) for command, _ in [*(r for r in REVIEW_ROWS)]),
        ("can you set a timer?", True),  # "Can you ...?" is still a request
        ("time the eggs for me", True),
        ("how long is left on my timer?", False),  # a status question
        ("is my alarm set?", False),
        ("what time is it", False),  # "time" is no verb here
        ("what's the weather", False),
    ],
)
def test_asks_for_timer(command: str, asks: bool) -> None:
    assert asks_for_timer(command, TimersConfig()) is asks


@pytest.mark.parametrize(("command", "reply"), REVIEW_ROWS)
def test_review_rows_get_the_line(command: str, reply: str) -> None:
    """The reviewer's probes with no tool call: each one gets the server's line; spoken,
    it replaces the reply, with its question unless it was a cancel."""
    cfg = TimersConfig()
    lines = (cfg.nothing_set, cfg.nothing_yet, cfg.nothing_changed)
    assert TimerTruth(cfg, command, spoken=False).finish(reply, [])[1].endswith(lines)
    held = TimerTruth(cfg, command, spoken=True)
    held.feed(reply)
    said, final = held.finish(reply, [])
    cancel = command.startswith(("snooze", "turn off"))
    question = cfg.ask_generic_timer if "timer" in command else cfg.ask_generic
    assert said == final and said.startswith(lines)  # the model's text is never spoken
    assert said == cfg.nothing_changed if cancel else said.endswith(question)


def test_a_timer_request_always_gets_the_line_unless_made_or_asked_back() -> None:
    cfg = TimersConfig()
    honest = "I couldn't do that just now."
    for reply in (*FALSE_CLAIMS, honest):
        truth = TimerTruth(cfg, "Set an alarm for 7.", spoken=False)
        assert truth.finish(reply, [])[1].endswith((cfg.nothing_set, cfg.nothing_yet))
    asked = TimerTruth(cfg, "Set an alarm for 7.", spoken=False)
    assert asked.finish("Morning or evening?", [])[1] == "Morning or evening?"
    made = TimerTruth(cfg, "Set an alarm for 7.", spoken=False)
    calls: list[dict[str, Any]] = [{"name": "set_alarm", "ok": True}]
    assert made.finish("Alarm set for 7.", calls)[1] == "Alarm set for 7."


def test_a_listed_timer_is_a_report_not_a_claim() -> None:
    truth = TimerTruth(TimersConfig(), "what alarms do I have?", spoken=False)
    calls: list[dict[str, Any]] = [{"name": "list_timers", "ok": True}]
    assert truth.finish("Your alarm is set for 7.", calls)[1] == "Your alarm is set for 7."


def reply_of(items: list[Any]) -> str:
    return next(f["text"] for f in items if isinstance(f, dict) and f["type"] == "reply")


@pytest.mark.parametrize("claim", FALSE_CLAIMS)
def test_typed_false_claims_get_the_line(settings: Settings, tmp_path: Path, claim: str) -> None:
    h = Harness(settings, tmp_path)
    with_timers(h)
    h.llm("big-model").responses = [resp(claim)]
    with h.client().websocket_connect(f"{WS}/ws") as ws:
        ws.receive_json()
        ws.receive_json()  # the timers snapshot
        ws.send_json({"type": "text", "text": "Set an alarm for 7 am."})
        got = turn_frames(ws)
    assert reply_of(got) == f"{claim} {h.settings.profile.timers.nothing_set}"


@pytest.mark.parametrize("claim", FALSE_CLAIMS)
def test_spoken_false_claims_are_held_and_replaced(
    settings: Settings, tmp_path: Path, claim: str
) -> None:
    tts = FakeTTS()
    h = Harness(settings, tmp_path, tts=tts)
    with_timers(h)
    h.llm("big-model").responses = [resp(claim)]
    with h.client().websocket_connect(f"{WS}/ws?speak=1") as ws:
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "text", "text": "Set an alarm for 7 am."})
        got = frames(ws, "state", "idle")
    cfg = h.settings.profile.timers
    line = f"{cfg.nothing_set} {cfg.ask_generic}"
    assert reply_of(got) == line and " ".join(text for text, _ in tts.spoken) == line


def test_the_remembered_reply_is_what_was_said(settings: Settings, tmp_path: Path) -> None:
    """The history keeps the text after the timer check, "Nothing was set." included."""
    h = Harness(settings, tmp_path)
    with_timers(h)
    h.llm("big-model").responses = [resp("Alarm's set for 7."), resp("ok")]
    with h.client().websocket_connect(f"{WS}/ws") as ws:
        ws.receive_json()
        ws.receive_json()
        for text in ("Set an alarm for 7 am.", "Thanks."):
            ws.send_json({"type": "text", "text": text})
            turn_frames(ws)
    seen = h.llm("big-model").seen[-1][0]
    remembered = [m["content"] for m in seen if m["role"] == "assistant"]
    assert remembered == [f"Alarm's set for 7. {h.settings.profile.timers.nothing_set}"]


# --- the start/stop hooks: the timers' scheduler and the browser both run --------------


class FakeBridge(BrowserBridge):
    def __init__(self) -> None:  # no real browser service
        self.loop = None
        self.service = None  # type: ignore[assignment]
        self.events: list[str] = []

    def attach(self, loop: Any) -> None:
        self.events.append("attach")

    async def stop(self) -> None:
        self.events.append("stop")


def test_timers_and_browser_hooks_both_run(settings: Settings, tmp_path: Path) -> None:
    """A custom `lifespan=` once made the app skip the router hooks (the browser's)."""
    h = Harness(settings, tmp_path)
    service, _ = with_timers(h)
    bridge = FakeBridge()
    h.deps.connectors = ConnectorRegistry(
        [BrowserConnector(bridge)], JsonStore(tmp_path / "connectors.json")
    )
    with TestClient(create_app(h.deps, web_dir=None), base_url=BASE_URL):
        assert bridge.events == ["attach"]
        assert service._thread is not None and service._thread.is_alive()
    assert bridge.events == ["attach", "stop"]
    assert service._stop.is_set()


def test_set_alarm_call_succeeds_without_the_line(settings: Settings, tmp_path: Path) -> None:
    h = Harness(settings, tmp_path)
    service, _ = with_timers(h)
    h.llm("big-model").responses = [
        resp("", [ToolCall("set_alarm", {"hour": 7, "period": "am"})]),
        resp("Alarm's set for 7."),
    ]
    with h.client().websocket_connect(f"{WS}/ws") as ws:
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "text", "text": "Set an alarm for 7 am."})
        got = turn_frames(ws)
    assert reply_of(got) == "Alarm's set for 7." and len(service.items) == 1
