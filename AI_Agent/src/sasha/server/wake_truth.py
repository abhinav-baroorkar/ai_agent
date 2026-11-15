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


def requests_change(command: str, lines: ReplyLines) -> bool:
    """Does the person's command ask for a device change? (T1)

    A change verb anywhere ("can you turn off the light?") is a request. Otherwise a
    state word next to a device word ("lights off") is one, unless the command is a
    question ("is the light on?"), so honest state answers still stream.
    """
    words = _words(command)
    if not words:
        return False
    if set(words) & {w.lower() for w in lines.change_verbs}:
        return True
    question = words[0] in {w.lower() for w in lines.question_words} or command.strip().endswith(
        "?"
    )
    if question:
        return False
    states = {w.lower() for w in lines.state_words}
    devices = {w.lower() for w in lines.device_words}
    at_state = [i for i, w in enumerate(words) if w in states]
    at_device = [i for i, w in enumerate(words) if w in devices]
    return any(abs(s - d) <= STATE_NEAR for s in at_state for d in at_device)


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
        self.change_requested = requests_change(command, lines)
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
        if not policy.changed and (self.change_requested or claims_change(tail, lines)):
            self.overridden = "reply_nothing_changed"
            return self._override(lines.nothing_changed)
        return tail, model_text  # nothing to correct: the model's own words stand

    def _override(self, fixed: str) -> tuple[str, str]:
        spoken = fixed if not self.released.strip() else f" {fixed}"
        return spoken, f"{self.released}{spoken}".strip()
