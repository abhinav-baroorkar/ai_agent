"""The `browse` tool: the main assistant hands a web task to an isolated helper
(browser-design.md §10 with review conditions P1-P8 and the memory rules of §10.0a).

By construction, not by a filter:
- The helper's system prompt is `helper_prompt()`: fixed wording from
  `profile.prompt_lines`, never `build_prompt` (so no persona, no capability section,
  and no memory section once memory exists).
- Its messages are that prompt, the checked task, and its own tool results (P5). It is
  a fresh `Assistant` with no history; the main conversation's history, system prompt
  and `TurnContext.user_messages` never go into its requests.
- Its tools are the browser tools and nothing else: no home control, no memory.
- It runs inside the main turn (`inherit_turn`), so `open_url` and the search leak check
  use the owner's own messages, never the model-written task (B1, design 10.2).
- One browser holder per main conversation, kept across calls (P6).
- 75 s for the whole lookup (P7); on timeout its LLM call is cancelled too.
- The result is capped, cleaned and parsed; `sites` is the server's own record of the
  sites the tabs loaded (P4). The orchestrator wraps it as untrusted and marks the
  conversation tainted (P1).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from sasha.browser.origin import owner_addresses
from sasha.browser.pages import PERSON_CHECK
from sasha.browser.task_check import check_task, clean
from sasha.browser.tools import BrowserConversation, browser_tools
from sasha.config import Settings
from sasha.config_prompts import PromptLines
from sasha.conversation import current_turn
from sasha.orchestrator import Assistant, RequestFailed, TurnHooks
from sasha.pipeline.llm import LLM, LLMCancelled
from sasha.telemetry import TelemetryWriter
from sasha.tools import Tool, ToolError, ToolRegistry

BROWSE_TOOL = "browse"
BROWSE_TIMEOUT_S = 75.0  # inside the browser's 90 s per turn (P7)
HELPER_ROUNDS = 8
MAX_ANSWER, MAX_FACTS, MAX_FACT, MAX_SITES = 500, 3, 200, 5
TOO_LONG = "The web lookup took too long"
DESCRIPTION = (
    "Look something up on the web. Give a short, self-contained task in the owner's own "
    "words (what to find, and the address if the owner gave one). Returns a short "
    "answer with the sites it came from."
)


OPEN_FIRST = "Open {url} with open_url first; search only if that page doesn't answer it."


def named_address(task: str, owner_text: str) -> str | None:
    """An address the owner wrote in his current message that the task is about: the
    helper opens it rather than searching for it (B1 still checks it in open_url)."""
    for address in owner_addresses((owner_text,)):
        host = address.host.split(":", 1)[0]
        if host and host in task.lower():
            return address.url()
    return None


def helper_prompt(lines: PromptLines) -> str:
    """The helper's whole system prompt. Deliberately not `build_prompt`."""
    return lines.helper


def _site(value: Any, sites: list[str]) -> str:
    host = str(value or "").strip().lower()
    return host if host in sites else ""  # only a site the tabs really loaded (P4)


def shape_result(text: str, sites: list[str]) -> dict[str, Any]:
    """The helper's final reply as {answer, facts, sites}, capped and cleaned."""
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        data = {"answer": text}
    raw = data.get("facts")
    facts: list[Any] = raw if isinstance(raw, list) else []
    shaped = [
        {"text": clean(f.get("text", ""))[:MAX_FACT], "site": _site(f.get("site"), sites)}
        for f in facts[:MAX_FACTS]
        if isinstance(f, dict) and clean(f.get("text", ""))
    ]
    answer = clean(str(data.get("answer") or ""))[:MAX_ANSWER]
    return {"answer": answer, "facts": shaped, "sites": sites[:MAX_SITES]}


class BrowseHelper:
    """One main conversation's web lookups."""

    def __init__(
        self,
        conv: BrowserConversation,
        settings: Settings,
        llm: LLM,
        telemetry: TelemetryWriter,
        lines: PromptLines,
        timeout: float = BROWSE_TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.conv = conv
        self.settings = settings
        self.llm = llm
        self.telemetry = telemetry
        self.lines = lines
        self.timeout = timeout
        self.clock = clock

    def assistant(self) -> Assistant:
        """A fresh helper conversation: the browser tools only, no history."""
        return Assistant(
            self.settings,
            self.llm,
            ToolRegistry(browser_tools(self.conv)),
            self.telemetry,
            history_turns=0,
            system_prompt=helper_prompt(self.lines),
            max_rounds=HELPER_ROUNDS,
        )

    def browse(self, task: str) -> dict[str, Any]:
        turn = current_turn.get()
        if turn is None:
            raise ToolError("Web lookups can only be used in a conversation")
        if turn.input_mode == "wake":
            raise ToolError("Web lookups aren't available for wake-word requests")
        try:
            checked = check_task(str(task), turn.user_messages)
        except ValueError as e:
            raise ToolError(str(e)) from None
        deadline = self.clock() + self.timeout
        outer = turn.should_stop

        def stop() -> bool:
            return self.clock() >= deadline or bool(outer and outer())

        self.conv.sites, self.conv.page_words = [], set()
        self.conv.person_check = False
        self.conv.helper_task = checked  # its searches: the query-word rule (10.2)
        named = named_address(checked, turn.user_messages[-1] if turn.user_messages else "")
        if named is not None:  # the owner named a site: open it, don't search for it
            checked = f"{checked}\n{OPEN_FIRST.format(url=named)}"
        # Streaming with a quiet `on_delta`, so a timeout cancels the LLM call (P7).
        hooks = TurnHooks(on_delta=lambda _: None, should_stop=stop)
        try:
            reply = self.assistant().handle_text(checked, hooks, turn.input_mode, inherit_turn=True)
        except RequestFailed as e:
            if isinstance(e.__cause__, LLMCancelled):
                stopped = outer is not None and outer()
                raise ToolError("The owner stopped the request" if stopped else TOO_LONG) from None
            raise ToolError("The web lookup failed") from None
        result = shape_result(reply.text, list(self.conv.sites))
        if self.conv.person_check and not result["facts"] and not result["sites"]:
            # the search was blocked: say so in the owner's words, not "no results"
            result = {"answer": PERSON_CHECK, "facts": [], "sites": [], "blocked": "person_check"}
        return result


def browse_tool(helper: BrowseHelper) -> Tool:
    return Tool(
        BROWSE_TOOL,
        DESCRIPTION,
        {
            "type": "object",
            "properties": {"task": {"type": "string", "description": "What to look up"}},
            "required": ["task"],
        },
        helper.browse,
        read_only=True,  # reads the web; but its result is outside text:
        taints=True,  # changes need the owner's words while it is in the context (P1)
    )
