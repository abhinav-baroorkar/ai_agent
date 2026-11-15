"""The read-only clock tool: every bot has it, wake turns may use it."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sasha.config import Settings
from sasha.tools import ToolRegistry, build_tools
from sasha.tools.clock import CLOCK_TOOL, clock_tool


def test_clock_reports_local_date_and_time() -> None:
    fixed = datetime(2026, 11, 15, 9, 5, tzinfo=timezone(timedelta(hours=-5), "EST"))
    result = ToolRegistry([clock_tool(lambda: fixed)]).call(CLOCK_TOOL, {})
    assert result.ok
    assert '"date": "2026-11-15"' in result.content and '"time": "09:05"' in result.content
    assert '"weekday": "Sunday"' in result.content and '"timezone": "EST"' in result.content


def test_cli_tools_include_the_clock(settings: Settings) -> None:
    tools, _ = build_tools(settings)
    assert CLOCK_TOOL in tools


def test_wake_policy_keeps_the_clock() -> None:
    from sasha.config import WakeConfig
    from sasha.server.wake_policy import RateLimiter, WakeToolPolicy

    policy = WakeToolPolicy(WakeConfig(), lambda: False, RateLimiter(6), lambda a: False, print)
    assert CLOCK_TOOL in policy.wrap(ToolRegistry([clock_tool()])).names
