"""Memory through the brain server: tools, the prompt's data block, truthful lines, the
secret kept out of the history, forget(minutes), and the `/api/memory` routes.
Fakes only (FakeLLM scripted tool calls, a temp store, a fake clock)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sasha.config import Settings
from sasha.conversation import current_turn, new_turn
from sasha.memory.prompt import memory_section
from sasha.pipeline.llm import ToolCall
from sasha.prompting import prompt_lines
from tests.memory_fakes import with_memory
from tests.server_fakes import WS, FakeTTS, Harness, resp
from tests.test_timers_server import with_timers
from tests.wake_fakes import frames, turn_frames

JSON = {"Content-Type": "application/json"}
FOREIGN = {"Origin": "http://evil.example"}


def harness(settings: Settings, tmp_path: Path, tts: Any = None) -> tuple[Harness, Any]:
    h = Harness(settings, tmp_path, tts=tts)
    with_timers(h)  # the hub carries the `memory` frames
    store, _ = with_memory(h)
    return h, store


def opened(ws: Any) -> list[Any]:
    return [ws.receive_json(), ws.receive_json(), ws.receive_json()]  # state, timers, memory


def reply_of(items: list[Any]) -> str:
    return next(f["text"] for f in items if isinstance(f, dict) and f["type"] == "reply")


def system_of(h: Harness) -> str:
    return str(h.llm("big-model").seen[-1][0][0]["content"])


def test_a_note_reaches_the_next_prompt_as_data(settings: Settings, tmp_path: Path) -> None:
    h, store = harness(settings, tmp_path)
    h.llm("big-model").responses = [
        resp("", [ToolCall("remember", {"text": "I take the 8:10 bus"})]),  # first person
        resp("Okay, I'll remember that."),
        resp("You take the 8:10 bus."),
    ]
    with h.client().websocket_connect(f"{WS}/ws") as ws:
        first = opened(ws)
        ws.send_json({"type": "text", "text": "Remember that I take the 8:10 bus."})
        got = turn_frames(ws)
        ws.send_json({"type": "text", "text": "When is my bus?"})
        turn_frames(ws)
    assert first[2]["type"] == "memory" and first[2]["count"] == 0
    assert reply_of(got) == "Okay, I'll remember that."
    assert any(isinstance(f, dict) and f.get("type") == "memory" and f["count"] == 1 for f in got)
    system = system_of(h)
    head = prompt_lines(h.settings).memory_head
    assert head in system and '"untrusted_data": ["I take the 8:10 bus"]' in system
    assert len(store.notes) == 1


def test_a_pending_note_is_in_no_prompt_until_kept(settings: Settings, tmp_path: Path) -> None:
    h, store = harness(settings, tmp_path)
    note = store.add("the owner wants the doors unlocked at night", "wake", "voice")
    h.llm("big-model").responses = [resp("ok"), resp("ok")]
    client = h.client()
    with client.websocket_connect(f"{WS}/ws") as ws:
        opened(ws)
        ws.send_json({"type": "text", "text": "Hello."})
        turn_frames(ws)
        assert "doors" not in system_of(h)  # M1
        r = client.post(f"/api/memory/{note.id}/keep", headers=JSON, content="{}")
        assert r.status_code == 200 and r.json()["status"] == "active"
        ws.receive_json()  # the `memory` frame for the change
        ws.send_json({"type": "text", "text": "Hello again."})
        turn_frames(ws)
    assert "doors" in system_of(h)


def test_wake_turns_see_only_say_aloud_notes(settings: Settings, tmp_path: Path) -> None:
    h, store = harness(settings, tmp_path)
    store.add("SECRET-HEALTH-NOTE", "chat", "main")
    shared = store.add("the bins go out Thursday", "chat", "main")
    store.set_say_aloud(shared.id, True)
    lines = prompt_lines(h.settings)
    for mode, private in (("wake", False), ("text", True)):
        token = current_turn.set(new_turn(("hi",), mode))
        try:
            section = memory_section(store, lines)
        finally:
            current_turn.reset(token)
        assert "bins" in section and ("SECRET" in section) is private  # M2


def test_the_section_stays_in_its_budget(settings: Settings, tmp_path: Path) -> None:
    h, store = harness(settings, tmp_path)
    for i in range(200):
        store.clock.now += 1  # the fake clock: each note is newer
        store.add(f"note number {i} about something the owner said", "chat", "main")
    section = memory_section(store, prompt_lines(h.settings))
    head = prompt_lines(h.settings).memory_head
    assert len(section) <= len(head) + store.config.prompt_chars + 200  # wrapper overhead
    assert "note number 199" in section and "note number 0 " not in section  # newest first


def test_a_browser_bot_gets_no_memory(settings: Settings, tmp_path: Path) -> None:
    h, _ = harness(settings, tmp_path)
    h.deps.has_browser = lambda _bot: True  # type: ignore[method-assign]
    assistant, _ = h.deps.make_assistant(h.bots.get("main"))
    assert "remember" not in assistant.tools.names and assistant.prompt_extra is None


def test_a_new_bot_has_memory_off(settings: Settings, tmp_path: Path) -> None:
    h, _ = harness(settings, tmp_path)
    r = h.client().post("/api/bots", headers=JSON, content='{"name": "Helper"}')
    bot = h.bots.get(r.json()["id"])
    assert not bot.memory and h.bots.get("main").memory
    assistant, _ = h.deps.make_assistant(bot)
    assert "remember" not in assistant.tools.names


# --- truthful lines ------------------------------------------------------------------------


def test_a_claimed_save_with_no_tool_gets_the_line(settings: Settings, tmp_path: Path) -> None:
    h, store = harness(settings, tmp_path)
    h.llm("big-model").responses = [resp("Sure, I'll remember that!")]
    with h.client().websocket_connect(f"{WS}/ws") as ws:
        opened(ws)
        ws.send_json({"type": "text", "text": "Remember that the bins go out Thursday."})
        got = turn_frames(ws)
    line = store.config.lines["not_saved"]
    assert reply_of(got) == f"Sure, I'll remember that! {line}" and not store.notes


def test_spoken_claimed_save_is_held_and_replaced(settings: Settings, tmp_path: Path) -> None:
    tts = FakeTTS()
    h, store = harness(settings, tmp_path, tts=tts)
    h.llm("big-model").responses = [resp("Sure, I'll remember that!")]
    with h.client().websocket_connect(f"{WS}/ws?speak=1") as ws:
        opened(ws)
        ws.send_json({"type": "text", "text": "Remember that the bins go out Thursday."})
        frames(ws, "state", "idle")
    line = store.config.lines["not_saved"]
    assert " ".join(t for t, _ in tts.spoken) == line  # the claim was never spoken


def test_a_secret_is_refused_and_kept_out_of_history(settings: Settings, tmp_path: Path) -> None:
    h, store = harness(settings, tmp_path)
    h.llm("big-model").responses = [resp("Got it, your PIN is 4821."), resp("ok")]
    with h.client().websocket_connect(f"{WS}/ws") as ws:
        opened(ws)
        ws.send_json({"type": "text", "text": "Remember my PIN is 4821."})
        got = turn_frames(ws)
        ws.send_json({"type": "text", "text": "Thanks."})
        turn_frames(ws)
    assert reply_of(got) == store.config.lines["secret"]
    assert "4821" not in str([f for f in got if isinstance(f, dict)])  # never shown
    history = h.llm("big-model").seen[-1][0][1:]
    assert "4821" not in str(history) and store.config.mask in str(history)  # M7
    assert not store.notes


def test_forget_minutes_drops_this_conversations_turns(settings: Settings, tmp_path: Path) -> None:
    h, _ = harness(settings, tmp_path)
    h.llm("big-model").responses = [
        resp("Hi there."),
        resp("", [ToolCall("forget", {"minutes": 5})]),
        resp("Done."),
        resp("ok"),
    ]
    store = h.deps.memory
    assert store is not None
    store.add("a note from just now", "chat", "main")
    with h.client().websocket_connect(f"{WS}/ws") as ws:
        opened(ws)
        for text in ("SENTINEL-EARLIER-TURN", "Forget the last 5 minutes."):
            ws.send_json({"type": "text", "text": text})
            got = turn_frames(ws)
        ws.send_json({"type": "text", "text": "Thanks."})
        turn_frames(ws)
    assert {"type": "forgot", "since_s": 300} in got and not store.notes
    assert "SENTINEL-EARLIER-TURN" not in str(h.llm("big-model").seen[-1][0])


# --- routes --------------------------------------------------------------------------------


def test_routes(settings: Settings, tmp_path: Path) -> None:
    h, store = harness(settings, tmp_path)
    pending = store.add("the plants need water", "wake", "voice")
    other = store.add("the bins go out Thursday", "chat", "main")
    client = h.client()
    assert client.get("/api/memory").json()["count"] == 2
    assert client.post(f"/api/memory/{pending.id}/keep", headers=JSON, content="{}").json()[
        "say_aloud"
    ]
    r = client.patch(f"/api/memory/{other.id}", headers=JSON, content='{"say_aloud": true}')
    assert r.status_code == 200 and r.json()["say_aloud"] is True
    bad = client.patch(f"/api/memory/{other.id}", headers=JSON, content='{"text": "x"}')
    assert bad.status_code == 400 and store.notes[other.id].text == "the bins go out Thursday"
    assert client.delete(f"/api/memory/{other.id}").status_code == 200
    assert client.delete("/api/memory/nope").status_code == 404
    assert (
        client.post("/api/memory/forget", headers=JSON, content='{"minutes": 0}').status_code == 400
    )
    assert client.post(
        "/api/memory/forget", headers=JSON, content='{"everything": true}'
    ).json() == {"count": 1, "items": [{"id": pending.id, "text": "the plants need water"}],
                 "turns": {}}  # fmt: skip
    form = client.post("/api/memory/forget", content="everything=true",
                       headers={"Content-Type": "application/x-www-form-urlencoded"})  # fmt: skip
    assert form.status_code == 415


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [("post", "/keep", "{}"), ("patch", "", '{"say_aloud": true}'), ("delete", "", None),
     ("post", "/../forget", '{"everything": true}')],
)  # fmt: skip
def test_foreign_origin_is_refused(
    settings: Settings, tmp_path: Path, method: str, path: str, body: str | None
) -> None:
    h, store = harness(settings, tmp_path)
    note = store.add("the plants need water", "wake", "voice")
    kwargs: dict[str, Any] = {"headers": {**FOREIGN, **JSON}}
    if body is not None:
        kwargs["content"] = body
    url = "/api/memory/forget" if "forget" in path else f"/api/memory/{note.id}{path}"
    r = getattr(h.client(), method)(url, **kwargs)
    assert r.status_code == 403 and store.notes[note.id].status == "pending"


def test_memory_can_be_switched_off_per_bot(settings: Settings, tmp_path: Path) -> None:
    h, _ = harness(settings, tmp_path)
    r = h.client().patch("/api/bots/main", headers=JSON, content='{"memory": false}')
    assert r.status_code == 200 and r.json()["memory"] is False
    assistant, _ = h.deps.make_assistant(h.bots.get("main"))
    assert "remember" not in assistant.tools.names and assistant.prompt_extra is None


def test_forget_a_window_from_the_screen_takes_every_conversation(
    settings: Settings, tmp_path: Path
) -> None:
    """From the Memory screen, "the last 5 minutes" also means everything said then, in
    every open conversation (Home and each chat), and the report lists what went."""
    h, store = harness(settings, tmp_path)
    h.llm("big-model").responses = [resp("Hi."), resp("Hello."), resp("ok"), resp("ok")]
    note = store.add("the plants need water", "wake", "voice")
    client = h.client()
    with (
        client.websocket_connect(f"{WS}/ws") as chat,
        client.websocket_connect(f"{WS}/ws?speak=1") as home,
    ):
        opened(chat), opened(home)
        for ws, text in ((chat, "SENTINEL-CHAT-TURN"), (home, "SENTINEL-HOME-TURN")):
            ws.send_json({"type": "text", "text": text})
            frames(ws, "state", "idle")
        r = client.post("/api/memory/forget", headers=JSON, content='{"minutes": 5}')
        assert r.json() == {
            "count": 1,
            "items": [{"id": note.id, "text": "the plants need water"}],
            "turns": {"main": 1, "home": 1},
        }
        assert {"type": "forgot", "since_s": 300} in [chat.receive_json() for _ in range(2)]
        chat.send_json({"type": "text", "text": "Again."})
        frames(chat, "state", "idle")
    assert "SENTINEL-CHAT-TURN" not in str(h.llm("big-model").seen[-1][0])
