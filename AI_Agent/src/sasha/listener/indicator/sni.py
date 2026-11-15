"""The tray item itself: a StatusNotifierItem with a two-line menu, spoken over a `Bus`.

This class has no thread and no timing of its own, so tests can drive it step by step.
`tray.py` runs it. What the desktop's tray (the appindicator extension on GNOME) needs,
checked against its source: an `Id`, a `Menu` path (an item without a menu is never
shown), `IconPixmap`, and `Activate` in the introspection data. A single left click
opens the menu; a double click sends `Activate`. Both toggle pause.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sasha.listener.indicator.base import STATES, Persona, State, describe
from sasha.listener.indicator.bus import Body, Bus, BusError, Incoming
from sasha.listener.indicator.icons import pixmaps

WATCHER = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM = "org.kde.StatusNotifierItem"
ITEM_PATH = "/StatusNotifierItem"
MENU = "com.canonical.dbusmenu"
MENU_PATH = "/MenuBar"
PROPS = "org.freedesktop.DBus.Properties"
DBUS = "org.freedesktop.DBus"
STATUS_ROW, TOGGLE_ROW = 1, 2
PAUSE_LABEL = "Pause listening (turn the microphone off)"
RESUME_LABEL = "Resume listening (turn the microphone on)"
Variant = tuple[str, Any]


def _xml(
    interface: str,
    methods: dict[str, tuple[str, str]],
    props: dict[str, str],
    signals: dict[str, str],
) -> str:
    """Introspection data: methods as {name: (in types, out types)}, space separated."""
    parts = [f'<node><interface name="{interface}">']
    for name, (ins, outs) in methods.items():
        args = [f'<arg type="{t}" direction="in"/>' for t in ins.split()]
        args += [f'<arg type="{t}" direction="out"/>' for t in outs.split()]
        parts.append(f'<method name="{name}">{"".join(args)}</method>')
    parts += [f'<property name="{n}" type="{t}" access="read"/>' for n, t in props.items()]
    for name, types in signals.items():
        args = [f'<arg type="{t}"/>' for t in types.split()]
        parts.append(f'<signal name="{name}">{"".join(args)}</signal>')
    return "".join(parts) + "</interface></node>"


ITEM_XML = _xml(
    ITEM,
    {
        "Activate": ("i i", ""), "SecondaryActivate": ("i i", ""),
        "ContextMenu": ("i i", ""), "Scroll": ("i s", ""),
    },
    {
        "Category": "s", "Id": "s", "Title": "s", "Status": "s", "IconName": "s",
        "IconPixmap": "a(iiay)",
        "ToolTip": "(sa(iiay)ss)", "ItemIsMenu": "b", "Menu": "o",
    },
    {"NewIcon": "", "NewTitle": "", "NewToolTip": "", "NewStatus": "s"},
)  # fmt: skip
MENU_XML = _xml(
    MENU,
    {
        "GetLayout": ("i i as", "u (ia{sv}av)"), "GetGroupProperties": ("ai as", "a(ia{sv})"),
        "GetProperty": ("i s", "v"), "Event": ("i s v u", ""), "AboutToShow": ("i", "b"),
    },
    {"Version": "u", "Status": "s"},
    {"LayoutUpdated": "u i"},
)  # fmt: skip


class SniItem:
    def __init__(self, bus: Bus, who: Persona, on_pause_toggle: Callable[[], None]) -> None:
        self.bus = bus
        self.who = who
        self.on_pause_toggle = on_pause_toggle
        self.state: State = "offline"
        self.registered = False
        self.revision = 1

    # --- registration and "is it really shown" -----------------------------------
    def setup(self) -> None:
        """Subscribe to the tray's comings and goings, then register. Raises BusError."""
        self.bus.add_match(
            f"type='signal',sender='{DBUS}',interface='{DBUS}',member='NameOwnerChanged',arg0='{WATCHER}'"
        )
        self.bus.add_match(f"type='signal',interface='{WATCHER}'")
        self.register()

    def register(self) -> None:
        self.registered = False
        self.bus.call(
            WATCHER, WATCHER_PATH, WATCHER, "RegisterStatusNotifierItem", "s", (ITEM_PATH,)
        )
        self.registered = True

    def check_shown(self) -> bool:
        """Ask the bus: is a tray host there, and does it list our item? Never assumed."""
        if not self.registered:
            return False
        try:
            host = self.bus.call(
                WATCHER,
                WATCHER_PATH,
                PROPS,
                "Get",
                "ss",
                (WATCHER, "IsStatusNotifierHostRegistered"),
            )
            items = self.bus.call(
                WATCHER,
                WATCHER_PATH,
                PROPS,
                "Get",
                "ss",
                (WATCHER, "RegisteredStatusNotifierItems"),
            )
        except BusError:
            return False
        ours = self.bus.unique_name
        listed = any(
            isinstance(i, str) and i.startswith(ours) and i[len(ours) :][:1] in ("", "@", "/")
            for i in items[0][1]
        )
        return host[0][1] is True and listed

    # --- state ---------------------------------------------------------------------
    def set_state(self, state: State) -> None:
        if state not in STATES:
            raise ValueError(f"Unknown indicator state: {state!r}")
        if state == self.state:
            return
        self.state = state
        self.revision += 1
        for member in ("NewIcon", "NewTitle", "NewToolTip"):
            self.bus.emit(ITEM_PATH, ITEM, member)
        self.bus.emit(MENU_PATH, MENU, "LayoutUpdated", "ui", (self.revision, 0))

    def item_properties(self) -> dict[str, Variant]:
        title = describe(self.state, self.who)
        icons = list(pixmaps(self.state))  # D-Bus structs must be tuples
        return {
            "Category": ("s", "ApplicationStatus"),
            "Id": ("s", "local-voice-assistant-listener"),
            "Title": ("s", title),
            "Status": ("s", "Active"),
            "WindowId": ("i", 0),
            "IconName": ("s", ""),
            "IconPixmap": ("a(iiay)", icons),
            "OverlayIconName": ("s", ""),
            "OverlayIconPixmap": ("a(iiay)", []),
            "AttentionIconName": ("s", ""),
            "AttentionIconPixmap": ("a(iiay)", []),
            "AttentionMovieName": ("s", ""),
            "ToolTip": ("(sa(iiay)ss)", ("", [], title, "")),
            "ItemIsMenu": ("b", False),
            "Menu": ("o", MENU_PATH),
        }

    # --- the menu: one line saying what is happening, one line to pause or resume -----
    def row(self, row_id: int) -> dict[str, Variant]:
        if row_id == STATUS_ROW:
            return {"label": ("s", describe(self.state, self.who)), "enabled": ("b", False)}
        if row_id == TOGGLE_ROW:
            paused = self.state == "paused"
            return {"label": ("s", RESUME_LABEL if paused else PAUSE_LABEL)}
        return {"children-display": ("s", "submenu")}

    def layout(self) -> tuple[int, dict[str, Variant], list[Variant]]:
        rows: list[Variant] = [
            ("(ia{sv}av)", (i, self.row(i), [])) for i in (STATUS_ROW, TOGGLE_ROW)
        ]
        return (0, self.row(0), rows)

    # --- incoming ------------------------------------------------------------------
    def handle(self, msg: Incoming) -> bool:
        """Deal with one message. Returns False when it shows the tray has gone."""
        if msg.kind == "signal":
            return self._signal(msg)
        try:
            self._call(msg)
        except BusError:
            raise
        except Exception:  # a malformed call from another program must not stop the indicator
            msg.fail("org.freedesktop.DBus.Error.InvalidArgs", "Bad arguments")
        return True

    def _signal(self, msg: Incoming) -> bool:
        if msg.member == "NameOwnerChanged" and msg.body[:1] == (WATCHER,):
            if len(msg.body) == 3 and msg.body[2]:
                self._try_register()  # a tray (re)appeared
                return True
            self.registered = False
            return False
        if msg.interface != WATCHER:
            return True
        if msg.member == "StatusNotifierHostRegistered":
            self._try_register()
            return True
        if msg.member in ("StatusNotifierHostUnregistered", "StatusNotifierItemUnregistered"):
            return self.check_shown()
        return True

    def _try_register(self) -> None:
        try:
            self.register()
        except BusError:
            self.registered = False

    def _call(self, msg: Incoming) -> None:
        key = (msg.interface, msg.member)
        on_item = msg.path == ITEM_PATH
        if key == ("org.freedesktop.DBus.Introspectable", "Introspect"):
            msg.reply(
                "s", (ITEM_XML if on_item else MENU_XML if msg.path == MENU_PATH else "<node/>",)
            )
        elif key == ("org.freedesktop.DBus.Peer", "Ping"):
            msg.reply("", ())
        elif key in ((PROPS, "GetAll"), (PROPS, "Get")):
            props = self.item_properties() if on_item else self.menu_properties()
            if msg.member == "GetAll":
                msg.reply("a{sv}", (props,))
            elif msg.body[1] in props:
                msg.reply("v", (props[msg.body[1]],))
            else:
                msg.fail("org.freedesktop.DBus.Error.UnknownProperty", str(msg.body[1]))
        elif key in ((ITEM, "Activate"), (ITEM, "SecondaryActivate")):
            msg.reply("", ())
            if msg.member == "Activate":
                self.on_pause_toggle()
        elif key in ((ITEM, "ContextMenu"), (ITEM, "Scroll")):
            msg.reply("", ())
        elif msg.interface == MENU:
            self._menu_call(msg)
        else:
            msg.fail("org.freedesktop.DBus.Error.UnknownMethod", f"{msg.interface}.{msg.member}")

    def menu_properties(self) -> dict[str, Variant]:
        return {
            "Version": ("u", 3),
            "TextDirection": ("s", "ltr"),
            "Status": ("s", "normal"),
            "IconThemePath": ("as", []),
        }

    def _menu_call(self, msg: Incoming) -> None:
        body: Body = msg.body
        if msg.member == "GetLayout":
            msg.reply("u(ia{sv}av)", (self.revision, self.layout()))
        elif msg.member == "GetGroupProperties":
            ids = list(body[0]) or [0, STATUS_ROW, TOGGLE_ROW]
            msg.reply(
                "a(ia{sv})", ([(i, self.row(i)) for i in ids if i in (0, STATUS_ROW, TOGGLE_ROW)],)
            )
        elif msg.member == "GetProperty":
            msg.reply("v", (self.row(int(body[0])).get(str(body[1]), ("s", "")),))
        elif msg.member == "Event":
            msg.reply("", ())
            if body[0] == TOGGLE_ROW and body[1] == "clicked":
                self.on_pause_toggle()
        elif msg.member == "EventGroup":
            msg.reply("ai", ([],))
            if any(e[0] == TOGGLE_ROW and e[1] == "clicked" for e in body[0]):
                self.on_pause_toggle()
        elif msg.member == "AboutToShow":
            msg.reply("b", (False,))
        elif msg.member == "AboutToShowGroup":
            msg.reply("aiai", ([], []))
        else:
            msg.fail("org.freedesktop.DBus.Error.UnknownMethod", msg.member)
