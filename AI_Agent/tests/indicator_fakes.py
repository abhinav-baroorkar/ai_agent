"""A fake session bus and helpers for the indicator tests."""

from __future__ import annotations

import time
from typing import Any

from sasha.listener.indicator import Persona
from sasha.listener.indicator.bus import Body, BusError, Incoming
from sasha.listener.indicator.sni import WATCHER, SniItem

WHO = Persona(name="Testbot", wake_phrase="Hey Testbot")
ME = ":1.77"
TRAY = ":1.5"  # the bus name of the fake tray (it owns the watcher name)


class FakeBus:
    """A session bus with a tray watcher that tests can switch on and off."""

    unique_name = ME

    def __init__(self, watcher: bool = True, host: bool = True) -> None:
        self.watcher, self.host = watcher, host
        self.items: list[str] = []
        self.matches: list[str] = []
        self.emitted: list[tuple[str, str, str, Body]] = []
        self.incoming: list[Incoming] = []
        self.closed = False
        self.dead = False
        self.poll_error: Exception | None = None

    def call(
        self,
        dest: str,
        path: str,
        interface: str,
        member: str,
        signature: str = "",
        body: Body = (),
        timeout: float = 1.0,
    ) -> Body:
        if self.dead:
            raise BusError("Bus connection lost")
        if dest == "org.freedesktop.DBus" and member == "GetNameOwner" and self.watcher:
            return (TRAY,)
        if dest != WATCHER or not self.watcher:
            raise BusError("org.freedesktop.DBus.Error.ServiceUnknown")
        if member == "RegisterStatusNotifierItem":
            self.items.append(f"{ME}@{body[0]}")
            return ()
        if member == "Get" and body[1] == "IsStatusNotifierHostRegistered":
            return (("b", self.host),)
        if member == "Get" and body[1] == "RegisteredStatusNotifierItems":
            return (("as", list(self.items)),)
        raise BusError("UnknownMethod")

    def emit(
        self, path: str, interface: str, member: str, signature: str = "", body: Body = ()
    ) -> None:
        self.emitted.append((path, interface, member, body))

    def add_match(self, rule: str) -> None:
        if self.dead:
            raise BusError("Bus connection lost")
        self.matches.append(rule)

    def poll(self, timeout: float) -> Incoming | None:
        if self.dead:
            raise BusError("Bus connection lost")
        if self.poll_error is not None:
            raise self.poll_error
        if self.incoming:
            return self.incoming.pop(0)
        time.sleep(min(timeout, 0.002))
        return None

    def close(self) -> None:
        self.closed = True

    # --- what the desktop does ----------------------------------------------------
    def tray_vanishes(self) -> None:
        self.watcher = False
        self.items.clear()
        self.incoming.append(
            signal("org.freedesktop.DBus", "NameOwnerChanged", (WATCHER, ":1.5", ""))
        )


def signal(interface: str, member: str, body: Body) -> Incoming:
    return Incoming("signal", ":1.5", "/", interface, member, body)


class Call:
    """A method call on the item, keeping what was answered."""

    def __init__(
        self, path: str, interface: str, member: str, *body: Any, sender: str = TRAY
    ) -> None:
        self.replies: list[tuple[str, Body]] = []
        self.errors: list[str] = []
        self.msg = Incoming(
            "call", sender, path, interface, member, body,
            reply=lambda sig, b: self.replies.append((sig, b)),
            fail=lambda name, text: self.errors.append(name),
        )  # fmt: skip


def make_item(bus: FakeBus | None = None) -> tuple[SniItem, FakeBus, list[str]]:
    bus = bus or FakeBus()
    clicks: list[str] = []
    item = SniItem(bus, WHO, lambda: clicks.append("toggle"))
    item.setup()
    return item, bus, clicks
