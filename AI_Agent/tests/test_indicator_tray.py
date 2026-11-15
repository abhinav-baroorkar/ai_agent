"""The tray item over a fake bus, and the running indicator: clicks and failing closed."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from sasha.listener.indicator import STATES, describe
from sasha.listener.indicator.bus import BusError
from sasha.listener.indicator.icons import pixmaps
from sasha.listener.indicator.sni import ITEM, ITEM_PATH, MENU, MENU_PATH, PROPS, WATCHER, SniItem
from sasha.listener.indicator.tray import TrayIndicator
from tests.indicator_fakes import ME, TRAY, WHO, Call, FakeBus, make_item, signal


# --- the tray item over a fake bus -------------------------------------------------------
def test_registers_and_reports_shown_only_when_the_bus_says_so() -> None:
    item, bus, _ = make_item()
    assert bus.items == [f"{ME}@{ITEM_PATH}"]
    assert any("NameOwnerChanged" in m for m in bus.matches)
    assert item.check_shown() is True
    bus.host = False  # a watcher but no tray drawing it
    assert item.check_shown() is False
    bus.host, bus.items = True, [":1.770@/StatusNotifierItem"]  # someone else's item, similar name
    assert item.check_shown() is False


def test_watcher_absent_means_not_available() -> None:
    bus = FakeBus(watcher=False)
    item = SniItem(bus, WHO, lambda: None)
    with pytest.raises(BusError):
        item.setup()
    assert item.registered is False and item.check_shown() is False


def test_state_changes_icon_title_and_menu() -> None:
    item, bus, _ = make_item()
    item.set_state("waiting")
    members = [m for _, _, m, _ in bus.emitted]
    assert {"NewIcon", "NewTitle", "NewToolTip", "LayoutUpdated"} <= set(members)
    props = item.item_properties()
    assert props["IconPixmap"][1][1][2] == pixmaps("waiting")[1][2]
    assert props["Title"][1] == describe("waiting", WHO) == props["ToolTip"][1][2]
    assert props["Menu"] == ("o", MENU_PATH) and props["IconName"] == ("s", "")
    assert item.row(2)["label"] == ("s", "Pause listening (turn the microphone off)")
    item.set_state("paused")
    assert item.row(2)["label"] == ("s", "Resume listening (turn the microphone on)")
    assert item.item_properties()["IconPixmap"][1][1][2] == pixmaps("paused")[1][2]
    before = len(bus.emitted)
    item.set_state("paused")  # no change, nothing sent
    assert len(bus.emitted) == before


def test_activate_and_menu_click_both_toggle_pause() -> None:
    item, _, clicks = make_item()
    call = Call(ITEM_PATH, ITEM, "Activate", 10, 10)
    assert item.handle(call.msg) and call.replies == [("", ())]
    assert clicks == ["toggle"]
    item.handle(Call(MENU_PATH, MENU, "Event", 2, "clicked", ("i", 0), 0).msg)
    assert clicks == ["toggle", "toggle"]
    item.handle(Call(MENU_PATH, MENU, "Event", 1, "clicked", ("i", 0), 0).msg)  # the status line
    item.handle(Call(MENU_PATH, MENU, "Event", 2, "hovered", ("i", 0), 0).msg)
    item.handle(Call(ITEM_PATH, ITEM, "SecondaryActivate", 1, 1).msg)
    assert clicks == ["toggle", "toggle"]


def test_clicks_from_any_other_program_are_refused() -> None:
    """Only the tray may toggle: nobody else can switch listening back on."""
    item, bus, clicks = make_item()
    assert item.tray == TRAY
    stranger = ":1.99"
    attempts = [
        Call(ITEM_PATH, ITEM, "Activate", 1, 1, sender=stranger),
        Call(MENU_PATH, MENU, "Event", 2, "clicked", ("i", 0), 0, sender=stranger),
        Call(MENU_PATH, MENU, "EventGroup", [(2, "clicked", ("i", 0), 0)], sender=stranger),
        Call(ITEM_PATH, ITEM, "Activate", 1, 1, sender=ME),  # not even ourselves
    ]
    for attempt in attempts:
        assert item.handle(attempt.msg) is True
        assert attempt.errors == ["org.freedesktop.DBus.Error.UnknownMethod"]
        assert not attempt.replies
    assert clicks == []
    item.handle(Call(MENU_PATH, MENU, "EventGroup", [(2, "clicked", ("i", 0), 0)]).msg)
    assert clicks == ["toggle"]
    bus.tray_vanishes()  # no tray: nobody's clicks count
    item.handle(bus.incoming.pop(0))
    gone = Call(ITEM_PATH, ITEM, "Activate", 1, 1)
    item.handle(gone.msg)
    assert gone.errors and clicks == ["toggle"]


def test_what_the_tray_asks_for_is_answered() -> None:
    item, _, _ = make_item()
    xml = Call(ITEM_PATH, "org.freedesktop.DBus.Introspectable", "Introspect")
    item.handle(xml.msg)
    assert '<method name="Activate">' in xml.replies[0][1][0]  # the tray checks for this
    everything = Call(ITEM_PATH, PROPS, "GetAll", ITEM)
    item.handle(everything.msg)
    assert everything.replies[0][0] == "a{sv}" and "IconPixmap" in everything.replies[0][1][0]
    layout = Call(MENU_PATH, MENU, "GetLayout", 0, -1, [])
    item.handle(layout.msg)
    revision, (root, _, rows) = layout.replies[0][1]
    assert root == 0 and [r[1][0] for r in rows] == [1, 2] and revision >= 1
    missing = Call(ITEM_PATH, PROPS, "Get", ITEM, "Nope")
    item.handle(missing.msg)
    assert missing.errors == ["org.freedesktop.DBus.Error.UnknownProperty"]
    broken = Call(MENU_PATH, MENU, "Event")  # no arguments: must not raise
    assert item.handle(broken.msg) and broken.errors


def test_tray_vanishing_is_reported_and_coming_back_registers_again() -> None:
    item, bus, _ = make_item()
    bus.tray_vanishes()
    assert item.handle(bus.incoming.pop(0)) is False
    assert item.check_shown() is False
    bus.watcher = True
    assert (
        item.handle(signal("org.freedesktop.DBus", "NameOwnerChanged", (WATCHER, "", ":1.8")))
        is True
    )
    assert item.check_shown() is True


def test_every_reply_and_signal_encodes_as_real_dbus() -> None:
    """The fake bus does not encode; the real one does, and is strict about types."""
    jeepney = pytest.importorskip("jeepney")
    address = jeepney.DBusAddress(ITEM_PATH, bus_name=ME, interface=ITEM)
    parent = jeepney.new_method_call(address, "Activate", "ii", (0, 0))
    parent.header.serial = 7
    item, bus, _ = make_item()
    calls = [
        Call(ITEM_PATH, PROPS, "GetAll", ITEM),
        Call(ITEM_PATH, PROPS, "Get", ITEM, "IconPixmap"),
        Call(ITEM_PATH, PROPS, "Get", ITEM, "ToolTip"),
        Call(ITEM_PATH, "org.freedesktop.DBus.Introspectable", "Introspect"),
        Call(ITEM_PATH, ITEM, "Activate", 1, 1),
        Call(MENU_PATH, PROPS, "GetAll", MENU),
        Call(MENU_PATH, MENU, "GetLayout", 0, -1, []),
        Call(MENU_PATH, MENU, "GetGroupProperties", [], []),
        Call(MENU_PATH, MENU, "GetProperty", 2, "label"),
        Call(MENU_PATH, MENU, "Event", 2, "clicked", ("i", 0), 0),
        Call(MENU_PATH, MENU, "EventGroup", [(2, "clicked", ("i", 0), 0)]),
        Call(MENU_PATH, MENU, "AboutToShow", 0),
        Call(MENU_PATH, MENU, "AboutToShowGroup", [0]),
    ]
    for state in STATES:
        item.set_state(state)
        for call in calls:
            call.replies.clear()
            item.handle(call.msg)
            assert len(call.replies) == 1 and not call.errors, call.msg.member
            signature, body = call.replies[0]
            jeepney.new_method_return(parent, signature or None, body).serialise(serial=1)
    for path, interface, member, body in bus.emitted:
        emitter = jeepney.DBusAddress(path, interface=interface)
        jeepney.new_signal(emitter, member, "ui" if body else None, body).serialise(serial=1)


# --- the running indicator ----------------------------------------------------------------
def _wait(condition: Any, seconds: float = 1.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.002)
    return False


def test_host_lost_calls_on_lost_within_one_frame() -> None:
    bus = FakeBus()
    lost: list[float] = []
    tray = TrayIndicator(lambda: bus, WHO)
    tray.start(lambda: None, lambda: lost.append(time.monotonic()))
    try:
        assert _wait(tray.available)
        tray.set_state("waiting")
        gone = time.monotonic()
        bus.tray_vanishes()
        assert _wait(lambda: bool(lost))
        assert lost[0] - gone < 0.02  # one 20 ms audio frame
        assert tray.available() is False
        assert len(lost) == 1  # said once, not on every poll
    finally:
        tray.stop()
    assert bus.closed


def test_bus_dying_is_also_lost_and_no_bus_is_never_available() -> None:
    bus = FakeBus()
    lost = threading.Event()
    tray = TrayIndicator(lambda: bus, WHO)
    tray.start(lambda: None, lost.set)
    try:
        assert _wait(tray.available)
        bus.dead = True
        assert lost.wait(1.0) and tray.available() is False
    finally:
        tray.stop()

    def no_bus() -> FakeBus:
        raise BusError("No session bus")

    nothing = TrayIndicator(no_bus, WHO)
    assert nothing.available() is False  # not started
    nothing.start(lambda: None, lambda: None)
    try:
        time.sleep(0.05)
        assert nothing.available() is False  # started, waiting for a bus, still running
        assert nothing._thread is not None and nothing._thread.is_alive()
    finally:
        nothing.stop()


def test_unexpected_error_in_the_thread_fails_closed() -> None:
    """Anything unexpected counts as losing the sign: on_lost once, not available."""
    bus = FakeBus()
    lost: list[int] = []
    tray = TrayIndicator(lambda: bus, WHO)
    tray.start(lambda: None, lambda: lost.append(1))
    try:
        assert _wait(tray.available)
        bus.poll_error = ValueError("something nobody planned for")
        assert _wait(lambda: bool(lost))
        assert tray.available() is False
        assert bus.closed and lost == [1]
        time.sleep(0.1)
        assert lost == [1]  # said once, not on every loop
        assert tray._thread is not None and tray._thread.is_alive()  # still there to recover
    finally:
        tray.stop()


def test_silently_dropped_item_is_noticed_without_anyone_asking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The thread re-checks by itself, so a tray that forgets us without a signal is caught."""
    monkeypatch.setattr("sasha.listener.indicator.tray.RECHECK_S", 0.05)
    bus = FakeBus()
    lost = threading.Event()
    tray = TrayIndicator(lambda: bus, WHO)
    tray.start(lambda: None, lost.set)
    try:
        assert _wait(tray.available)
        bus.items.clear()  # no signal, no call to available()
        assert lost.wait(1.0)
    finally:
        tray.stop()


def test_no_tray_waits_then_appears() -> None:
    bus = FakeBus(watcher=False)
    tray = TrayIndicator(lambda: bus, WHO)
    tray.start(lambda: None, lambda: None)
    try:
        time.sleep(0.05)
        assert tray.available() is False
        bus.watcher = True
        bus.incoming.append(
            signal("org.freedesktop.DBus", "NameOwnerChanged", (WATCHER, "", ":1.8"))
        )
        assert _wait(tray.available)
    finally:
        tray.stop()


def test_click_reaches_the_listener_and_a_failing_callback_does_not_stop_the_tray() -> None:
    bus = FakeBus()
    clicks: list[int] = []

    def toggle() -> None:
        clicks.append(1)
        raise RuntimeError("listener bug")

    tray = TrayIndicator(lambda: bus, WHO)
    tray.start(toggle, lambda: None)
    try:
        assert _wait(tray.available)
        bus.incoming.append(Call(ITEM_PATH, ITEM, "Activate", 1, 1).msg)
        assert _wait(lambda: clicks == [1])
        assert tray.available() is True
    finally:
        tray.stop()
