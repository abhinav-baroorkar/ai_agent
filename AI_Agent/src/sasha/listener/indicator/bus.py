"""The small part of D-Bus the indicator needs, behind an interface so tests can fake it.

The real one uses `jeepney` (pure Python). Everything on a bus is used from one thread.
"""

from __future__ import annotations

import contextlib
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

Body = tuple[Any, ...]


class BusError(Exception):
    """A call failed, timed out, or the connection is gone."""


@dataclass
class Incoming:
    """A method call made on us, or a signal we subscribed to."""

    kind: Literal["call", "signal"]
    sender: str
    path: str
    interface: str
    member: str
    body: Body
    reply: Callable[[str, Body], None] = lambda signature, body: None
    fail: Callable[[str, str], None] = lambda name, text: None


class Bus(Protocol):
    unique_name: str

    def call(
        self,
        dest: str,
        path: str,
        interface: str,
        member: str,
        signature: str = "",
        body: Body = (),
        timeout: float = 1.0,
    ) -> Body: ...

    def emit(
        self, path: str, interface: str, member: str, signature: str = "", body: Body = ()
    ) -> None: ...

    def add_match(self, rule: str) -> None: ...

    def poll(self, timeout: float) -> Incoming | None: ...

    def close(self) -> None: ...


class JeepneyBus:
    """A blocking session-bus connection."""

    def __init__(self) -> None:
        try:
            from jeepney.io.blocking import open_dbus_connection  # type: ignore[import-untyped]

            self._conn = open_dbus_connection(bus="SESSION")
        except Exception as exc:  # no jeepney, no session bus, refused
            raise BusError(f"No session bus ({type(exc).__name__})") from exc
        self.unique_name: str = self._conn.unique_name
        self._waiting: deque[Any] = (
            deque()
        )  # messages that arrived while a call waited for its reply

    def _post(self, message: Any) -> int:
        serial: int = next(self._conn.outgoing_serial)
        try:
            self._conn.send(message, serial=serial)
        except (OSError, EOFError) as exc:  # only a broken connection; a bad message is a bug
            raise BusError(f"Bus connection lost ({type(exc).__name__})") from exc
        return serial

    def _receive(self, timeout: float) -> Any | None:
        try:
            return self._conn.receive(timeout=max(0.0, timeout))
        except TimeoutError:
            return None
        except (OSError, EOFError) as exc:
            raise BusError(f"Bus connection lost ({type(exc).__name__})") from exc

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
        from jeepney import (  # type: ignore[import-untyped]
            DBusAddress,
            HeaderFields,
            MessageType,
            new_method_call,
        )

        address = DBusAddress(path, bus_name=dest, interface=interface)
        serial = self._post(new_method_call(address, member, signature or None, body))
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            message = self._receive(left)
            if message is None:
                break
            if message.header.fields.get(HeaderFields.reply_serial) != serial:
                self._waiting.append(message)
                continue
            if message.header.message_type == MessageType.error:
                raise BusError(str(message.header.fields.get(HeaderFields.error_name, "error")))
            return tuple(message.body)
        raise BusError(f"No answer from {dest} to {member}")

    def emit(
        self, path: str, interface: str, member: str, signature: str = "", body: Body = ()
    ) -> None:
        from jeepney import DBusAddress, new_signal

        self._post(
            new_signal(DBusAddress(path, interface=interface), member, signature or None, body)
        )

    def add_match(self, rule: str) -> None:
        self.call(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "AddMatch",
            "s",
            (rule,),
        )

    def poll(self, timeout: float) -> Incoming | None:
        from jeepney import HeaderFields, MessageType, new_error, new_method_return

        message = self._waiting.popleft() if self._waiting else self._receive(timeout)
        if message is None:
            return None
        kind = message.header.message_type
        if kind not in (MessageType.method_call, MessageType.signal):
            return None
        fields = message.header.fields

        def reply(signature: str, body: Body) -> None:
            self._post(new_method_return(message, signature or None, body))

        def fail(name: str, text: str) -> None:
            self._post(new_error(message, name, "s", (text,)))

        return Incoming(
            kind="call" if kind == MessageType.method_call else "signal",
            sender=str(fields.get(HeaderFields.sender, "")),
            path=str(fields.get(HeaderFields.path, "")),
            interface=str(fields.get(HeaderFields.interface, "")),
            member=str(fields.get(HeaderFields.member, "")),
            body=tuple(message.body),
            reply=reply,
            fail=fail,
        )

    def close(self) -> None:
        with contextlib.suppress(Exception):  # already gone
            self._conn.close()
