"""The review of 6d01783 / 3b27083 (Dependencies/team/reviews/memory-server-6d01783.md):
one test per probe, R1-R6, W1-W3 and the should-fixes. Fakes only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sasha.config import Settings
from sasha.config_memory import MemoryConfig
from sasha.memory import check
from sasha.memory.store import MemoryError
from sasha.memory.truth import MemoryTruth
from sasha.orchestrator import Assistant
from sasha.pipeline.llm import ToolCall
from sasha.telemetry import TelemetryWriter
from sasha.tools import Tool, ToolRegistry
from tests.memory_fakes import make_store, tools, turn, with_memory
from tests.server_fakes import WS, FakeLLM, FakeTTS, Harness, resp
from tests.test_memory_tools import reason
from tests.test_timers_server import with_timers
from tests.wake_fakes import frames, listen, make, segment, turn_frames

CFG = MemoryConfig()
JSON = {"Content-Type": "application/json"}


def harness(settings: Settings, tmp_path: Path, tts: Any = None) -> tuple[Harness, Any]:
    h = Harness(settings, tmp_path, tts=tts)
    with_timers(h)
    store, _ = with_memory(h)
    return h, store


def shown(items: list[Any]) -> str:
    return next(f["text"] for f in items if isinstance(f, dict) and f["type"] == "reply")


def history(h: Harness) -> str:
    return str(h.llm("big-model").seen[-1][0][1:])  # everything after the system prompt


# --- R1: "forget everything" needs a plain yes to the server's own question --------------


def test_r1_a_yes_to_another_question_clears_nothing(settings: Settings, tmp_path: Path) -> None:
    h, store = harness(settings, tmp_path)
    store.add("the bus is at 8:10", "chat", "main")
    store.add("the bins go out Thursday", "chat", "main")
    h.llm("big-model").responses = [
        resp("", [ToolCall("forget", {"everything": True})]),
        resp("Do you want me to forget just the note about the bus?"),
        resp("", [ToolCall("forget", {"which": "bus"})]),
        resp("Done."),
    ]
    with h.client().websocket_connect(f"{WS}/ws") as ws:
        [ws.receive_json() for _ in range(3)]
        ws.send_json({"type": "text", "text": "Forget all my notes."})
        first = turn_frames(ws)
        ws.send_json({"type": "text", "text": "Yes."})
        turn_frames(ws)
    assert shown(first).endswith(CFG.lines["confirm_everything"])  # always the server's
    assert len(store.notes) == 2


@pytest.mark.parametrize(
    ("text", "yes"),
    [("Yes.", True), ("yes please", True), ("OK", True), ("go ahead", True),
     ("yes but only the last one", False), ("ok wait no", False),
     ("sure, actually don't", False), ("yes, just the bus", False)],
)  # fmt: skip
def test_r1_only_a_plain_yes(text: str, yes: bool) -> None:
    assert check.says_yes(text, CFG) is yes


def test_r1_a_hedged_yes_clears_nothing(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    store.add("a", "chat", "main")
    memory = tools(store)
    with turn("Forget everything."):
        assert reason(memory.forget, everything=True) == "confirm_everything"
    with turn("Forget everything.", "yes but only the last one"):
        assert reason(memory.forget, everything=True) == "not_asked"
    assert len(store.notes) == 1


# --- R2: a secret in the answer to an asked-back question -----------------------------


@pytest.mark.parametrize("speak", ["0", "1"])
def test_r2_a_secret_in_the_answer_is_never_shown_or_kept(
    settings: Settings, tmp_path: Path, speak: str
) -> None:
    tts = FakeTTS()
    h, store = harness(settings, tmp_path, tts=tts)
    h.llm("big-model").responses = [
        resp("What is the code?"),
        resp("", [ToolCall("remember", {"text": "the door code is 4821"})]),
        resp("Sorry, I can't keep the code 4821."),
        resp("ok"),
    ]
    with h.client().websocket_connect(f"{WS}/ws?speak={speak}") as ws:
        [ws.receive_json() for _ in range(3)]
        got: list[Any] = []
        for text in ("Remember the door code.", "It's 4821.", "Thanks."):
            ws.send_json({"type": "text", "text": text})
            got += frames(ws, "state", "idle")
    assert "4821" not in str([f for f in got if isinstance(f, dict)])  # never shown
    assert "4821" not in history(h) and not store.notes  # both messages masked
    if speak == "1":
        said = [t for t, _ in tts.spoken]
        assert "4821" not in str(said) and CFG.lines["secret"] in " ".join(said)


def test_r2_a_replacement_is_never_sent_as_a_slice() -> None:
    from sasha.server.turn_check import ChainCheck

    redacted: list[int] = []
    part = MemoryTruth(CFG, ["Thanks."], False, lambda _f, n: redacted.append(n))
    chain = ChainCheck([part])
    calls = [{"name": "remember", "ok": False, "asks": "secret"}]
    tail, final = chain.finish("Sorry, I can't keep the code 4821.", calls)
    assert tail == "" and final == CFG.lines["secret"] and chain.replaced and redacted == [1]


# --- R3: wake turns -------------------------------------------------------------------------


def test_r3_a_wake_secret_stays_out_of_the_history(settings: Settings, tmp_path: Path) -> None:
    h, stt = make(settings, tmp_path)  # no TTS: the next segment needs no playback
    with_timers(h)
    store, _ = with_memory(h)
    name = settings.assistant.wake_word.phrase
    stt.say(f"{name}, remember my PIN is 4821", f"{name}, hello")
    h.llm("big-model").responses = [
        resp("", [ToolCall("remember", {"text": "my PIN is 4821"})]),
        resp("Okay, your PIN 4821 is saved."),
        resp("Hi."),
    ]
    with h.client().websocket_connect(f"{WS}/ws?wake=1&speak=1") as ws:
        listen(ws)
        segment(ws)
        first = turn_frames(ws)
        segment(ws)
        turn_frames(ws)
    assert shown(first) == CFG.lines["secret"] and not store.notes
    said_back = [f for f in first if isinstance(f, dict) and f["type"] != "transcript"]
    assert "4821" not in str(said_back)  # the transcript is the owner's own words, on screen
    assert "4821" not in str(h.llm("big-model").seen[-1][0])  # the next wake prompt


# --- R4: a waiting note's text reaches no prompt --------------------------------------------


def test_r4_waiting_text_is_in_no_tool_result(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    store.add("WAITING: the doors unlocked at night", "wake", "voice")
    memory = tools(store)
    with turn("what do you remember?", mode="wake"):
        assert "WAITING" not in str(memory.list_memories())
    with turn("What do you remember?"):
        assert "WAITING" not in str(memory.list_memories())
    with turn("Forget the last 5 minutes."):
        assert "WAITING" not in str(memory.forget(minutes=5))


# --- R5: negations and the request's own words ------------------------------------------------


@pytest.mark.parametrize(
    ("owner", "note", "ok"),
    [
        ("Remember that I'm not allergic to peanuts.", "I'm allergic to peanuts", False),
        ("Remember that I'm not allergic to peanuts.", "I'm not allergic to peanuts", True),
        ("Remember I never leave the back door unlocked.", "I leave the back door unlocked",
         False),
        ("Don't forget I take the pill at 8.", "don't take the pill at 8", False),
        ("Don't forget I take the pill at 8.", "I take the pill at 8", True),
        ("Remember the shop isn't open on Sunday.", "the shop is not open on Sunday", True),
    ],
)  # fmt: skip
def test_r5_negations_and_request_words(owner: str, note: str, ok: bool) -> None:
    unsaid = check.unsaid_words(note, owner, CFG) + check.unsaid_numbers(note, owner)
    assert (not unsaid) is ok


# --- R6: forget(which) is named by the owner's own words -------------------------------------


def test_r6_which_must_be_the_owners_words(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    store.add("I take insulin at 9", "chat", "main")
    bus = store.add("the bus is at 8:10", "chat", "main")
    memory = tools(store)
    with turn("Forget that note about the bus."):
        assert reason(memory.forget, which="insulin") == "which_note"
        assert reason(memory.forget, which=bus.id) == "which_note"  # an id isn't said
        assert memory.forget(which="bus")["forgot"] == {"notes": 1}
    assert [n.text for n in store.all()] == ["I take insulin at 9"]


# --- W1, W2, W3 ---------------------------------------------------------------------------------


def test_w1_a_forgotten_note_never_comes_back_in_the_reply(
    settings: Settings, tmp_path: Path
) -> None:
    h, store = harness(settings, tmp_path)
    store.add("I take insulin at 9", "chat", "main")
    h.llm("big-model").responses = [
        resp("", [ToolCall("forget", {"minutes": 5})]),
        resp("Done, I forgot: I take insulin at 9."),
        resp("ok"),
    ]
    with h.client().websocket_connect(f"{WS}/ws") as ws:
        [ws.receive_json() for _ in range(3)]
        ws.send_json({"type": "text", "text": "Forget the last 5 minutes."})
        got = turn_frames(ws)
        ws.send_json({"type": "text", "text": "Thanks."})
        turn_frames(ws)
    assert shown(got) == CFG.lines["forgot"].format(what="1 note")
    assert "insulin" not in str([f for f in got if isinstance(f, dict) and f["type"] != "memory"])
    assert "insulin" not in history(h)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def assistant(settings: Settings, tmp_path: Path, llm: FakeLLM, tools: list[Tool]) -> Assistant:
    out = Assistant(settings, llm, ToolRegistry(tools), TelemetryWriter(tmp_path, "a", "p", False))
    out.clock = Clock()
    return out


def test_w2_a_turn_in_flight_during_a_forget_is_dropped(settings: Settings, tmp_path: Path) -> None:
    holder: list[Assistant] = []

    def forget_now() -> dict[str, int]:  # the Memory screen's forget, landing mid-turn
        return {"turns": holder[0].forget_turns(300)}

    forget = Tool("forget_now", "x", {"type": "object", "properties": {}}, forget_now)
    llm = FakeLLM("m", [resp("Hi."), resp("", [ToolCall("forget_now", {})]), resp("ok"),
                        resp("ok")])  # fmt: skip
    a = assistant(settings, tmp_path, llm, [forget])
    holder.append(a)
    a.handle_text("an older turn")
    a.clock.now += 1000  # type: ignore[attr-defined]
    a.handle_text("the surprise party for my sister is on friday")  # forget lands mid-turn
    a.clock.now += 10  # type: ignore[attr-defined]
    a.handle_text("a later turn")
    kept = [m["content"] for m in a._history if m["role"] == "user"]
    assert kept == ["an older turn", "a later turn"]


def test_w3_replace_reply_never_writes_another_turns_entry(
    settings: Settings, tmp_path: Path
) -> None:
    a = assistant(settings, tmp_path, FakeLLM("m", [resp("old reply"), resp("new reply")]), [])
    a.handle_text("old")
    a.clock.now += 1000  # type: ignore[attr-defined]
    a.handle_text("new")
    a.forget_turns(300)  # the screen's forget between the turn and its truth check
    a.replace_reply("REPLACED")
    a.redact_request(lambda _t: "REDACTED")
    assert [m["content"] for m in a._history] == ["old", "old reply"]


# --- should-fixes ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["the code for the gate is 4821", "the alarm code is four eight two one",
     "safe combination 12 34 56", "the wifi is hunter2butter",
     "remember the network password: tulip"],
)  # fmt: skip
def test_s1_more_secrets(text: str) -> None:
    assert check.has_secret(text, CFG)


def test_s2_keep_only_a_waiting_note(settings: Settings, tmp_path: Path) -> None:
    h, store = harness(settings, tmp_path)
    private = store.add("SECRET-HEALTH-NOTE", "chat", "main")
    with pytest.raises(MemoryError):
        store.keep(private.id)
    r = h.client().post(f"/api/memory/{private.id}/keep", headers=JSON, content="{}")
    assert r.status_code == 409 and not store.notes[private.id].say_aloud


@pytest.mark.parametrize(
    "text",
    ["I remember the bus was late", "remind me what I asked you to remember",
     "we remember that trip fondly", "what did I tell you to remember?"],
)  # fmt: skip
def test_s4_no_request(text: str) -> None:
    assert not check.asks_to_remember(text, CFG)


def test_s3_the_kept_text_is_shown_back() -> None:
    truth = MemoryTruth(CFG, ["Remember that I take the bus."], False, lambda _f, _n: None)
    calls = [{"name": "remember", "ok": True, "report": {"noted": "I take the bus"}}]
    assert truth.finish("Okay!", calls) == (
        " Noted: I take the bus.",
        "Okay! Noted: I take the bus.",
    )
