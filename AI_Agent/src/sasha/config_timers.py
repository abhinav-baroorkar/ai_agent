"""Timer settings, kept apart from config.py (which is near the 400-line limit)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TimersConfig(BaseModel):
    """Timers, alarms and reminders (server/timers/; Dependencies/team/timers-design.md)."""

    timezone: str = ""  # "" = the system's time zone
    max_active: int = Field(default=50, ge=1, le=500)
    max_timer_s: int = Field(default=24 * 3600, ge=60)
    max_ahead_days: int = Field(default=365, ge=1)
    max_text: int = Field(default=120, ge=10, le=500)
    late_fire_s: float = 12 * 3600  # timers/reminders up to this late still fire once
    alarm_late_s: float = 600  # alarms only if at most this late
    late_after_s: float = 60  # fired later than this (suspend): generic line only (D2)
    ring_wait_s: float = 30  # a ring waits at most this long for a turn's speech to end
    pending_keep_s: float = 12 * 3600  # undelivered items wait this long for a socket
    keep_done_days: float = 7  # missed/done items stay on the card
    check_every_s: float = 30  # the scheduler re-reads the clock at least this often
    tone_every_s: float = 10
    ring_s: float = 120
    alarm_ring_s: float = 300
    stop_window_s: float = 8
    bare_snooze_max: int = 3
    snooze_minutes: int = 5
    wake_max_per_window: int = 5  # items created by wake turns, per window
    wake_window_s: float = 600
    wake_max_active: int = 10
    stop_words: list[str] = Field(default_factory=lambda: ["stop", "dismiss", "snooze"])
    # Spoken lines (per language later, like wake.reply_lines).
    timer_done: str = "Timer done."
    timer_done_label: str = "Timer done: {label}."
    alarm_now: str = "It's {time}."
    reminder_now: str = "Reminder: {text}"
    voice_prefix: str = "Voice reminder: "
    missed_line: str = "You have {count} missed {things}."
