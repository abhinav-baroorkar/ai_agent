"""The microphone in bot chats (`avatar/web/js/voice-input.js`) closes when it must.

The rule is that the microphone is never open without a visible sign, and that a
recording is thrown away when the owner leaves it. These tests run the real module
under Node, with a fake microphone, a fake conversation and a minimal page, and
check: leaving the chat, hiding the window and closing the page each close the
microphone and send nothing more; a start that is overtaken closes what it opened;
the sign stays up while any part of the page has the microphone; and discarding a
recording that never started sends no `cancel` (so it can't stop a reply).
Skipped where Node isn't installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

WEB_JS = REPO_ROOT / "AI_Agent/src/sasha/avatar/web/js"

# Stand-ins for the two modules that touch real devices or the network.
FAKE_API = "export const MOCK = false;\nexport async function api() { return {}; }\n"
FAKE_AUDIO_IN = """
export const mics = [];
export class Mic {
  constructor() {
    Object.assign(this, { starts: 0, stops: 0, onFrame: null, gate: null });
    mics.push(this);
  }
  static supported() { return true; }
  start(onFrame) {
    this.starts += 1;
    this.onFrame = onFrame;
    if (!globalThis.holdMic) return Promise.resolve(true);
    return new Promise((resolve) => { this.gate = resolve; }); // the test opens it later
  }
  stop() { this.stops += 1; this.onFrame = null; }
}
export function micErrorText() { return 'error'; }
"""

HARNESS = """
const events = new EventTarget();
const sign = { hidden: true, addEventListener() {} };
globalThis.window = events;
globalThis.document = Object.assign(new EventTarget(), {
  hidden: false,
  querySelector: (sel) => (sel === '#mic-on' ? sign : null),
});
globalThis.location = { hash: '#chat', search: '' };
globalThis.matchMedia = () => ({ matches: false });
const dir = process.argv[process.argv.length - 1];
const { VoiceInput } = await import(`${dir}/voice-input.js`);
const { micSign } = await import(`${dir}/mic-sign.js`);
const { mics } = await import(`${dir}/audio-in.js`);
const mic = mics[0];
const tick = () => new Promise((r) => setTimeout(r, 0));

function conversation(state = 'idle') {
  return {
    state, log: [], waiting: null,
    whenIdle(next) {
      if (this.state === 'idle') return next();
      this.waiting = next;
      this.log.push('cancel'); // the real one asks the running reply to stop first
      return undefined;
    },
    startAudio() { this.log.push('audio_start'); },
    sendAudio() { this.log.push('frame'); },
    stopAudio() { this.log.push('audio_stop'); },
    cancel() { this.log.push('cancel'); },
  };
}

function input(conv, owner = 'chat:x') {
  return new VoiceInput({
    conv, owner, isVisible: () => location.hash === '#chat',
    onChange() {}, onNotice() {},
  });
}

const frame = new Int16Array(320).buffer;
const results = {};

async function leaveBy(kind) {
  location.hash = '#chat';
  document.hidden = false;
  const conv = conversation();
  const v = input(conv);
  const stopsBefore = mic.stops;
  await v.start();
  mic.onFrame(frame);
  const signWhileOn = !sign.hidden;
  const feed = mic.onFrame;
  if (kind === 'leave') { location.hash = '#bots'; events.dispatchEvent(new Event('hashchange')); }
  if (kind === 'hide') {
    document.hidden = true;
    document.dispatchEvent(new Event('visibilitychange'));
  }
  if (kind === 'pagehide') events.dispatchEvent(new Event('pagehide'));
  const after = conv.log.length;
  feed(frame); // a frame still in flight from the audio device
  results[kind] = {
    log: conv.log, sentAfter: conv.log.slice(after), on: v.on, signWhileOn,
    signAfter: !sign.hidden, micStopped: mic.stops > stopsBefore,
  };
}

await leaveBy('leave');
await leaveBy('hide');
await leaveBy('pagehide');

// A start overtaken by leaving: the microphone opens after the owner has gone.
location.hash = '#chat';
document.hidden = false;
globalThis.holdMic = true;
{
  const conv = conversation();
  const v = input(conv);
  const pending = v.start();
  await tick();
  const signWhileStarting = !sign.hidden;
  const stops = mic.stops;
  location.hash = '#bots';
  events.dispatchEvent(new Event('hashchange'));
  mic.gate(true); // the browser finally hands over the microphone
  await pending;
  results.overtaken = {
    log: conv.log, on: v.on, signWhileStarting,
    signAfter: !sign.hidden, closedAgain: mic.stops > stops,
  };
}
globalThis.holdMic = false;

