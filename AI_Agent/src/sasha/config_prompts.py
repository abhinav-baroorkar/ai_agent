"""Wording the server adds to system prompts, per language (kept apart from config.py,
which is near the 400-line limit). The persona itself stays in assistant.yaml."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Limit(BaseModel):
    """Something the assistant can't do, said unless one of `unless_any` tools exists."""

    text: str
    unless_any: list[str] = Field(default_factory=list)


_WEB = ["search", "open_url"]
_BROWSER = [*_WEB, "read_page", "click", "scroll", "back", "list_tabs", "switch_tab"]
_TIMERS = ["set_timer", "set_alarm", "set_reminder", "list_timers", "cancel_timer"]
_DEVICES_READ = ["list_devices", "get_state"]
_CAN_BROWSE = "search the web and read web pages in your own browser"
_CAN_TIMERS = "set timers, alarms and reminders"


class PromptLines(BaseModel):
    # Spoken conversations (the Home window, the listener): what comes out is read aloud.
    spoken_note: str = (
        "Everything you write here is spoken aloud. Answer in short, plain spoken "
        "sentences: no lists, no markdown, no asterisks or other symbols, no emoji."
    )
    times_word: str = "times"  # "2*3" is spoken as "2 times 3" (server/spoken.py)
    # What this bot can really do, from its tools (prompting.py).
    can_head: str = "What you can do right now, and only this:"
    no_tools: str = "You have no tools right now: you can only talk and answer from what you know."
    cannot_head: str = "What you can't do:"
    never_claim: str = (
        "When asked what you can do, say only the things above, briefly. Never claim any "
        "other ability; if asked for something else, say you can't do that yet. Use a "
        "tool only when the request needs it, and don't mention a tool's result (such "
        "as the time) unless it answers what was asked."
    )
    # Tool name -> what it lets you do. Tools that share a phrase are said once; a tool
    # missing here isn't named at all (its name may come from outside, such as MCP).
    abilities: dict[str, str] = Field(
        default_factory=lambda: {
            "get_current_time": "tell the current date and time",
            **dict.fromkeys(_DEVICES_READ, "check the smart-home devices and their state"),
            "call_service": "switch and adjust the smart-home devices",
            **dict.fromkeys(_BROWSER, _CAN_BROWSE),
            **dict.fromkeys(_TIMERS, _CAN_TIMERS),
            "snooze_timer": _CAN_TIMERS,
        }
    )
    limits: list[Limit] = Field(
        default_factory=lambda: [
            Limit(text="You can't go on the internet or look anything up online.",
                  unless_any=_WEB),
            Limit(text="You can't check the weather or the news.", unless_any=_WEB),
            Limit(text="You can't set timers, alarms or reminders.", unless_any=_TIMERS),
        ]
    )  # fmt: skip
