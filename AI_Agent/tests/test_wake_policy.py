"""Wake-turn tool policy (server/wake_policy.py): read-only default, opt-in limits, C1."""

from __future__ import annotations

import json
from typing import Any

import pytest

from sasha.config import WakeConfig
from sasha.server.wake_policy import RateLimiter, WakeToolPolicy, describe
from sasha.tools import Tool, ToolRegistry


class Devices:
    """Fake Home Assistant tools: records every service call that really ran."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def registry(self) -> ToolRegistry:
        obj: dict[str, Any] = {"type": "object", "properties": {}}
        call_params = {
            "type": "object",
            "properties": {
                "domain": {"type": "string"},
                "service": {"type": "string"},
                "entity_id": {"type": "string"},
                "data": {"type": "object"},
            },
            "required": ["domain", "service", "entity_id"],
        }

        def call_service(domain: str, service: str, entity_id: Any, data: Any = None) -> Any:
            self.calls.append(
                {"domain": domain, "service": service, "entity_id": entity_id, "data": data}
            )
            return {"ok": True}

        return ToolRegistry(
            [
                Tool("list_devices", "list", obj, lambda: [{"entity_id": "light.a"}]),
                Tool("get_state", "state", obj, lambda **_: {"state": "on"}),
                Tool("call_service", "call", call_params, call_service),
                Tool("send_email_draft", "other tool", obj, lambda: "never"),
            ]
        )


class Policy:
    def __init__(self, control: bool = True, answer: bool = True, limit: int = 6) -> None:
        self.devices = Devices()
        self.asked: list[str] = []
        self.records: list[str] = []
        self.answer = answer
        self.control = control
        self.limiter = RateLimiter(limit)
        self.policy = WakeToolPolicy(
            WakeConfig(), lambda: self.control, self.limiter, self._confirm, self.records.append
        )
        self.tools = self.policy.wrap(self.devices.registry())

    def _confirm(self, action: str) -> bool:
        self.asked.append(action)
        return self.answer

    def call(self, entity: Any, domain: str = "light", service: str = "turn_off", **data: Any):
        args: dict[str, Any] = {"domain": domain, "service": service, "entity_id": entity}
        if data:
            args["data"] = data
        result = self.tools.call("call_service", args)
        return result.ok, json.loads(result.content)


def test_read_only_tools_by_default() -> None:
    p = Policy(control=False)
    assert set(p.tools.names) == {"list_devices", "get_state", "call_service"}
    ok, body = p.call("light.kitchen")
    assert not ok and "can't change devices" in body["error"]
    assert p.devices.calls == [] and p.records == ["refused_read_only"]
    assert "send_email_draft" not in p.tools.names  # other tools never in wake turns


def test_opt_in_single_device_acts_directly() -> None:
    p = Policy()
    assert p.call("light.kitchen") == (True, {"ok": True})
    assert p.asked == [] and len(p.devices.calls) == 1 and p.records == ["allowed"]


def test_opt_in_is_read_live() -> None:
    p = Policy(control=True)
    p.control = False  # the owner switched it off in the Bots screen
    assert p.call("light.kitchen")[0] is False


@pytest.mark.parametrize(
    ("entity", "domain", "service"),
    [
        ("all", "light", "turn_off"),
        ("scene.movie", "scene", "turn_on"),
        (["light.a", "light.b", "light.c", "light.d"], "light", "turn_on"),
    ],
)
def test_needs_a_spoken_yes(entity: Any, domain: str, service: str) -> None:
    yes = Policy(answer=True)
    assert yes.call(entity, domain, service)[0] is True and len(yes.asked) == 1
    no = Policy(answer=False)
    ok, body = no.call(entity, domain, service)
    assert not ok and "Not confirmed" in body["error"] and no.devices.calls == []


def test_c1_entities_counted_per_turn_not_per_call() -> None:
    """One turn with 4 single-entity calls asks before the 4th."""
    p = Policy(answer=True)
    for room in ("a", "b", "c"):
        assert p.call(f"light.{room}")[0] is True
    assert p.asked == []
    assert p.call("light.d")[0] is True
    assert p.asked == ["turn off d"] and len(p.devices.calls) == 4
    p.policy.begin_turn()  # a new turn starts counting again
    p.asked.clear()
    assert p.call("light.e")[0] is True and p.asked == []


def test_a_no_refuses_the_rest_of_the_turn() -> None:
    p = Policy(answer=False)
    for room in ("a", "b", "c"):
        p.call(f"light.{room}")
    assert p.call("light.d")[0] is False
    ok, body = p.call("light.a")  # even an already-touched device: the turn is closed
    assert not ok and "not confirmed" in body["error"]
    assert len(p.devices.calls) == 3


@pytest.mark.parametrize("entity", ["switch.garage", ["light.a", "switch.door"]])
def test_switches_are_never_allowed(entity: Any) -> None:
    p = Policy(answer=True)
    ok, body = p.call(entity, domain="light")
    assert not ok and "Switches" in body["error"]
    assert p.call("switch.x", domain="switch", service="turn_on")[0] is False
    assert p.devices.calls == [] and p.asked == []


def test_volume_is_capped() -> None:
    p = Policy()
    ok, body = p.call("media_player.tv", "media_player", "volume_set", volume_level=1.0)
    assert ok and body["note"] == "volume limited to 0.6"
    assert p.devices.calls[0]["data"] == {"volume_level": 0.6}
    assert p.records == ["allowed_capped"]
    p.call("media_player.tv", "media_player", "volume_set", volume_level=0.3)
    assert p.devices.calls[1]["data"] == {"volume_level": 0.3}


@pytest.mark.parametrize(
    "data",
    [{"volume_level": "1.0"}, '{"volume_level": "0.99"}', '{"volume_level": 1}'],
)
def test_volume_as_string_is_coerced_then_capped(data: Any) -> None:
    """S1: Home Assistant coerces "1.0" to 1.0, so strings must not slip past the cap."""
    p = Policy()
    args = {"domain": "media_player", "service": "volume_set", "entity_id": "media_player.tv"}
    result = p.tools.call("call_service", {**args, "data": data})
    assert result.ok and json.loads(result.content)["note"] == "volume limited to 0.6"
    assert p.devices.calls[0]["data"] == {"volume_level": 0.6}


@pytest.mark.parametrize(
    "bad",
    ["loud", "nan", "inf", "-0.1", "1.5", 2, -1, True, None, [0.5], {"v": 1}],
)
def test_volume_must_be_a_finite_number_from_0_to_1(bad: Any) -> None:
    p = Policy()
    ok, body = p.call("media_player.tv", "media_player", "volume_set", volume_level=bad)
    assert not ok and "volume_level must be a number from 0 to 1" in body["error"]
    assert p.devices.calls == [] and p.records == ["refused_bad_value"]


def test_rate_limit_six_per_minute_brain_wide() -> None:
    now = [0.0]
    limiter = RateLimiter(6, clock=lambda: now[0])
    first = Policy()
    first.policy.limiter = limiter
    second = Policy()
    second.policy.limiter = limiter  # another wake turn shares the same budget
    for i in range(3):
        assert first.call(f"light.{i}")[0] is True
        first.policy.begin_turn()
        assert second.call(f"light.x{i}")[0] is True
        second.policy.begin_turn()
    ok, body = first.call("light.extra")
    assert not ok and "Too many" in body["error"]
    now[0] += 61
    assert first.call("light.extra")[0] is True


def test_describe_is_short_and_clean() -> None:
    assert describe("light", "turn_off", ["all"]) == "turn off all light devices"
    assert describe("scene", "turn_on", ["scene.movie_night"]) == "turn on movie night"
    assert describe("light", "turn_on", ["light.a", "light.b"]) == "turn on 2 devices"
    assert "<" not in describe("light", "turn_on", ["light.<script>"])
