"""Wake-word rules and the server's own wake-turn sentences (moved out of config.py,
which reached the 400-line limit). `sasha.config` re-exports both names."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ReplyLines(BaseModel):
    """The server's own sentences for wake turns, per language (Marathi comes later)."""

    not_changed: str = "I didn't change: {names}."
    changed: str = "I changed: {names}."
    nothing_changed: str = "Nothing was changed."
    ask_again: str = "Can you say the time again?"  # when the model's question also claimed
    asking_words: list[str] = Field(  # a question opening with these is a real question
        default_factory=lambda: ["should", "shall", "would", "will", "can", "could", "may"]
    )
    more: str = "and {count} more"
    joiner: str = ", "
    unknown_one: str = "a device I don't know"
    unknown_many: str = "{count} devices I don't know"
    # A wake command asks for a change (so the model's text is held until the turn
    # shows a change really happened) if it has a change verb, or a state word next
    # to a device word and is not a question ("is the light on?" streams as usual).
    change_verbs: list[str] = Field(
        default_factory=lambda: [
            "turn", "switch", "set", "dim", "brighten", "raise", "lower", "increase",
            "decrease", "mute", "unmute", "start", "stop", "play", "pause", "activate",
            "toggle", "kill", "cut", "shut",
        ]
    )  # fmt: skip
    state_words: list[str] = Field(
        default_factory=lambda: [
            "on", "off", "up", "down", "brighter", "dimmer", "louder", "quieter",
            "darker", "lighter", "out",
        ]
    )  # fmt: skip
    device_words: list[str] = Field(
        default_factory=lambda: [
            "light", "lights", "lamp", "lamps", "fan", "fans", "tv", "television",
            "music", "volume", "speaker", "speakers", "scene", "it", "them", "everything",
        ]
    )  # fmt: skip
    # A command starting with one of these is a question even if it has a change verb
    # ("when does the sun set?"); polite requests start with can/could/please instead.
    comparatives: list[str] = Field(
        default_factory=lambda: [
            "brighter", "dimmer", "louder", "quieter", "warmer", "cooler", "darker",
            "lighter", "softer", "higher", "lower",
        ]
    )  # fmt: skip
    number_words: list[str] = Field(
        default_factory=lambda: [
            "percent", "half", "full", "max", "maximum", "minimum", "ten", "twenty",
            "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
        ]
    )  # fmt: skip
    suggestion_starts: list[str] = Field(
        default_factory=lambda: [
            "how about", "what about", "why don't you", "why dont you", "why not",
            "what if",
        ]
    )  # fmt: skip
    wh_words: list[str] = Field(
        default_factory=lambda: [
            "what", "which", "who", "when", "where", "why", "how", "what's", "who's",
            "where's", "how's", "when's",
        ]
    )  # fmt: skip
    question_words: list[str] = Field(
        default_factory=lambda: [
            "is", "are", "was", "were", "what", "which", "who", "when", "where", "why",
            "how", "does", "do", "did", "what's", "who's", "where's", "how's", "when's",
        ]
    )  # fmt: skip
    # Words that claim a device was changed ("I've turned off ..."). A plain state
    # report ("the hallway light is off") has none of them, so it is left alone.
    claim_words: list[str] = Field(
        default_factory=lambda: [
            "turned", "turning", "switched", "switching", "dimmed", "brightened",
            "activated", "deactivated", "started", "stopped", "paused", "changed",
            "adjusted", "set", "done", "turn", "switch",
        ]
    )  # fmt: skip


class WakeConfig(BaseModel):
    """Wake-word rules on the brain (v1: detection by transcription; see server/wake.py).

    Persona-free: the phrase and its spellings live in assistant.yaml `wake_word`.
    """

    armed_window_s: float = Field(default=5.0, gt=0, le=30)  # phrase alone → next segment
    confirm_window_s: float = Field(default=5.0, gt=0, le=30)  # spoken yes after a question
    cooldown_s: float = Field(default=3.0, ge=0, le=60)  # after any turn, brain-wide
    speaking_tail_s: float = Field(default=0.3, ge=0, le=5)  # mic stays closed after tts_end
    max_segment_s: float = Field(default=8.0, gt=0, le=30)
    # Phrase alone: correct ones score 0.45-0.60 (synthetic, 2026-11-16); reset from telemetry.
    min_confidence: float = Field(default=0.35, ge=0, le=1)
    # "Phrase, command" needs evidence the phrase was said (false wakes, 2026-11-16, at
    # 0.26-0.46 on 5.4-7.5 s segments; bench/wake_false_accepts.py): refused below the
    # first bar; below the second, a segment of recheck_from_s or longer is decoded again
    # without the hint and must still start with the phrase's first word. Real wakes with
    # a command (synthetic, room included): 0.38-0.78 on 2.6-3.8 s. Reset from telemetry.
    min_command_confidence: float = Field(default=0.35, ge=0, le=1)
    sure_command_confidence: float = Field(default=0.55, ge=0, le=1)
    recheck_from_s: float = Field(default=4.0, ge=0, le=30)
    max_entities_per_turn: int = Field(default=3, ge=1, le=50)  # more needs a spoken yes
    max_calls_per_minute: int = Field(default=6, ge=1, le=60)  # device changes, brain-wide
    volume_cap: float = Field(default=0.6, ge=0, le=1)
    keep_alive_while_listening: int | str = -1  # Ollama keep_alive while a listener is on
    fillers: list[str] = Field(default_factory=lambda: ["uh", "um", "hey", "ok", "okay", "oh"])
    # Words right after the name that mean it is talked about, not addressed.
    continuations: list[str] = Field(
        default_factory=lambda: ["is", "was", "said", "says", "and", "or", "too", "will"]
    )
    yes_words: list[str] = Field(
        default_factory=lambda: ["yes", "yeah", "yep", "sure", "confirm", "ok", "okay"]
    )
    # A clear no; anything that is neither is reported "unclear" (and still means no).
    no_words: list[str] = Field(
        default_factory=lambda: ["no", "nope", "nah", "don't", "dont", "stop", "cancel"]
    )
    # Truthful replies (server/wake_truth.py): after a device change on a wake turn did
    # not happen, the server says so itself. Device control by voice requires it.
    truthful_replies: bool = True
    reply_lines: dict[str, ReplyLines] = Field(default_factory=lambda: {"en": ReplyLines()})
    prompt_note: str = (
        "Say that a device changed only if a tool result says it did. If a tool result "
        "is an error, say the device was not changed."
    )
