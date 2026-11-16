"""The said-check for memory's M1 too: target words from the owner's recent messages
(built by the orchestrator, within a window), `always_guard`, and `said_checked`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sasha.browser.helper import BROWSE_TOOL
from sasha.config import Settings
from sasha.orchestrator import SAME_TURN, SAY_IT, Assistant
from sasha.pipeline.llm import ToolCall
from sasha.said import SaidGuard, device_change_said
from sasha.telemetry import TelemetryWriter
from sasha.tools import Tool, ToolRegistry
from tests.server_fakes import FakeLLM, resp
from tests.test_browse_taint import DEVICES, OBJ, Home, _call

NAMES = {d["entity_id"]: d["name"] for d in DEVICES}
OFF = {"domain": "light", "service": "turn_off", "entity_id": "light.bedroom"}


def test_targets_may_come_from_earlier_actions_never() -> None:
    assert device_change_said("turn it off", OFF, NAMES, earlier="is the bedroom light on?")
    assert not device_change_said("turn it off", OFF, NAMES)  # no target
    # the action must be in the current message, never only earlier
    assert not device_change_said("the bedroom one", OFF, NAMES, earlier="turn off the light")
    scene = {"domain": "scene", "service": "turn_on", "entity_id": "scene.movie_night"}
    assert not device_change_said("do it", scene, NAMES, earlier="movie night later?")
    assert SaidGuard(lambda: DEVICES)("call_service", OFF, "turn it off", "the bedroom light")
    assert not SaidGuard(lambda: DEVICES)("call_service", OFF, "turn it off")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _setup(
    settings: Settings, tmp_path: Path, extra: list[Tool] | None = None
) -> tuple[Assistant, FakeLLM, Home, Clock, list[tuple[Any, ...]]]:
    home = Home()
    tools = ToolRegistry([*home.tools(), *(extra or [])])
    llm = FakeLLM("m")
    seen: list[tuple[Any, ...]] = []
    real = SaidGuard(tools.get("list_devices").handler)

    def guard(tool: str, args: dict[str, Any], text: str, earlier: str) -> bool:
        seen.append((tool, text, earlier))
        return real(tool, args, text, earlier)

    clock = Clock()
    telemetry = TelemetryWriter(tmp_path, "a", "p", enabled=False)
    a = Assistant(
        settings, llm, tools, telemetry, change_guard=guard, always_guard=True, clock=clock
    )
    return a, llm, home, clock, seen


def _turn(a: Assistant, llm: FakeLLM, text: str, *calls: ToolCall) -> None:
    llm.responses = [resp(calls=list(calls)), resp("ok")] if calls else [resp("ok")]
    a.handle_text(text)


def test_always_guard_uses_the_owners_recent_messages(settings: Settings, tmp_path: Path) -> None:
    a, llm, home, clock, seen = _setup(settings, tmp_path)
    _turn(a, llm, "is the bedroom light on?")
    clock.now += 30
    _turn(a, llm, "turn it off", _call("turn_off", "light.bedroom"))
    assert [c["entity_id"] for c in home.calls] == ["light.bedroom"]
    assert seen[-1] == ("call_service", "turn it off", "is the bedroom light on?")
    # the orchestrator built `earlier`, from the owner's own messages only
    _turn(a, llm, "turn the kitchen light off", _call("turn_off", "light.kitchen"))
    assert seen[-1][2] == "is the bedroom light on? turn it off"  # the last 2 kept turns


def test_the_window_and_the_two_turn_rule(settings: Settings, tmp_path: Path) -> None:
    window = settings.profile.llm.target_window_s
    assert window == 180.0
    a, llm, home, clock, seen = _setup(settings, tmp_path)
    _turn(a, llm, "is the bedroom light on?")
    clock.now += window + 1
    _turn(a, llm, "turn it off", _call("turn_off", "light.bedroom"))
    assert home.calls == [] and seen[-1][2] == ""  # too long ago
    _turn(a, llm, "is the bedroom light on?")
    _turn(a, llm, "thanks")
    _turn(a, llm, "and the weather?")
    _turn(a, llm, "turn it off", _call("turn_off", "light.bedroom"))
    assert home.calls == []  # three turns back: outside the last 2
    assert "bedroom" not in seen[-1][2]


def test_while_tainted_earlier_is_empty(settings: Settings, tmp_path: Path) -> None:
    a, llm, home, _, seen = _setup(settings, tmp_path)
    _turn(a, llm, "is the bedroom light on?")
    _turn(a, llm, "look up x", ToolCall(BROWSE_TOOL, {"task": "x"}))
    _turn(a, llm, "turn it off", _call("turn_off", "light.bedroom"))
    assert home.calls == [] and seen[-1][2] == ""  # P1 as strict as before
    assert a.tainted


def test_said_checked_tools_run_their_own_check(settings: Settings, tmp_path: Path) -> None:
    ran: list[str] = []
    own = Tool(
        "set_reminder", "r", OBJ, lambda: ran.append("own") or {"ok": True}, said_checked=True
    )
    a, llm, home, _, seen = _setup(settings, tmp_path, [own])
    _turn(a, llm, "remind me", ToolCall("set_reminder", {}))
    assert ran == ["own"] and seen == []  # the guard deferred to the tool
    _turn(a, llm, "mystery please", ToolCall("mystery", {}))
    assert home.other == [] and SAY_IT in str(llm.seen[-1][0][-1]["content"])  # G4
    # the same-turn stop after a lookup still holds for it
    llm.responses = [
        resp(calls=[ToolCall(BROWSE_TOOL, {"task": "x"})]),
        resp(calls=[ToolCall("set_reminder", {})]),
        resp("ok"),
    ]
    a.handle_text("look up x and remind me")
    assert ran == ["own"] and SAME_TURN in str(llm.seen[-1][0][-1]["content"])


def test_without_always_guard_untainted_changes_need_no_check(
    settings: Settings, tmp_path: Path
) -> None:
    a, llm, home, _, seen = _setup(settings, tmp_path)
    a.always_guard = False
    _turn(a, llm, "lights out", _call("turn_off", "all"))
    assert len(home.calls) == 1 and seen == []
    assert Tool("x", "x", OBJ, lambda: None).said_checked is False


# --- review G1, G2 -------------------------------------------------------------------
def test_a_negated_request_is_refused() -> None:
    kitchen = {"domain": "light", "service": "turn_off", "entity_id": "light.kitchen"}
    door = {"domain": "lock", "service": "unlock", "entity_id": "lock.front_door"}
    assert device_change_said("turn off the kitchen light", kitchen, NAMES)
    for said in (
        "don't turn off the kitchen light",
        "do not turn off the kitchen light",
        "please never turn off the kitchen light",
        "don\u2019t turn the kitchen light off",
    ):
        assert not device_change_said(said, kitchen, NAMES), said
    assert not device_change_said("don't unlock the front door", door, NAMES)
    assert device_change_said("unlock the front door", door, NAMES)
    guard = SaidGuard(lambda: DEVICES, negations=("ne",))  # the words come from config
    assert guard("call_service", kitchen, "don't turn off the kitchen light")
    assert not guard("call_service", kitchen, "ne turn off the kitchen light")


def test_the_only_device_of_its_kind_may_be_named_by_kind() -> None:
    tv = {"domain": "media_player", "service": "volume_up", "entity_id": "media_player.lr"}
    one = {"media_player.lr": "Living room TV", "light.kitchen": "Kitchen Light"}
    assert device_change_said("turn the tv volume up", tv, one)
    two = {**one, "media_player.den": "Den TV"}
    assert not device_change_said("turn the tv volume up", tv, two)  # which one?
    assert device_change_said("turn the living room tv volume up", tv, two)


# --- the merge with timers: the marks ------------------------------------------------
def test_timer_tool_marks(tmp_path: Path) -> None:
    from sasha.server.timers.tools import add_timer_tools
    from tests.timer_fakes import make_service

    service, _, _ = make_service(tmp_path)
    tools = add_timer_tools(ToolRegistry(), service)
    marks = {n: (tools.get(n).read_only, tools.get(n).said_checked) for n in tools.names}
    assert marks["list_timers"] == (True, False)
    for name in ("set_timer", "set_alarm", "set_reminder"):
        assert marks[name] == (False, True), name  # their own said-check
    for name in ("cancel_timer", "snooze_timer"):
        # Their own owner-words check since memory's M1 (always_guard on memory bots),
        # so the device-only guard would otherwise refuse "cancel my timer" every time.
        assert marks[name] == (False, True), name
    names = ("set_timer", "set_alarm", "set_reminder", "cancel_timer", "snooze_timer")
    fields = {n: tools.get(n).said_fields for n in names}
    assert fields == {"set_timer": ("label",), "set_alarm": ("label",), "set_reminder": ("text",),
                      "cancel_timer": ("which",), "snooze_timer": ("which",)}  # fmt: skip


# --- delta review E1-E3, and G1 with earlier targets -----------------------------------
def test_an_earlier_target_fills_in_only_when_none_is_named_now() -> None:
    names = {**NAMES, "media_player.tv": "TV"}
    asked = "is the bedroom light on?"
    many = {"domain": "light", "service": "turn_off",
            "entity_id": ["light.kitchen", "light.bedroom"]}  # fmt: skip
    assert not device_change_said("turn off the tv", OFF, names, earlier=asked)  # E1 row 1
    assert not device_change_said("turn off the kitchen light", many, names, earlier=asked)
    assert device_change_said("turn it off", OFF, names, earlier=asked)
    assert device_change_said("switch it off", OFF, names, earlier=asked)  # a verb, not a kind
    assert not device_change_said("don't turn it off", OFF, names, earlier=asked)  # G1


def test_locks_and_covers_never_carry_a_target_over() -> None:
    door = {"domain": "lock", "service": "unlock", "entity_id": "lock.front_door"}
    assert not device_change_said("unlock it", door, NAMES, earlier="is the front door locked?")
    assert device_change_said("unlock the front door", door, NAMES)


def test_after_a_lookup_timers_work_but_page_text_is_refused(
    settings: Settings, tmp_path: Path
) -> None:
    """Primary's E3 decision: while tainted, a said_checked tool runs only if every word
    of its free-text fields is the owner's own (this turn, or the previous within the
    window); its own check still runs inside it. The real timer tools."""
    from sasha.conversation import current_turn
    from sasha.server.timers.tools import add_timer_tools
    from tests.timer_fakes import make_service

    service, _, _ = make_service(tmp_path)
    home = Home()
    tools = add_timer_tools(ToolRegistry(home.tools()), service, said=current_turn.get)
    llm = FakeLLM("m")
    telemetry = TelemetryWriter(tmp_path, "a", "p", enabled=False)
    a = Assistant(settings, llm, tools, telemetry, change_guard=SaidGuard(None))
    _turn(a, llm, "when does the shop close?", ToolCall(BROWSE_TOOL, {"task": "x"}))
    assert a.tainted
    _turn(a, llm, "set a timer for 20 minutes", ToolCall("set_timer", {"minutes": 20}))
    assert [c["ok"] for c in a.last_tool_calls] == [True]  # the owner's timer is set
    planted = ToolCall("set_reminder", {"text": "unlock the front door", "in_minutes": 10})
    _turn(a, llm, "remind me in 10 minutes", planted)
    assert [c["ok"] for c in a.last_tool_calls] == [False]
    assert SAY_IT in str(llm.seen[-1][0][-1]["content"])
    own = ToolCall("set_reminder", {"text": "call the plumber", "in_minutes": 10})
    _turn(a, llm, "remind me to call plumber in 10 minutes", own)
    assert [c["ok"] for c in a.last_tool_calls] == [True]
    _turn(a, llm, "cancel it", ToolCall("cancel_timer", {"which": "all"}))
    assert [c["ok"] for c in a.last_tool_calls] == [False]  # no own check: fails closed


def test_said_by_owner() -> None:
    from sasha.said import said_by_owner

    assert said_by_owner("buy the milk", "remind me to buy milk")
    assert said_by_owner("", "anything")
    assert said_by_owner("eggs", "remind me about the egg")  # a plural of his word
    assert not said_by_owner("unlock the front door", "remind me in 10 minutes")
