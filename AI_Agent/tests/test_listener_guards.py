"""Fail-closed guards with a fake system backend."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from sasha.listener.guards import GuardEvent, Guards
from tests.listener_fakes import FakeBackend


def run_guards(
    body: Callable[[Guards, FakeBackend, list[str]], Awaitable[None]],
    pause_on_battery: bool = True,
    setup: Callable[[FakeBackend], None] | None = None,
) -> None:
    async def main() -> None:
        backend = FakeBackend()
        if setup:
            setup(backend)
        calls: list[str] = []
        guards = Guards(backend, lambda: calls.append("change"), pause_on_battery, poll_s=0.02)
        await guards.start()
        await asyncio.sleep(0.01)
        try:
            await body(guards, backend, calls)
        finally:
            await guards.stop()

    asyncio.run(main())


def test_all_clear_allows_capture() -> None:
    async def body(g: Guards, b: FakeBackend, calls: list[str]) -> None:
        assert g.allowed() == (True, "ok")
        assert b.inhibitor

    run_guards(body)


@pytest.mark.parametrize(
    ("fact", "reason"),
    [("locked", "screen locked"), ("active", "session not active"), ("battery", "on battery")],
)
def test_each_fact_closes_the_mic_by_signal(fact: str, reason: str) -> None:
    async def body(g: Guards, b: FakeBackend, calls: list[str]) -> None:
        value = fact != "active"  # locked=True, active=False, battery=True are "bad"
        setattr(b, fact, value)
        kind = {"locked": "lock", "active": "active", "battery": "battery"}[fact]
        b.events_q.put_nowait(GuardEvent(kind, value))
        await asyncio.sleep(0.001)
        ok, why = g.allowed()
        assert not ok and reason in why

    run_guards(body)


def test_polling_catches_a_missed_signal() -> None:
    async def body(g: Guards, b: FakeBackend, calls: list[str]) -> None:
        b.locked = True  # no signal sent
        await asyncio.sleep(0.08)
        assert not g.allowed()[0]
        b.locked = False
        await asyncio.sleep(0.08)
        assert g.allowed()[0]

    run_guards(body)


def test_a_read_error_closes_the_mic_until_reads_work_again() -> None:
    async def body(g: Guards, b: FakeBackend, calls: list[str]) -> None:
        b.fail.add("lock")
        await asyncio.sleep(0.08)
        ok, why = g.allowed()
        assert not ok and "read failed" in why
        b.fail.clear()
        await asyncio.sleep(0.08)
        assert g.allowed()[0]

    run_guards(body)


@pytest.mark.parametrize("what", ["connect", "subscribe", "inhibit"])
def test_setup_failures_refuse_capture(what: str) -> None:
    async def body(g: Guards, b: FakeBackend, calls: list[str]) -> None:
        await asyncio.sleep(0.05)
        assert not g.allowed()[0]

    run_guards(body, setup=lambda b: b.fail.add(what))


def test_battery_ignored_when_pause_on_battery_is_off() -> None:
    async def body(g: Guards, b: FakeBackend, calls: list[str]) -> None:
        b.battery = True
        b.fail.add("battery")  # not even read
        await asyncio.sleep(0.08)
        assert g.allowed() == (True, "ok")

    run_guards(body, pause_on_battery=False)


def test_sleep_closes_the_mic_before_releasing_the_inhibitor() -> None:
    async def body(g: Guards, b: FakeBackend, calls: list[str]) -> None:
        order: list[str] = []
        g.on_change = lambda: order.append(f"change:{g.allowed()[0]}")
        real_release = b.release_sleep_inhibitor

        def release() -> None:
            order.append("release")
            real_release()

        b.release_sleep_inhibitor = release  # type: ignore[method-assign]
        b.events_q.put_nowait(GuardEvent("sleep", True))
        await asyncio.sleep(0.01)
        assert order[:2] == ["change:False", "release"]
        assert not b.inhibitor and not g.allowed()[0]
        b.events_q.put_nowait(GuardEvent("sleep", False))
        await asyncio.sleep(0.05)
        assert b.inhibitor and g.allowed()[0]

    run_guards(body)


def test_stop_releases_everything() -> None:
    async def main() -> None:
        b = FakeBackend()
        g = Guards(b, lambda: None)
        await g.start()
        await g.stop()
        assert not b.inhibitor and "close" in b.log

    asyncio.run(main())


def test_lost_inhibitor_after_resume_fails_closed_until_retaken() -> None:
    """Review L1: a good read must not reopen the mic while no inhibitor is held."""

    async def body(g: Guards, b: FakeBackend, calls: list[str]) -> None:
        b.events_q.put_nowait(GuardEvent("sleep", True))
        await asyncio.sleep(0.01)
        b.fail.add("inhibit")
        b.events_q.put_nowait(GuardEvent("sleep", False))
        await asyncio.sleep(0.1)  # several 20 ms polls with good reads
        ok, why = g.allowed()
        assert not ok and "no sleep inhibitor" in why and not g.state.inhibited
        b.fail.clear()  # the next poll retries and takes it
        await asyncio.sleep(0.08)
        assert g.allowed() == (True, "ok") and b.inhibitor

    run_guards(body)


def test_subscribed_only_after_the_backend_says_ready() -> None:
    """Review L7: no "subscribed" before every AddMatch has succeeded."""

    async def main() -> None:
        gate = asyncio.Event()

        class SlowBackend(FakeBackend):
            async def events(self):  # type: ignore[no-untyped-def,override]
                await gate.wait()  # AddMatch still running
                async for event in super().events():
                    yield event

        b = SlowBackend()
        g = Guards(b, lambda: None, poll_s=0.02)
        await g.start()
        await asyncio.sleep(0.05)
        ok, why = g.allowed()
        assert not ok and "subscription" in why
        gate.set()
        await asyncio.sleep(0.02)
        assert g.allowed()[0]
        await g.stop()

    asyncio.run(main())


def test_inhibitor_is_taken_off_the_event_loop() -> None:
    """Review L9: the blocking Inhibit call runs in a worker thread."""
    import threading

    threads: list[str] = []

    async def body(g: Guards, b: FakeBackend, calls: list[str]) -> None:
        assert threads and all(t != "MainThread" for t in threads)

    def setup(b: FakeBackend) -> None:
        real = b.take_sleep_inhibitor

        def take() -> None:
            threads.append(threading.current_thread().name)
            real()

        b.take_sleep_inhibitor = take  # type: ignore[method-assign]

    run_guards(body, setup=setup)
