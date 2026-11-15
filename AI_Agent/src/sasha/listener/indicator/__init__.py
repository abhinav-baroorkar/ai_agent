"""The on-screen sign for the wake word listener: a tray icon, and two short sounds.

The product rule is that the microphone is never open without a visible sign, so this
is a safety part. The listener opens the microphone only while `available()` is true.

    indicator = make_indicator()
    indicator.start(on_pause_toggle, on_lost)
    indicator.available()        # asked of the bus each time, never assumed
    indicator.set_state("waiting")
    indicator.earcon("wake")     # Earcon(pcm, sample_rate)
    indicator.stop()
"""

from __future__ import annotations

from sasha.listener.indicator.base import (
    MIC_OPEN,
    STATES,
    Earcon,
    EarconKind,
    FakeIndicator,
    Indicator,
    Persona,
    State,
    describe,
)
from sasha.listener.indicator.earcons import earcon
from sasha.listener.indicator.tray import TrayIndicator

__all__ = [
    "MIC_OPEN",
    "STATES",
    "Earcon",
    "EarconKind",
    "FakeIndicator",
    "Indicator",
    "Persona",
    "State",
    "TrayIndicator",
    "describe",
    "earcon",
    "make_indicator",
]


def make_indicator(who: Persona | None = None) -> Indicator:
    """The real tray indicator. The name and wake phrase come from `assistant.yaml`."""
    from sasha.listener.indicator.bus import JeepneyBus

    if who is None:
        from sasha.config import load_settings

        assistant = load_settings().assistant
        who = Persona(name=assistant.name, wake_phrase=assistant.wake_word.phrase)
    return TrayIndicator(JeepneyBus, who)
