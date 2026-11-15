"""Timer settings, kept apart from config.py (which is near the 400-line limit)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TimeCues(BaseModel):
    """Words in the person's own text that let the server trust a field (one language).

    A number counts only where it stands as a time (B1, B3): a clock time after "at",
    "by" and the like, or before am/pm, "o'clock" or a period word ("7 in the evening");
    a delay after "in" or "for", or before a duration word ("5 minutes"); or anywhere in
    a message that is only a time ("Seven thirty.", "Ten minutes."). "2 pills" is no
    time. A `period` needs a matching am or pm word (or a 13-23 hour said as one); the
    hour must be one said; a delay's value must be one said. Otherwise the server drops
    the field or asks: it never keeps a field nobody said.
    """

    am_words: list[str] = Field(default_factory=lambda: ["am", "morning", "midnight"])
    pm_words: list[str] = Field(
        default_factory=lambda: ["pm", "afternoon", "evening", "tonight", "night", "noon",
                                 "midday"]
    )  # fmt: skip
    # Numbers said as words (tens and units add up: "twenty five" is 25).
    number_values: dict[str, int] = Field(
        default_factory=lambda: {
            "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
            "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
            "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
            "fifty": 50, "sixty": 60, "ninety": 90, "couple": 2, "dozen": 12,
        }
    )  # fmt: skip
    fraction_words: list[str] = Field(default_factory=lambda: ["half", "quarter"])
    # A number right after one of these is a clock time ("at 7", "half past six").
    clock_before: list[str] = Field(
        default_factory=lambda: ["at", "by", "around", "about", "until", "till", "til",
                                 "before", "from", "for", "past", "to"]
    )  # fmt: skip
    # A number right after one of these is a delay ("in 5", "for 10 minutes").
    delay_before: list[str] = Field(default_factory=lambda: ["in", "for", "after", "within"])
    # Words that may stand between a number and its period word ("7 in the evening").
    period_gap: list[str] = Field(default_factory=lambda: ["in", "the", "this", "at"])
    before_words: list[str] = Field(
        default_factory=lambda: ["to", "till", "til", "before"]  # "quarter to eight"
    )
    time_words: list[str] = Field(
        default_factory=lambda: ["noon", "midnight", "midday", "o'clock", "oclock"]
    )
    # A message with one of these is a request of its own, not an answer (B2).
    request_words: list[str] = Field(
        default_factory=lambda: ["remind", "reminder", "reminders", "alarm", "alarms",
                                 "timer", "timers", "wake", "set", "cancel", "snooze",
                                 "countdown"]
    )  # fmt: skip
    # Words a message that is only a time may also contain ("At 7, please.").
    filler_words: list[str] = Field(
        default_factory=lambda: ["at", "in", "for", "about", "around", "please", "ok",
                                 "okay", "um", "uh", "and", "a", "an", "the", "yes",
                                 "make", "it", "past", "to", "of", "say"]
    )  # fmt: skip
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
    # Typed and window turns: a reply claiming a timer change no tool made (truth.py).
    nothing_set: str = "Nothing was set."
    claim_words: list[str] = Field(
        default_factory=lambda: ["set", "created", "scheduled", "cancelled", "canceled",
                                 "snoozed", "started", "stopped", "added", "removed",
                                 "deleted", "changed", "moved", "done"]
    )  # fmt: skip
    timer_words: list[str] = Field(
        default_factory=lambda: ["timer", "timers", "alarm", "alarms", "reminder",
                                 "reminders", "countdown", "remind"]
    )  # fmt: skip
    negation_words: list[str] = Field(
        default_factory=lambda: ["not", "can't", "cannot", "unable", "don't", "won't", "no"]
    )
    missed_line: str = "You have {count} missed {things}."
