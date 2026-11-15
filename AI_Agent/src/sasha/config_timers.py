"""Timer settings, kept apart from config.py (which is near the 400-line limit)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TimeCues(BaseModel):
    """Words in the person's own text that let the server trust a field (one language).

    A `period` needs a matching am or pm word (or a 13-23 hour) in the words; a clock
    time needs a number or a time word, and its hour must be one of the numbers said; a
    delay needs a number or a duration word. Otherwise the server drops the field or
    asks: it never keeps a field nobody said.
    """

    am_words: list[str] = Field(default_factory=lambda: ["am", "morning", "midnight"])
    pm_words: list[str] = Field(
        default_factory=lambda: ["pm", "afternoon", "evening", "tonight", "night", "noon",
                                 "midday"]
    )  # fmt: skip
    # Hours said as words, for "the hour must be one that was said" (S5).
    hour_words: dict[str, int] = Field(
        default_factory=lambda: {
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
            "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "noon": 12,
            "midday": 12, "midnight": 12,
        }
    )  # fmt: skip
    before_words: list[str] = Field(
        default_factory=lambda: ["to", "till", "til", "before"]  # "quarter to eight"
    )
    number_words: list[str] = Field(
        default_factory=lambda: [
            "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
            "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
            "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty",
            "sixty", "ninety", "hundred", "half", "quarter", "couple", "dozen",
        ]
    )  # fmt: skip
    time_words: list[str] = Field(
        default_factory=lambda: ["noon", "midnight", "midday", "o'clock", "oclock"]
    )
    duration_words: list[str] = Field(
        default_factory=lambda: [
            "hour", "hours", "minute", "minutes", "min", "mins", "second", "seconds",
            "sec", "secs",
        ]
    )  # fmt: skip


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
    answer_window_s: float = 120  # an answer is read with its question only this soon
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
    # Per language; a cue from any configured language counts (English now).
    cues: dict[str, TimeCues] = Field(default_factory=lambda: {"en": TimeCues()})
    # Spoken lines (per language later, like wake.reply_lines).
    timer_done: str = "Timer done."
    timer_done_label: str = "Timer done: {label}."
    alarm_now: str = "It's {time}."
    reminder_now: str = "Reminder: {text}"
    voice_prefix: str = "Voice reminder: "
    missed_line: str = "You have {count} missed {things}."
