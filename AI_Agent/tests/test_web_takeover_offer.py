"""A failed web lookup offers Take over right in the chat (owner bug, 2026-11-16: he
could not find the browser panel to solve the search site's person check).

- The chat words its offer by matching the server's own person-check line. The
  pattern is read from chat.js and checked against browser/pages.py, so a change of
  wording on either side fails here, not on his screen.
- The offer's button does what the panel's Take over does, and only when that is
  possible: the real browser-takeover.js runs under Node with a stand-in DOM and a
  fake api. Skipped where Node isn't installed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from sasha.browser.pages import NOT_RESULTS, PERSON_CHECK
from tests.conftest import REPO_ROOT

WEB_JS = REPO_ROOT / "AI_Agent/src/sasha/avatar/web/js"


def _chat_pattern() -> re.Pattern[str]:
    source = (WEB_JS / "chat.js").read_text(encoding="utf-8")
    found = re.search(r"^const PERSON_CHECK = /(.+)/i;$", source, re.MULTILINE)
    assert found, "chat.js no longer has its PERSON_CHECK pattern"
    return re.compile(found.group(1), re.IGNORECASE)


def test_the_chat_recognises_the_servers_person_check_line() -> None:
    pattern = _chat_pattern()
    assert pattern.search(PERSON_CHECK)
    assert pattern.search(PERSON_CHECK.replace("'", "\u2019"))  # a curly apostrophe too
    assert not pattern.search(NOT_RESULTS)  # "may want to check": the milder wording


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
console.log(JSON.stringify(out));
"""

FAKE_API = """export const MOCK = false;
export const posted = [];
export async function api(method, path) { posted.push(`${method} ${path}`); return { ok: true }; }
"""


@pytest.fixture(scope="module")
def presses(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[str]]:
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
    out: dict[str, list[str]] = json.loads(done.stdout.strip().splitlines()[-1])
    return out


def test_take_over_from_the_chat_is_the_panels_own_action(presses: dict[str, list[str]]) -> None:
    assert presses["assistant"] == ["POST /api/browser/take_over"]


@pytest.mark.parametrize("when", ["off", "owner", "restoring", "old_server"])
def test_take_over_from_the_chat_sends_nothing_when_it_cannot_start(
    presses: dict[str, list[str]], when: str
) -> None:
    assert presses[when] == []
