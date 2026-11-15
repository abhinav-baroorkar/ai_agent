"""What the listener sees of the indicator: the states, the interface and a fake."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol, get_args

State = Literal["waiting", "listening", "thinking", "speaking", "paused", "offline", "debug"]
STATES: tuple[State, ...] = get_args(State)
EarconKind = Literal["wake", "armed"]

# States in which the microphone is open. Every one of them shows a live microphone glyph.
MIC_OPEN: frozenset[State] = frozenset({"waiting", "listening", "debug"})


@dataclass(frozen=True)
class Earcon:
    """A short sound: signed 16-bit little-endian mono PCM at `sample_rate` Hz."""

    pcm: bytes
    sample_rate: int


@dataclass(frozen=True)
class Persona:
    """The words that come from `assistant.yaml`; nothing here is hard-coded."""

    name: str
    wake_phrase: str


def describe(state: State, who: Persona) -> str:
    """One plain sentence per state, used as the tray title and tooltip."""
    name = who.name
    text: dict[State, str] = {
        "waiting": f"{name} is listening for “{who.wake_phrase}”. The microphone is on.",
        "listening": f"{name} is listening to you. The microphone is on.",
        "thinking": f"{name} is thinking.",
        "speaking": f"{name} is speaking.",
        "paused": f"{name} is paused. The microphone is off.",
        "offline": f"{name} is offline. The microphone is off.",
        "debug": f"{name} is listening and SAVING AUDIO to disk (debug mode).",
    }
    return text[state]


class Indicator(Protocol):
    """The on-screen sign for the wake word listener.

    The listener opens the microphone only while `available()` is true, and stops
    capture as soon as `on_lost` is called.
    """

    def start(self, on_pause_toggle: Callable[[], None], on_lost: Callable[[], None]) -> None: ...

    def available(self) -> bool: ...

    def set_state(self, state: State) -> None: ...

    def stop(self) -> None: ...

    def earcon(self, kind: EarconKind) -> Earcon: ...


@dataclass
class FakeIndicator:
    """An indicator for tests: no bus, no screen. Drive it with `click()` and `lose()`."""

    shown: bool = True
    started: bool = False
    states: list[State] = field(default_factory=list)
    _toggle: Callable[[], None] | None = None
    _lost: Callable[[], None] | None = None

    def start(self, on_pause_toggle: Callable[[], None], on_lost: Callable[[], None]) -> None:
        self._toggle, self._lost, self.started = on_pause_toggle, on_lost, True

    def available(self) -> bool:
        return self.started and self.shown

    def set_state(self, state: State) -> None:
        if state not in STATES:
            raise ValueError(f"Unknown indicator state: {state!r}")
        self.states.append(state)

    @property
    def state(self) -> State | None:
        return self.states[-1] if self.states else None

    def stop(self) -> None:
        self.started = False

    def earcon(self, kind: EarconKind) -> Earcon:
        from sasha.listener.indicator.earcons import earcon

        return earcon(kind)

    # --- test controls ---------------------------------------------------------
    def click(self) -> None:
        """The owner clicks the icon (pause or resume)."""
        if self._toggle is not None:
            self._toggle()

    def lose(self) -> None:
        """The tray disappears: the sign is no longer on screen."""
        self.shown = False
        if self._lost is not None:
            self._lost()

    def restore(self) -> None:
        self.shown = True
