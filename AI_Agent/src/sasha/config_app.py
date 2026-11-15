"""Settings split out of config.py (at its size limit): the wake phrase, the app
shell's words, and the window opening on a wake."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AppText(BaseModel):
    """Words the app shell shows outside its page; `{name}` is filled in."""

    menu_comment: str = "Local voice assistant"  # the applications menu's tooltip
    listening_notice: str = "{name} is listening"  # the notification when it can't raise


class WindowConfig(BaseModel):
    """The wake word bringing up the window (Dependencies/team/app-shell-design.md 6)."""

    open_on_wake: bool = True
    min_interval_s: float = Field(default=30.0, ge=0)  # at most one open or raise per this
    per_hour: int = Field(default=6, ge=0)
    false_wakes_for_pause: int = Field(default=3, ge=1)  # this many with no command ...
    false_wake_window_s: float = Field(default=600.0, gt=0)  # ... within this ...
    pause_s: float = Field(default=1800.0, ge=0)  # ... pause raising for this long
    closed_quickly_s: float = Field(default=10.0, ge=0)  # closed this soon: a false wake


class WakeWord(BaseModel):
    phrase: str
    model: str | None = None
    threshold: float = 0.5
    # Other spellings speech-to-text may write for the whole phrase (matched as whole words).
    variants: list[str] = Field(default_factory=list)
    # Other spellings for one word of the phrase, e.g. {"hey": ["hay", "hi"]}: any mix of
    # them matches. Keys are words of `phrase` (any case).
    word_variants: dict[str, list[str]] = Field(default_factory=dict)
    # How people say it, as Kokoro phonemes: only for bench/wake_synthetic.py.
    test_pronunciations: list[str] = Field(default_factory=list)
