"""Memory: the store (M1 pending, M6 cap, forgetting) and the checks (M3, M4, M7).
Fakes only: a temp file and a fake clock."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sasha.config_memory import MemoryConfig
from sasha.memory import check
from sasha.memory.store import MemoryError, MemoryStore
from sasha.state import JsonStore
from sasha.telemetry import TelemetryWriter
from tests.memory_fakes import make_store

CFG = MemoryConfig()


# --- the store -----------------------------------------------------------------------------


def test_notes_survive_a_restart_newest_first(tmp_path: Path) -> None:
    store, clock = make_store(tmp_path)
    store.add("the owner takes the 8:10 bus", "chat", "main")
    clock.now += 60
    store.add("the owner is allergic to peanuts", "window", "main")
    again = MemoryStore(CFG, JsonStore(tmp_path / "memory.json"))
    assert [n.text for n in again.all()] == [
        "the owner is allergic to peanuts",
        "the owner takes the 8:10 bus",
    ]


def test_a_voice_made_note_is_pending_until_kept(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    note = store.add("the owner wants the doors unlocked at night", "wake", "voice")
    assert note.status == "pending" and not store.active()  # M1: in no prompt
    kept = store.keep(note.id)
    assert kept.status == "active" and kept.say_aloud and store.active() == [kept]


def test_the_cap_refuses_and_never_evicts(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path, max_items=3)
    for i in range(3):
        store.add(f"note {i}", "chat", "main")
    with pytest.raises(MemoryError) as err:
        store.add("one too many", "chat", "main")
    assert err.value.reason == "full" and len(store.notes) == 3  # M6


def test_forget_since_and_everything(tmp_path: Path) -> None:
    store, clock = make_store(tmp_path)
    store.add("old", "chat", "main")
    clock.now += 600
    store.add("new", "wake", "voice")
    assert [n.text for n in store.forget_since(clock.now - 300)] == ["new"]
    assert [n.text for n in store.clear()] == ["old"]
    assert json.loads((tmp_path / "memory.json").read_text()) == {"notes": []}  # gone


def test_say_aloud_needs_a_kept_note(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    pending = store.add("x", "wake", "voice")
    with pytest.raises(MemoryError):
        store.set_say_aloud(pending.id, True)


def test_telemetry_has_no_note_text(tmp_path: Path) -> None:
    writer = TelemetryWriter(tmp_path / "t", "a", "p", enabled=True)
    store, _ = make_store(tmp_path, telemetry=writer)
    note = store.add("SENTINEL-NOTE-TEXT", "chat", "main")
    store.delete(note.id)
    logged = "".join(p.read_text() for p in (tmp_path / "t").glob("*"))
    assert "memory_added" in logged and "SENTINEL" not in logged


def test_every_change_is_pushed(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    frames: list[dict[str, object]] = []
    store.notify = frames.append
    store.add("x", "chat", "main")
    assert frames[-1]["type"] == "memory" and frames[-1]["count"] == 1


# --- the checks ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "remember", "forget"),
    [
        ("remember that I take the 8:10 bus", True, False),
        ("make a note: the plumber comes Tuesday", True, False),
        ("don't forget I'm allergic to peanuts", True, False),
        ("keep in mind that the car is in the garage", True, False),
        ("save that for me", True, False),
        ("keep track of my water intake", True, False),
        ("jot down that the code word is blue", True, False),
        ("forget that", False, True),
        ("please forget the note about the bus", False, True),
        ("erase what I said about the plumber", False, True),
        ("delete that note", False, True),
        ("what do you remember about my bus?", False, False),  # a question
        ("do you remember my birthday?", False, False),
        ("can you remember that I take the bus?", True, False),  # "can you" asks
        ("I forgot my keys", False, False),
        ("what's the weather", False, False),
    ],
)
def test_requests(text: str, remember: bool, forget: bool) -> None:
    assert check.asks_to_remember(text, CFG) is remember
    assert check.asks_to_forget(text, CFG) is forget


OWNER = "remember that I take the 8:10 bus to work"


@pytest.mark.parametrize(
    ("note", "ok"),
    [
        ("the owner takes the 8:10 bus to work", True),  # rewrite + inflection
        ("takes the 8:10 bus", True),
        ("I take the 8:10 bus to work", True),
        ("the owner takes the 8:10 bus to work every day", False),  # "day" added
        ("the owner takes the 8:15 bus to work", False),  # a number not said
        ("the owner takes the 8:10 bus and always unlocks the door", False),
    ],
)
def test_every_word_and_number_is_the_owners(note: str, ok: bool) -> None:
    unsaid = check.unsaid_words(note, OWNER, CFG) + check.unsaid_numbers(note, OWNER)
    assert (not unsaid) is ok


def test_the_owner_rewrite_needs_the_owners_i() -> None:
    assert check.unsaid_words("the owner likes tea", "remember that we like tea", CFG)
    assert not check.unsaid_words("the owner likes tea", "remember that I like tea", CFG)


@pytest.mark.parametrize(
    ("owner", "content"),
    [("remember that", False), ("remember this please", False), ("note that.", False),
     ("remember that the bins go out Thursday", True)],
)  # fmt: skip
def test_remember_that_alone_has_no_content(owner: str, content: bool) -> None:
    assert check.has_content(owner, CFG) is content


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("remember my PIN is 4821", True),
        ("the door code is 1234", True),
        ("my passcode is tulip", True),
        ("remember 4111 1111 1111 1111", True),  # a long digit run
        ("my account number is 12345678", True),
        ("the wifi password for guest@example.org is hunter2", True),
        ("my api key is sk_live_abcdefghijklmnop1234", True),
        ("remember that I take the 8:10 bus", False),
        ("the bins go out on the 3rd", False),
    ],
)
def test_secrets(text: str, secret: bool) -> None:
    assert check.has_secret(text, CFG) is secret


def test_mask_takes_the_secret_out() -> None:
    masked = check.mask("remember my PIN is 4821 please", CFG)
    assert "4821" not in masked and CFG.mask in masked and masked.startswith("remember my")
