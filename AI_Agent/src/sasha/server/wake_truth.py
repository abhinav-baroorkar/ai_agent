"""Truthful replies on wake turns: the server, not the model, says what did not change.

Two live findings (wake-server review, round 3): a mis-heard command ("<name> turned
off all the lights") made the model claim an action it never took, and after a refused
fourth light the model still said "all the lights are now off". The prompt line helps;
this is the guarantee:

- if the person's command asks for a change ("lights off"), all model text is held
  until the turn ends, whatever its wording (T1: "Okay, it's dark in there now.");
- otherwise the model's text is released one complete sentence at a time, and only
  while no device change has been attempted and the sentence claims none. From then
  on the rest is held until the turn ends (so nothing false is ever spoken);
- at the end, if any device change was refused, unconfirmed or failed, the held text
  is dropped and the server says its own sentence: "I changed: …" (if any) and
  "I didn't change: …", with names taken from the tool calls (cleaned, capped);
- if no device changed at all but the held text claims one, it says
  "Nothing was changed.";
- if a timer tool asked back (an unclear time) and nothing changed, the model's
  question stands, with its other sentences only if none claims a change (else
  "Nothing was changed." and the question, T9);
- otherwise the held text is released unchanged.
The sentences come from `wake.reply_lines` per language (config), not from code.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from sasha.config import ReplyLines, WakeConfig
from sasha.server.wake_policy import WakeToolPolicy

AVAILABLE = True  # this module is present; the device-control opt-in checks it
MAX_NAMES = 5


def wake_control_allowed(config: WakeConfig) -> bool:
    """Device control by voice needs truthful replies present and on (review gate)."""
    return AVAILABLE and config.truthful_replies


_SENTENCE_END = re.compile(r"[.!?]+(?:\s+|$)")
STATE_NEAR = 3  # a state word within this many words of a device word
SHORT_COMMAND = 3  # a command this short with any state word is a request ("off.")


def names_text(
    pairs: list[tuple[str, str]], lines: ReplyLines, label: Callable[[str, str], str | None]
) -> str:
    """Spoken names, only from exposed entities' real names (T3): `label` returns None
    for anything the house doesn't have, which becomes "a device I don't know"."""
    names: list[str] = []
    unknown = 0
    for domain, entity in pairs:
        name = label(domain, entity)
        if name is None:
            unknown += 1
        elif name not in names:
            names.append(name)
    if unknown:
        names.append(
            lines.unknown_one if unknown == 1 else lines.unknown_many.format(count=unknown)
        )
    shown = names[:MAX_NAMES]
    text = lines.joiner.join(shown)
    if len(names) > MAX_NAMES:
        text += f"{lines.joiner}{lines.more.format(count=len(names) - MAX_NAMES)}"
    return text


def _words(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower().replace(chr(0x2019), "'"))


def claims_change(text: str, lines: ReplyLines) -> bool:
    return bool(set(_words(text)) & {w.lower() for w in lines.claim_words})


def _is_verb(word: str, verbs: set[str]) -> bool:
    """A change verb, also as "-ing" ("turning", "dimming", "cutting")."""
    if word in verbs:
        return True
    if word.endswith("ing") and len(word) > 5:
        stem = word[:-3]
        return stem in verbs or stem[:-1] in verbs or f"{stem}e" in verbs
    return False


def _informational(command: str, words: list[str], lines: ReplyLines) -> bool:
    """A wh-question that asks for information, not a change (narrow on purpose, P1).

    Not one: a suggestion ("how about", "why don't you", "what if"), or a later clause
    (after a comma or "and") that starts with a change verb ("where's the fan, turn it
    on"). A miss costs a possibly false claim; a false positive only the plain line.
    """
    if not words or words[0] not in {w.lower() for w in lines.wh_words}:
        return False
    head = " ".join(words[:3])
    if any(head.startswith(s) for s in lines.suggestion_starts):
        return False
    verbs = {w.lower() for w in lines.change_verbs}
    for clause in re.split(r",|\band\b", command.lower())[1:]:
        first = [w for w in _words(clause) if w not in ("please", "you", "just")][:1]
        if first and _is_verb(first[0], verbs):
            return False
    return True