// The sign belongs to everyone who has the microphone, not to the last one.
{
  location.hash = '#chat';
  micSign('home', true);
  const v = input(conversation(), 'chat:y');
  await v.start();
  v.cancel();
  const stillUp = !sign.hidden;
  micSign('home', false);
  results.shared = { stillUp, downAtEnd: sign.hidden };
}

// R2: a recording still waiting for a reply to finish is discarded without a cancel.
{
  location.hash = '#chat';
  const conv = conversation('thinking');
  const v = input(conv);
  await v.start();
  const before = conv.log.slice();
  location.hash = '#bots';
  events.dispatchEvent(new Event('hashchange'));
  results.waiting = {
    before, after: conv.log.slice(before.length), on: v.on, waiting: conv.waiting,
  };
}

// "Send now" while the recording waits for a reply to finish: sent once idle, not dropped.
{
  location.hash = '#chat';
  const conv = conversation('thinking');
  const v = input(conv);
  await v.start();
  mic.onFrame(frame);
  mic.onFrame(frame);
  v.send(); // the owner clicks the microphone again: send it now
  const beforeIdle = conv.log.slice();
  const next = conv.waiting;
  conv.state = 'idle';
  if (next) next(); // the reply finished: the server is idle
  results.sendNow = {
    beforeIdle, after: conv.log.slice(beforeIdle.length), on: v.on, signAfter: !sign.hidden,
  };
}

console.log(JSON.stringify(results));
"""


def _run(tmp_path: Path) -> dict[str, dict[str, object]]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    js = tmp_path / "web" / "js"
    shutil.copytree(WEB_JS, js)
    (tmp_path / "web" / "package.json").write_text('{"type": "module"}\n', encoding="utf-8")
    (js / "api.js").write_text(FAKE_API, encoding="utf-8")
    (js / "audio-in.js").write_text(FAKE_AUDIO_IN, encoding="utf-8")
    done = subprocess.run(
        [node, "--input-type=module", "-e", HARNESS, js.as_uri()],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    results: dict[str, dict[str, object]] = json.loads(done.stdout.strip().splitlines()[-1])
    return results


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, object]]:
    return _run(tmp_path_factory.mktemp("voice"))


@pytest.mark.parametrize("kind", ["leave", "hide", "pagehide"])
def test_leaving_closes_the_microphone_and_sends_nothing_more(
    results: dict[str, dict[str, object]], kind: str
) -> None:
    got = results[kind]
    assert got["signWhileOn"] is True
    assert got["log"][:2] == ["audio_start", "frame"]  # type: ignore[index]
    assert got["sentAfter"] == []  # the in-flight frame was dropped
    assert "audio_stop" not in got["log"]  # type: ignore[operator]
    assert got["log"][-1] == "cancel"  # type: ignore[index]  # the server drops what it heard
    assert got["on"] is False and got["micStopped"] is True and got["signAfter"] is False


def test_a_start_overtaken_by_leaving_closes_what_it_opened(
    results: dict[str, dict[str, object]],
) -> None:
    got = results["overtaken"]
    assert got["signWhileStarting"] is True  # the sign is up from the moment it starts opening
    assert got["closedAgain"] is True
    assert got["log"] == [] and got["on"] is False and got["signAfter"] is False


def test_the_sign_stays_up_while_any_part_of_the_page_has_the_microphone(
    results: dict[str, dict[str, object]],
) -> None:
    assert results["shared"] == {"stillUp": True, "downAtEnd": True}


def test_send_now_while_waiting_sends_once_the_server_is_idle(
    results: dict[str, dict[str, object]],
) -> None:
    got = results["sendNow"]
    assert got["beforeIdle"] == ["cancel"]  # only the barge-in, nothing sent yet
    assert got["after"] == ["audio_start", "frame", "frame", "audio_stop"]  # what was said, kept
    assert got["on"] is False and got["signAfter"] is False  # the microphone closed at the click


def test_discarding_a_recording_that_never_started_sends_no_cancel(
    results: dict[str, dict[str, object]],
) -> None:
    got = results["waiting"]
    assert got["before"] == ["cancel"]  # the start itself asked the reply to stop (barge-in)
    assert got["after"] == []  # leaving sends nothing more: no second cancel, no audio_start
    assert got["on"] is False and got["waiting"] is None
