"""What the server adds to a bot's system prompt: facts, not persona.

- A capability section generated from the conversation's real tools: what it can do
  right now, what it can't (no internet without the browser, no weather or news, no
  timers until that tool exists), and an instruction never to claim anything else. In
  testing the model claimed "weather and news" and timers this build doesn't have.
- For spoken conversations (the Home window, the listener): plain spoken sentences,
  no lists, no markdown, no symbols. The server also cleans what is spoken
  (server/spoken.py); this keeps the model from writing it in the first place.
Wording comes from `profile.prompt_lines` per language (config_prompts.py).
"""

from __future__ import annotations

from collections.abc import Iterable

from sasha.config import Settings
from sasha.config_prompts import PromptLines


def prompt_lines(settings: Settings, language: str | None = None) -> PromptLines:
    lines = settings.profile.prompt_lines
    language = language or settings.assistant.languages.default
    return lines.get(language) or lines.get("en") or PromptLines()


def capability_section(tool_names: Iterable[str], lines: PromptLines) -> str:
    names = list(tool_names)
    can: list[str] = []
    for name in names:
        phrase = lines.abilities.get(name) or lines.unknown_tool.format(name=name)
        if phrase not in can:
            can.append(phrase)
    parts = [lines.can_head, *(f"- {p}" for p in can)] if can else [lines.no_tools]
    if lines.answer_freely:
        parts.append(lines.answer_freely)
    cannot = [limit.text for limit in lines.limits if not set(limit.unless_any) & set(names)]
    if cannot:
        parts += [lines.cannot_head, *(f"- {c}" for c in cannot)]
    parts.append(lines.never_claim)
    return "\n".join(parts)


def build_prompt(
    base: str, tool_names: Iterable[str], lines: PromptLines, spoken: bool = False
) -> str:
    """The bot's own prompt, then the capability facts, then (spoken) the style line."""
    text = f"{base.rstrip()}\n\n{capability_section(tool_names, lines)}"
    return f"{text}\n{lines.spoken_note}" if spoken else text