def requests_change(
    command: str, lines: ReplyLines, scenes: Callable[[], list[str]] | None = None
) -> bool:
    """Does the person's command ask for a device change? (T1, P1)

    Requests: a change verb ("can you turn off the light?", "how about turning it
    off"), a comparative ("a bit dimmer please"), a device word with a number or
    "percent", a state word next to a device word ("lights off"), any short command
    with a state word ("off."), or a bare scene name ("goodnight"). Informational
    wh-questions ("how do I turn off the TV?") and yes/no questions about state ("is
    the light on?") are not, so honest answers still stream.
    """
    words = _words(command)
    if not words or _informational(command, words, lines):
        return False
    verbs = {w.lower() for w in lines.change_verbs}
    if any(_is_verb(w, verbs) for w in words):
        return True
    if set(words) & {w.lower() for w in lines.comparatives}:
        return True
    devices = {w.lower() for w in lines.device_words}
    numbers = {w.lower() for w in lines.number_words}
    has_number = any(w.isdigit() or w in numbers for w in words)
    if has_number and set(words) & devices:
        return True  # "lights at fifty percent"
    # A yes/no question about state ("is the light on?"). Wh-words were already judged
    # above, narrowly, so a suggestion like "how about lights off" isn't one.
    yes_no = {w.lower() for w in lines.question_words} - {w.lower() for w in lines.wh_words}
    if words[0] in yes_no or command.strip().endswith("?"):
        return False
    states = {w.lower() for w in lines.state_words}
    if len(words) <= SHORT_COMMAND and set(words) & states:
        return True  # "off." after an armed name, "all off", "lights out please"
    at_state = [i for i, w in enumerate(words) if w in states]
    at_device = [i for i, w in enumerate(words) if w in devices]
    if any(abs(s - d) <= STATE_NEAR for s in at_state for d in at_device):
        return True
    if scenes is not None and len(words) <= SHORT_COMMAND:
        said = " ".join(words)
        return any(said == " ".join(_words(name)) for name in scenes())  # "goodnight"
    return False


class TruthfulReply:
    """One per wake turn. Feed it streamed text; ask it for the final reply."""

    def __init__(self, lines: ReplyLines, policy: WakeToolPolicy, command: str = "") -> None:
        self.lines = lines
        self.policy = policy
        self.pending = ""  # text of the sentence still being written
        self.released = ""  # text already sent and spoken
        self.held = ""  # complete sentences kept back until the turn ends
        # T1: a command that asks for a change holds all model text from the start, so
        # "The lights are off now." can't be spoken before the turn shows a change.
        self.change_requested = requests_change(command, lines, policy.scene_names)
        self.holding = self.change_requested
        self.overridden: str | None = None  # telemetry label when the server spoke

    def feed(self, piece: str) -> str:
        """Streamed model text in; the part that may be sent and spoken now out."""
        self.pending += piece
        out = ""
        while (m := _SENTENCE_END.search(self.pending)) is not None:
            sentence, self.pending = self.pending[: m.end()], self.pending[m.end() :]
            out += self._judge(sentence)
        return out

    def _judge(self, sentence: str) -> str:
        if not self.holding and (self.policy.attempted or claims_change(sentence, self.lines)):
            self.holding = True
        if self.holding:
            self.held += sentence
            return ""
        self.released += sentence
        return sentence

    def finish(self, model_text: str) -> tuple[str, str]:
        """(text still to send and speak, the final reply text)."""
        tail = self.held + self.pending
        self.pending = self.held = ""
        policy, lines, label = self.policy, self.lines, self.policy.device_label
        if policy.not_changed:
            parts = []
            if policy.changed:
                parts.append(lines.changed.format(names=names_text(policy.changed, lines, label)))
            parts.append(
                lines.not_changed.format(names=names_text(policy.not_changed, lines, label))
            )
            self.overridden = "reply_not_changed"
            return self._override(" ".join(parts))
        if not policy.changed and getattr(policy, "asked", False):
            fixed = self._asked_back(tail)
            if fixed == "":
                return tail, model_text  # an honest question back: it stands
            if fixed is not None:
                self.overridden = "reply_nothing_changed"
                return self._override(fixed)
        if not policy.changed and (self.change_requested or claims_change(tail, lines)):
            self.overridden = "reply_nothing_changed"
            return self._override(lines.nothing_changed)
        return tail, model_text  # nothing to correct: the model's own words stand

    def _asked_back(self, tail: str) -> str | None:
        """A timer tool asked back (T9). The model's question stands, but only with
        statements that claim nothing: "Done, the lights are off. Morning or evening?"
        becomes "Nothing was changed. Morning or evening?". Returns None if the model
        asked nothing, "" if its text is honest, else the text to say instead."""
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", tail.strip()) if s]
        questions = [s for s in sentences if s.endswith("?")]
        if not questions:
            return None
        statements = [s for s in sentences if not s.endswith("?")]
        if not any(claims_change(s, self.lines) for s in statements):
            return ""
        return f"{self.lines.nothing_changed} {questions[-1]}"

    def _override(self, fixed: str) -> tuple[str, str]:
        spoken = fixed if not self.released.strip() else f" {fixed}"
        return spoken, f"{self.released}{spoken}".strip()
