"""Truthful replies on wake turns: the server, not the model, says what did not change.

Two live findings (wake-server review, round 3): a mis-heard command ("<name> turned
off all the lights") made the model claim an action it never took, and after a refused
fourth light the model still said "all the lights are now off". The prompt line helps;
this is the guarantee:

- the model's streamed text is released one complete sentence at a time, and only
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

from sasha.config import ReplyLines, WakeConfig
from sasha.server.wake_policy import WakeToolPolicy

AVAILABLE = True  # this module is present; the device-control opt-in checks it
MAX_NAMES = 5


def wake_control_allowed(config: WakeConfig) -> bool:
    """Device control by voice needs truthful replies present and on (review gate)."""
    return AVAILABLE and config.truthful_replies


_SENTENCE_END = re.compile(r"[.!?]+(?:\s+|$)")
_CLEAN = re.compile(r"[^\w .-]+")


def device_name(domain: str, entity: str) -> str:
    """A short spoken name: `light.hallway` -> "hallway light"; `all` -> "all light devices"."""
    domain = _CLEAN.sub(" ", entity.split(".", 1)[0] if "." in entity else domain).strip()
    if entity in ("", "all"):
        return f"all {domain} devices"[:48]
    name = _CLEAN.sub(" ", entity.split(".", 1)[-1].replace("_", " ")).strip()[:40]
    return name if domain in name.split() else f"{name} {domain}".strip()


def names_text(pairs: list[tuple[str, str]], lines: ReplyLines) -> str:
    names = [device_name(d, e) for d, e in pairs]
    shown = names[:MAX_NAMES]
    text = lines.joiner.join(shown)
    if len(names) > MAX_NAMES:
        text += f"{lines.joiner}{lines.more.format(count=len(names) - MAX_NAMES)}"
    return text


def claims_change(text: str, lines: ReplyLines) -> bool:
    words = set(re.findall(r"[\w']+", text.lower()))
    return bool(words & {w.lower() for w in lines.claim_words})


class TruthfulReply:
    """One per wake turn. Feed it streamed text; ask it for the final reply."""

    def __init__(self, lines: ReplyLines, policy: WakeToolPolicy) -> None:
        self.lines = lines
        self.policy = policy
        self.pending = ""  # text of the sentence still being written
        self.released = ""  # text already sent and spoken
        self.held = ""  # complete sentences kept back until the turn ends
        self.holding = False
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
        policy, lines = self.policy, self.lines
        if policy.not_changed:
            parts = []
            if policy.changed:
                parts.append(lines.changed.format(names=names_text(policy.changed, lines)))
            parts.append(lines.not_changed.format(names=names_text(policy.not_changed, lines)))
            self.overridden = "reply_not_changed"
            return self._override(" ".join(parts))
        if not policy.changed and claims_change(tail, lines):
            self.overridden = "reply_nothing_changed"
            return self._override(lines.nothing_changed)
        return tail, model_text  # nothing to correct: the model's own words stand

    def _override(self, fixed: str) -> tuple[str, str]:
        spoken = fixed if not self.released.strip() else f" {fixed}"
        return spoken, f"{self.released}{spoken}".strip()
