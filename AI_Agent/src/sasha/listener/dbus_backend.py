"""The real guard backend over D-Bus with `jeepney` (MIT, pure Python).

Session bus: GNOME's screen saver (lock state). System bus: logind (the user's
graphical session and sleep) and UPower (battery). The sleep inhibitor needs file
descriptor passing, which jeepney only offers on its blocking connection, so that one
call runs on its own small connection.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from typing import Any

from sasha.listener.guards import GuardEvent

LOGIND = "org.freedesktop.login1"
TIMEOUT_S = 3.0


def _addr(path: str, bus_name: str, interface: str) -> Any:
    from jeepney import DBusAddress

    return DBusAddress(path, bus_name=bus_name, interface=interface)


def _value(body: Any) -> Any:
    """The value inside a Properties.Get reply: ((signature, value),)."""
    return body[0][1]


class DBusBackend:
    def __init__(self) -> None:
        self._session: Any = None
        self._system: Any = None
        self._stack = contextlib.AsyncExitStack()
        self._session_path: str | None = None
        self._inhibitor: Any = None

    async def connect(self) -> None:
        from jeepney.io.asyncio import open_dbus_router

        self._session = await self._stack.enter_async_context(open_dbus_router("SESSION"))
        self._system = await self._stack.enter_async_context(open_dbus_router("SYSTEM"))
        self._session_path = await self._graphical_session()

    async def _call(self, router: Any, msg: Any) -> Any:
        from jeepney import MessageType

        reply = await asyncio.wait_for(router.send_and_get_reply(msg), TIMEOUT_S)
        if reply.header.message_type != MessageType.method_return:
            raise RuntimeError(f"D-Bus error {reply.header.fields.get(4, 'unknown')}")
        return reply.body

    async def _get(self, router: Any, addr: Any, prop: str) -> Any:
        from jeepney import Properties

        return _value(await self._call(router, Properties(addr).get(prop)))

    async def _graphical_session(self) -> str:
        """The user's graphical session (logind User.Display); a systemd user service
        is not inside a session itself, so GetSessionByPID would fail there."""
        from jeepney import new_method_call

        manager = _addr("/org/freedesktop/login1", LOGIND, f"{LOGIND}.Manager")
        body = await self._call(
            self._system, new_method_call(manager, "GetUser", "u", (os.getuid(),))
        )
        user = _addr(body[0], LOGIND, f"{LOGIND}.User")
        _sid, path = await self._get(self._system, user, "Display")
        if not path or path == "/":
            raise RuntimeError("no graphical session")
        return str(path)

    async def screen_locked(self) -> bool:
        from jeepney import new_method_call

        saver = _addr("/org/gnome/ScreenSaver", "org.gnome.ScreenSaver", "org.gnome.ScreenSaver")
        body = await self._call(self._session, new_method_call(saver, "GetActive"))
        return bool(body[0])

    async def session_active(self) -> bool:
        assert self._session_path is not None
        session = _addr(self._session_path, LOGIND, f"{LOGIND}.Session")
        return bool(await self._get(self._system, session, "Active"))

    async def on_battery(self) -> bool:
        upower = _addr(
            "/org/freedesktop/UPower", "org.freedesktop.UPower", "org.freedesktop.UPower"
        )
        return bool(await self._get(self._system, upower, "OnBattery"))

    def take_sleep_inhibitor(self) -> None:
        from jeepney import new_method_call
        from jeepney.io.blocking import open_dbus_connection

        if self._inhibitor is not None:
            return
        manager = _addr("/org/freedesktop/login1", LOGIND, f"{LOGIND}.Manager")
        msg = new_method_call(
            manager,
            "Inhibit",
            "ssss",
            ("sleep", "sasha-listen", "Close the microphone before sleep", "delay"),
        )
        with open_dbus_connection("SYSTEM", enable_fds=True) as conn:
            reply = conn.send_and_get_reply(msg, timeout=TIMEOUT_S)
        self._inhibitor = reply.body[0]  # a jeepney FileDescriptor; closing it releases

    def release_sleep_inhibitor(self) -> None:
        fd, self._inhibitor = self._inhibitor, None
        if fd is not None:
            fd.close()

    async def events(self) -> AsyncIterator[GuardEvent]:
        from jeepney import MatchRule, message_bus
        from jeepney.io.asyncio import Proxy

        assert self._session_path is not None
        rules = [
            (
                self._session,
                "lock",
                MatchRule(
                    type="signal",
                    interface="org.gnome.ScreenSaver",
                    member="ActiveChanged",
                    path="/org/gnome/ScreenSaver",
                ),
            ),
            (
                self._system,
                "sleep",
                MatchRule(
                    type="signal",
                    interface=f"{LOGIND}.Manager",
                    member="PrepareForSleep",
                    path="/org/freedesktop/login1",
                ),
            ),
            (
                self._system,
                "active",
                MatchRule(
                    type="signal",
                    interface="org.freedesktop.DBus.Properties",
                    member="PropertiesChanged",
                    path=self._session_path,
                ),
            ),
            (
                self._system,
                "battery",
                MatchRule(
                    type="signal",
                    interface="org.freedesktop.DBus.Properties",
                    member="PropertiesChanged",
                    path="/org/freedesktop/UPower",
                ),
            ),
        ]
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        async with contextlib.AsyncExitStack() as stack:
            for router, kind, rule in rules:
                await asyncio.wait_for(Proxy(message_bus, router).AddMatch(rule), TIMEOUT_S)
                inner: asyncio.Queue[Any] = asyncio.Queue()
                stack.enter_context(router.filter(rule, queue=inner))
                stack.push_async_callback(
                    _cancel, asyncio.create_task(_forward(kind, inner, queue))
                )
            while True:
                kind, msg = await queue.get()
                if (event := _to_event(kind, msg.body)) is not None:
                    yield event

    async def close(self) -> None:
        self.release_sleep_inhibitor()
        await self._stack.aclose()


async def _forward(
    kind: str, inner: asyncio.Queue[Any], out: asyncio.Queue[tuple[str, Any]]
) -> None:
    while True:
        out.put_nowait((kind, await inner.get()))


async def _cancel(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def _to_event(kind: str, body: Any) -> GuardEvent | None:
    if kind in ("lock", "sleep"):
        return GuardEvent(kind, bool(body[0]))
    changed = body[1] if len(body) > 1 else {}
    key = "Active" if kind == "active" else "OnBattery"
    if key in changed:
        return GuardEvent(kind, bool(changed[key][1]))
    return None
