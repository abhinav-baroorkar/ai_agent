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
    nothing_yet: str = "Nothing is set yet."  # ...when the reply only offered or asked
    nothing_changed: str = "Nothing was changed."  # ...after a stop or cancel request
    # When a timer tool asked the person back this turn, the server asks its own fixed
    # question for the reason (the tool's ask label; "tool.reason" wins over "reason"):
    # replacing a held spoken reply, or after a typed one that doesn't ask. Empty: no
    # question ("none_active": there is nothing to change, the line says so).
    ask_lines: dict[str, str] = Field(
        default_factory=lambda: {
            "ambiguous_period": "Morning or evening?",
            "missing_time": "For what time?",
            "set_timer.missing_time": "For how long?",
            "not_said": "For what time?",
            "set_timer.not_said": "For how long?",
            "set_reminder.not_said": "When should I remind you?",
            "bad_day": "For which day?",
            "next_weekday": "This {weekday} or the one after?",
            "past": "That time has passed. For what time?",
            "ambiguous_item": "Which one?",
            "unknown": "Which one?",
            "missing_text": "What should the reminder say?",
            "bad_value": "What time should it be?",
            "none_active": "",
        }
    )  # fmt: skip
    # An ask with no line above, and a held spoken request reply the server replaces
    # with no tool having asked: "Nothing was set. What time should it be?".
    ask_generic: str = "What time should it be?"
    ask_generic_timer: str = "For how long?"  # ...when the request names a timer
    timer_only_words: list[str] = Field(default_factory=lambda: ["timer", "timers", "countdown"])
    # ...but not after a stop or cancel request ("turn off the alarm"): the line alone.
    cancel_words: list[str] = Field(
        default_factory=lambda: ["cancel", "stop", "off", "delete", "remove", "dismiss",
                                 "clear", "snooze"]
    )  # fmt: skip
    claim_words: list[str] = Field(
        default_factory=lambda: ["set", "created", "scheduled", "cancelled", "canceled",
                                 "snoozed", "started", "stopped", "added", "removed",
                                 "deleted", "changed", "moved", "done"]
    )  # fmt: skip
    timer_words: list[str] = Field(
        default_factory=lambda: ["timer", "timers", "alarm", "alarms", "reminder",
                                 "reminders", "countdown", "remind"]
    )  # fmt: skip
    # A turn is a timer request (Q2) if it has a timer verb ("wake me at 7", "ping me in
    # 20"; "time" only as a verb: "time 10 minutes"), or a timer word together with a
    # request verb or phrase ("set", "turn off") or a time expression (a number, "in 5",
    # "tonight", "minutes"). "Thanks for the reminder" and "tell me about timers" aren't.
    timer_verbs: list[str] = Field(
        default_factory=lambda: ["wake", "remind", "snooze", "nudge", "ping", "alert"]
    )
    request_verbs: list[str] = Field(
        default_factory=lambda: ["set", "start", "make", "need", "want", "give", "cancel",
                                 "stop", "delete", "remove", "change", "move", "schedule",
                                 "add", "create", "clear", "dismiss", "reset", "put"]
    )  # fmt: skip
    request_phrases: list[str] = Field(
        default_factory=lambda: ["turn off", "switch off", "shut off"]
    )
    time_expression_words: list[str] = Field(
        default_factory=lambda: ["tonight", "tomorrow", "noon", "midnight", "o'clock", "am",
                                 "pm", "half", "quarter", "hour", "hours", "minute",
                                 "minutes", "min", "mins", "second", "seconds"]
    )  # fmt: skip
    # ...unless it is a status question: it ends in "?" and starts with one of these
    # ("how long is left on my timer?"). "Can you ...?" is still a request.
    status_starts: list[str] = Field(
        default_factory=lambda: ["what", "which", "when", "where", "who", "why", "how",
                                 "is", "are", "do", "does", "did", "has", "have", "was",
                                 "were", "what's", "when's", "how's", "where's", "who's",
                                 "what're", "how many", "how long"]
    )  # fmt: skip
    # A claim word right after one of these is an offer ("I'd be happy to set a timer"):
    # it only picks the wording ("Nothing is set yet." instead of "Nothing was set.").
    offer_words: list[str] = Field(
        default_factory=lambda: ["happy to", "glad to", "want me to", "can", "could", "may",
                                 "might"]
    )  # fmt: skip
    # On a request turn that sets nothing, a spoken reply's last question is kept after
    # the line only if it asks for one of these ("What time?"); else the fixed question.
    asking_words: list[str] = Field(
        default_factory=lambda: ["time", "when", "long", "morning", "evening", "afternoon",
                                 "night", "am", "pm", "which", "day", "date", "minutes",
                                 "hours", "say"]
    )  # fmt: skip
    # A status question ("is my alarm set?") answered without list_timers: the server
    # says the real state instead (status.py). Reminder text is never read out.
    status_none: dict[str, str] = Field(
        default_factory=lambda: {
            "alarm": "You have no alarms set.", "timer": "You have no timers running.",
            "reminder": "You have no reminders.",
            "all": "You have no timers, alarms or reminders.",
        }
    )  # fmt: skip
    # ...and only when it is about the owner's own items: one of these is in it.
    status_words: list[str] = Field(
        default_factory=lambda: ["my", "any", "have", "left", "set", "running", "active",
                                 "next", "upcoming", "pending", "ringing"]
    )  # fmt: skip
    status_kinds: dict[str, str] = Field(
        default_factory=lambda: {"timer": "Timer", "alarm": "Alarm", "reminder": "Reminder"}
    )
    status_at: str = "{kind}: {when}."
    status_left: str = "{kind}: {left} left."
    status_ringing: str = "{kind}: ringing now."
    status_more: str = "And {count} more."
    status_today: str = "today"
    status_tomorrow: str = "tomorrow"
    negation_words: list[str] = Field(
        default_factory=lambda: ["not", "can't", "cannot", "unable", "don't", "won't", "no"]
    )
    missed_line: str = "You have {count} missed {things}."
