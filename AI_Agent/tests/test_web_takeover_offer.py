"""A failed web lookup offers Take over right in the chat (owner bug, 2026-11-16: he
could not find the browser panel to solve the search site's person check).

- The card shows only for the search site's person check, from the server's fixed label
  on the reply's own tool calls (asks == "person_check"), never from text, and not when
  a web tool also worked (review L1, L2).
- The panel shows itself while BrowserState.person_check is true, so the server's line
  "Take over in the browser panel beside this chat" stays true after the browser stops.
- The offer's button does what the panel's Take over does, and only when that is
  possible: the real browser-takeover.js runs under Node with a stand-in DOM and a
  fake api. Skipped where Node isn't installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tests.conftest import REPO_ROOT

WEB_JS = REPO_ROOT / "AI_Agent/src/sasha/avatar/web/js"


HARNESS = """
class Node0 {
  constructor(tag) {
    Object.assign(this, { tag, kids: [], attrs: {}, dataset: {}, hidden: false });
  }
  append(...k) { this.kids.push(...k); }
  replaceChildren(...k) { this.kids = k; }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener() {}
}
const make = (t) => new Node0(t);
globalThis.document = { createElement: make, createElementNS: (_, t) => make(t) };
globalThis.location = { search: '', hash: '' };
const dir = process.argv[process.argv.length - 1];
const { posted } = await import(`${dir}/api.js`);
const { initTakeover } = await import(`${dir}/browser-takeover.js`);
const takeover = initTakeover(make('div'), { assistant: { name: 'Helper' } });
const tick = () => new Promise((r) => setTimeout(r, 0));
const out = {};
for (const [name, state] of [
  ['assistant', { mode: 'assistant', enabled: true }],
  ['off', { mode: 'assistant', enabled: false }],
  ['owner', { mode: 'owner', enabled: true }],
  ['restoring', { mode: 'restoring', enabled: true }],
  ['old_server', { mode: null, enabled: true }],
]) {
  posted.length = 0;
  takeover.show({ owner_refused: {}, owner_until: 0, ...state });
  takeover.start();
  takeover.start(); // a second press while the first is on its way sends nothing more
  await tick();
  out[name] = posted.slice();
}
const { metPersonCheck, cleanBrowserState } = await import(`${dir}/clean.js`);
const { showsByItself } = await import(`${dir}/browser-panel.js`);
const web = (name) => ['browse', 'search', 'open_url'].includes(name);
const check = { name: 'search', ok: false, asks: 'person_check' };
const turns = {
  person_check: [check],
  helper_person_check: [{ name: 'browse', ok: false, asks: 'person_check' }],
  nothing_found: [{ name: 'search', ok: false, asks: 'nothing_found' }],
  failed_without_label: [{ name: 'search', ok: false }],
  check_then_worked: [check, { name: 'open_url', ok: true }],
  plain_question_after_a_stop: [], // the next reply's own calls: nothing of the stopped turn
  no_tool_calls_field: undefined,
  label_on_another_tool: [{ name: 'call_service', ok: false, asks: 'person_check' }],
  label_not_exact: [{ name: 'search', ok: false, asks: 'Person_check ' }],
  label_on_a_tool_that_worked: [{ name: 'search', ok: true, asks: 'person_check' }],
  junk: [null, 'search', { name: 'search', ok: 'no', asks: 'person_check' }],
};
const card = ([k, calls]) => [k, metPersonCheck(calls, web)];
out.card = Object.fromEntries(Object.entries(turns).map(card));
const flags = { yes: true, text_true: 'true', one: 1, null: null, missing: undefined };
const flag = ([k, v]) => [k, cleanBrowserState({ person_check: v }).person_check];
out.flag = Object.fromEntries(Object.entries(flags).map(flag));
const idle = cleanBrowserState({ enabled: true, running: false, mode: 'assistant' });
out.panel = {
  idle: showsByItself('open', idle, false),
  person_check_waits: showsByItself('open', { ...idle, person_check: true }, false),
  person_check_link_down: showsByItself('offline', { ...idle, person_check: true }, false),
  running: showsByItself('open', { ...idle, running: true }, false),
  owner: showsByItself('open', { ...idle, mode: 'owner' }, false),
  just_stopped: showsByItself('open', idle, true),
};
console.log(JSON.stringify(out));
"""

FAKE_API = """export const MOCK = false;
export const HAS_TOKEN = false;
export function openBrowserSocket() { throw new Error("no sockets in this test"); }
export const posted = [];
export async function api(method, path) { posted.push(`${method} ${path}`); return { ok: true }; }
"""


@pytest.fixture(scope="module")
def presses(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    js = tmp_path_factory.mktemp("offer") / "web" / "js"
    shutil.copytree(WEB_JS, js)
    (js.parent / "package.json").write_text('{"type": "module"}\n', encoding="utf-8")
    (js / "api.js").write_text(FAKE_API, encoding="utf-8")
    done = subprocess.run(
        [node, "--input-type=module", "-e", HARNESS, js.as_uri()],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    out: dict[str, object] = json.loads(done.stdout.strip().splitlines()[-1])
    return out


def test_take_over_from_the_chat_is_the_panels_own_action(presses: dict[str, object]) -> None:
    assert presses["assistant"] == ["POST /api/browser/take_over"]


@pytest.mark.parametrize("when", ["off", "owner", "restoring", "old_server"])
def test_take_over_from_the_chat_sends_nothing_when_it_cannot_start(
    presses: dict[str, object], when: str
) -> None:
    assert presses[when] == []


def test_the_card_shows_only_for_the_person_check_label(presses: dict[str, object]) -> None:
    """Review L1 and L2: from this reply's own tool calls and the server's fixed label,
    exact compare; another failure reason, a lookup that also worked, a stopped turn or
    a label on a non-web tool give no card."""
    assert presses["card"] == {
        "person_check": True,
        "helper_person_check": True,
        "nothing_found": False,
        "failed_without_label": False,
        "check_then_worked": False,
        "plain_question_after_a_stop": False,
        "no_tool_calls_field": False,
        "label_on_another_tool": False,
        "label_not_exact": False,
        "label_on_a_tool_that_worked": False,
        "junk": False,
    }


def test_person_check_in_the_browser_state_is_a_strict_yes(presses: dict[str, object]) -> None:
    flags = {"yes": True, "text_true": False, "one": False, "null": False, "missing": False}
    assert presses["flag"] == flags


def test_the_panel_stays_up_while_a_person_check_waits(presses: dict[str, object]) -> None:
    assert presses["panel"] == {
        "idle": False,
        "person_check_waits": True,
        "person_check_link_down": False,
        "running": True,
        "owner": True,
        "just_stopped": True,
    }


def test_nothing_in_the_chat_reads_the_reply_text_for_the_card() -> None:
    source = (WEB_JS / "chat.js").read_text(encoding="utf-8")
    assert "lookupFailed" not in source and "PERSON_CHECK" not in source
    assert "metPersonCheck(m.tool_calls" in source  # decided in finish() from the reply itself
