"""Tool limits for wake-word turns, enforced on the server whatever the bot says.

Anyone in earshot (or a television) can start a wake turn, so (design section 3):
- read-only by default: only `list_devices` and `get_state`;
- with the owner's opt-in (`voice` bot `wake_device_control`), `call_service` too, but:
  - never `switch.*` (garage relays and door strikes are often switches);
  - a spoken yes first for `entity_id: "all"`, any scene, or more than
    `max_entities_per_turn` distinct entities changed across the whole turn (counted
    per turn, not per call: a model may split "every room" into single calls);
  - a "no" (or no answer) refuses the rest of the turn's device changes;
  - `volume_level` capped at `volume_cap`;
  - at most `max_calls_per_minute` device changes from wake turns, brain-wide;
- every other tool is left out of wake turns in v1.
Decisions are reported through `record(label)`: labels only, never arguments.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from sasha.config import WakeConfig
from sasha.tools import Tool, ToolError, ToolRegistry

READ_ONLY_TOOLS = ("list_devices", "get_state")
CONTROL_TOOL = "call_service"
_CLEAN = re.compile(r"[^\w .-]+")


class RateLimiter:
    """At most `limit` events per `window_s`, shared by all wake turns (thread-safe)."""

    def __init__(
        self, limit: int, window_s: float = 60.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self.clock = clock
        self._times: deque[float] = deque()
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        with self._lock:
            now = self.clock()
            while self._times and now - self._times[0] >= self.window_s:
                self._times.popleft()
            if len(self._times) >= self.limit:
                return False
            self._times.append(now)
            return True


def _entities(entity_id: Any) -> list[str]:
    if isinstance(entity_id, str):
        return [entity_id]
    if isinstance(entity_id, list):
        return [str(e) for e in entity_id]
    return []


def _unit_number(value: Any) -> float | None:
    """`value` as a finite float in [0, 1] (numbers or numeric strings), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) and 0.0 <= number <= 1.0 else None


def _data_dict(data: Any) -> dict[str, Any] | None:
    if isinstance(data, str) and data.strip():
        try:
            data = json.loads(data)
        except ValueError:
            return None
    return dict(data) if isinstance(data, dict) else None


def describe(domain: str, service: str, entities: list[str]) -> str:
    """A short plain sentence for the spoken question (model-made names are cleaned)."""
    verb = _CLEAN.sub(" ", service.replace("_", " ")).strip()[:30]
    if entities == ["all"]:
        target = f"all {_CLEAN.sub(' ', domain)[:20]} devices"
    elif len(entities) == 1:
        target = _CLEAN.sub(" ", entities[0].split(".", 1)[-1].replace("_", " "))[:40]
    else:
        target = f"{len(entities)} devices"
    return f"{verb} {target}".strip()


class WakeToolPolicy:
    """One per listener socket. Call `begin_turn()` before every wake turn."""

    def __init__(
        self,
        config: WakeConfig,
        device_control: Callable[[], bool],
        limiter: RateLimiter,
        confirm: Callable[[str], bool],
        record: Callable[[str], None],
    ) -> None:
        self.config = config
        self.device_control = device_control
        self.limiter = limiter
        self.confirm = confirm
        self.record = record
        self.begin_turn()

    def begin_turn(self) -> None:
        self.turn_entities: set[str] = set()
        self.refused_rest = False

    def wrap(self, tools: ToolRegistry) -> ToolRegistry:
        """The wake-turn view of a bot's tools."""
        allowed = ToolRegistry()
        for name in tools.names:
            tool = tools.get(name)
            if name in READ_ONLY_TOOLS:
                allowed.register(tool)
            elif name == CONTROL_TOOL:
                guarded = self._guard(tool.handler)
                allowed.register(Tool(tool.name, tool.description, tool.parameters, guarded))
        return allowed

    def _refuse(self, label: str, message: str) -> ToolError:
        self.record(label)
        return ToolError(message)

    def _guard(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        def call_service(domain: str, service: str, entity_id: Any, data: Any = None) -> Any:
            if not self.device_control():
                raise self._refuse("refused_read_only", "Voice requests can't change devices")
            if self.refused_rest:
                raise self._refuse(
                    "refused_after_no", "Device changes for this request were not confirmed"
                )
            entities = _entities(entity_id)
            domain = str(domain)
            if domain == "switch" or any(e.startswith("switch.") for e in entities):
                raise self._refuse("refused_switch", "Switches can't be controlled by voice")
            values = _data_dict(data)
            capped = False
            if values is not None and "volume_level" in values:
                # Coerce, validate, then limit: Home Assistant turns "1.0" into 1.0, so a
                # string must not slip past the cap.
                volume = _unit_number(values["volume_level"])
                if volume is None:
                    raise self._refuse(
                        "refused_bad_value", "volume_level must be a number from 0 to 1"
                    )
                capped = volume > self.config.volume_cap
                values["volume_level"] = min(volume, self.config.volume_cap)
            changed = self.turn_entities | set(entities)
            needs_yes = (
                "all" in entities
                or domain == "scene"
                or any(e.startswith("scene.") for e in entities)
                or len(changed) > self.config.max_entities_per_turn
            )
            if needs_yes:
                self.record("confirm_asked")
                if not self.confirm(describe(domain, str(service), entities)):
                    self.refused_rest = True
                    raise self._refuse("confirm_no", "Not confirmed; nothing was changed")
                self.record("confirm_yes")
            if not self.limiter.try_acquire():
                raise self._refuse(
                    "rate_limited", "Too many device changes by voice; try again in a minute"
                )
            result = handler(
                domain=domain,
                service=service,
                entity_id=entity_id,
                data=values if values is not None else data,
            )
            self.turn_entities = changed
            self.record("allowed_capped" if capped else "allowed")
            if capped and isinstance(result, dict):
                result = {**result, "note": f"volume limited to {self.config.volume_cap}"}
            return result

        return call_service
