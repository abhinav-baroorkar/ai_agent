"""Settings for the app shell and the window opening on a wake (kept apart from
config.py, which is at its size limit)."""

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
