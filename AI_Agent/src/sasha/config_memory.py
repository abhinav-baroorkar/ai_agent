"""Memory settings (Dependencies/team/memory-design.md), kept apart from config.py.

Wording and word lists are English now; per language later, like the timers' cues.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MemoryConfig(BaseModel):
    enabled: bool = True
    max_items: int = Field(default=200, ge=1, le=1000)  # M6: refuse at the cap, never evict
    max_text: int = Field(default=300, ge=20, le=1000)
    max_per_turn: int = 5
    wake_max_per_window: int = 10  # voice-made notes per window (M1: all pending)
    wake_window_s: float = 600
    prompt_chars: int = 1500  # the prompt's note block, newest first (about 400 tokens)
    last_turns: int = 2  # "forget that": a note made this recently in this conversation
    answer_window_s: float = 120  # an answer ("yes") is read with its question this soon
    max_forget_minutes: int = 24 * 60

    # M4: a request to remember or forget, written broadly (the timers' T1 lesson).
    remember_phrases: list[str] = Field(
        default_factory=lambda: ["remember", "note that", "make a note", "take a note",
                                 "don't forget", "do not forget", "keep in mind",
                                 "save that", "keep track of", "jot down", "write down"]
    )  # fmt: skip
    forget_phrases: list[str] = Field(
        default_factory=lambda: ["forget", "delete that note", "delete the note",
                                 "delete my note", "delete my notes", "erase",
                                 "remove that note", "remove the note", "scratch that note",
                                 "clear my notes", "unremember"]
    )  # fmt: skip
    # ...but a question about notes is no request ("what do you remember about my bus?").
    question_starts: list[str] = Field(
        default_factory=lambda: ["what", "which", "when", "where", "who", "why", "how", "do",
                                 "does", "did", "is", "are", "have", "has"]
    )  # fmt: skip
    # "I remember the bus was late" is the owner's own memory, not a request (S4).
    self_subjects: list[str] = Field(
        default_factory=lambda: ["i", "we", "i'd", "we'd", "i'll", "we'll", "they", "he", "she"]
    )
    everything_words: list[str] = Field(default_factory=lambda: ["everything", "all"])
    yes_words: list[str] = Field(
        default_factory=lambda: ["yes", "yeah", "yep", "sure", "confirm", "confirmed", "ok",
                                 "okay", "do it", "go ahead"]
    )  # fmt: skip
    # A yes may carry these and nothing else ("Yes please."); "yes but..." is no yes (R1).
    courtesy_words: list[str] = Field(
        default_factory=lambda: ["please", "thanks", "thank", "you", "that's", "right", "fine"]
    )
    # R5: a note keeps exactly the owner's negations ("not", "never" and any "n't").
    negation_words: list[str] = Field(
        default_factory=lambda: ["not", "no", "never", "without", "nobody", "nothing", "none",
                                 "neither", "nor", "nowhere", "cannot"]
    )  # fmt: skip

    # M3: every content word of a note is the owner's own. These may be added or dropped
    # (and inflections, through the shared stemmer); nothing else.
    stop_words: list[str] = Field(
        default_factory=lambda: [
            "a", "an", "the", "is", "are", "was", "were", "be", "been", "am", "to", "of",
            "in", "on", "at", "for", "with", "by", "from", "and", "or", "but", "that",
            "this", "it", "its", "as", "so", "about", "into", "has", "have", "had", "does",
            "do", "did", "will", "would", "should", "can", "could", "there", "their", "they",
            "them", "then", "than", "also", "just", "please", "every", "each", "s",
        ]
    )  # fmt: skip
    # The rewrites a note may make: "I take the bus" -> "the owner takes the bus".
    owner_words: list[str] = Field(default_factory=lambda: ["owner", "owner's", "owners"])
    self_words: list[str] = Field(
        default_factory=lambda: ["i", "me", "my", "mine", "myself", "i'm", "i've", "i'll"]
    )

    # M7: never stored, and kept out of the history when refused.
    secret_words: list[str] = Field(
        default_factory=lambda: ["password", "passwords", "passcode", "passphrase", "pin",
                                 "pins", "otp", "cvv", "cvc", "iban", "token", "api key",
                                 "secret key", "private key", "one-time code",
                                 "security code", "card number", "account number",
                                 "routing number", "social security", "login code"]
    )  # fmt: skip
    secret_patterns: list[str] = Field(
        default_factory=lambda: [
            r"(?:\d[ -]?){7,}",  # a run of 7+ digits ("4111 1111 1111 1111")
            r"\b(?:code|pin|passcode)\W+(?:is\W+)?\d+",
            r"\b(?=[A-Za-z0-9_\-]{20,}\b)(?=\S*\d)(?=\S*[A-Za-z])[A-Za-z0-9_\-]+",  # key-like
            # S1: spoken digits ("four eight two one"), and a wifi or network password.
            r"\b(?:(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)\W+){3,}"
            r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)\b",
            r"\b(?:wi-?fi|wireless|network|router)\b[^.?!]*?(?:\bis\b|:)\s*\S+",
        ]
    )
    # S1: one of these anywhere with any number (digits or words) is a secret.
    code_words: list[str] = Field(
        default_factory=lambda: ["code", "codes", "combination", "combo", "pin", "pins",
                                 "passcode", "password", "passphrase", "lock code"]
    )  # fmt: skip
    email_pattern: str = r"[\w.+-]+@[\w-]+\.[\w.-]+"  # with "password": refused
    mask: str = "[removed]"

    # The server's own lines (the turn's truth check, memory/truth.py, and the tools).
    lines: dict[str, str] = Field(
        default_factory=lambda: {
            "not_saved": "I didn't save that.",
            "not_forgotten": "Nothing was forgotten.",
            "secret": "I don't store passwords, PINs, codes or long numbers.",
            "say_what": "Say what to remember.",
            "not_said": "I can only keep your own words. Say it the way you want it kept.",
            "full": "My notes are full; ask me to forget something first.",
            "not_asked": "I didn't save that.",
            "too_many": "That's a lot at once. Tell me one thing at a time.",
            "wake_limit": "I can't take more notes by voice right now.",
            "which_note": "Which note?",
            "no_notes": "There are no notes to forget.",
            "confirm_everything": "Forget all your notes? Say yes to confirm.",
            "on_screen": "Do that on the screen, please.",
            "bad_minutes": "For how many minutes?",
            "forgot": "Forgot {what}.",  # W1: the reply after a forget, from the counts
            "noted": "Noted: {text}.",  # S3: what was really kept, shown back
            "noted_short": "Noted.",  # ...spoken, when the text is long
            "and": " and ",
        }
    )
    # "{n} note(s)", "{n} earlier turn(s)" in the forget line.
    units: dict[str, list[str]] = Field(
        default_factory=lambda: {"notes": ["note", "notes"],
                                 "turns": ["earlier turn", "earlier turns"]}
    )  # fmt: skip
    spoken_noted_max: int = 120  # longer notes are "Noted." when spoken
