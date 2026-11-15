"""An oracle for "is a microphone child alive?" that doesn't trust the product's code.

It wraps `asyncio.create_subprocess_exec` (a child exists from the moment the spawn is
called, before any pid is known) and `os.kill` in the capture module (a SIGKILL that
was really sent to that pid). A child is gone once asyncio has reaped it. Only children
whose command line carries the rig's marker (the fake mic) are tracked.
"""

from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass, field
from typing import Any

import pytest


@dataclass
class Child:
    proc: Any = None  # the asyncio Process, once the spawn has returned
    sigkilled: bool = False

    @property
    def gone(self) -> bool:
        return self.proc is not None and self.proc.returncode is not None


@dataclass
class ChildOracle:
    marker: str
    children: list[Child] = field(default_factory=list)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_spawn = asyncio.create_subprocess_exec
        real_kill = os.kill
        oracle = self

        async def spawn(*args: Any, **kwargs: Any) -> Any:
            if not any(oracle.marker in str(a) for a in args):
                return await real_spawn(*args, **kwargs)
            child = Child()
            oracle.children.append(child)  # exists from here, before any pid
            try:
                child.proc = await real_spawn(*args, **kwargs)
            except BaseException:
                oracle.children.remove(child)  # asyncio killed and reaped it already
                raise
            return child.proc

        def kill(pid: int, sig: int) -> None:
            if sig == signal.SIGKILL:
                for child in oracle.children:
                    if child.proc is not None and child.proc.pid == pid:
                        child.sigkilled = True
            real_kill(pid, sig)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr("sasha.listener.capture.os.kill", kill)

    def unsafe(self) -> bool:
        """A fake-mic child exists that is neither gone nor really SIGKILLed."""
        return any(not c.gone and not c.sigkilled for c in self.children)
