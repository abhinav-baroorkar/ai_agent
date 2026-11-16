"""The browser panel's `restoring` mode (contract v0.5: the clean-up after the owner's
window, while bots are refused and the server retries by itself).

Runs the real `clean.js` and `browser-takeover.js` under Node with a tiny stand-in
DOM. It checks that `restoring` survives cleaning, that `restore_problem` is plain
capped text, that the take-over strip says what is happening with no buttons, and
that a problem is shown as text. Skipped where Node isn't installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

WEB_JS = REPO_ROOT / "AI_Agent/src/sasha/avatar/web/js"

HARNESS = """
class Node0 {
  constructor(tag) {
    Object.assign(this, { tag, kids: [], attrs: {}, dataset: {}, className: '' });
    Object.assign(this, { hidden: false, disabled: false });
  }
  append(...k) { this.kids.push(...k); }
  replaceChildren(...k) { this.kids = k; }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener() {}
  get textContent() {
    return this.kids.map((k) => (typeof k === 'string' ? k : k.textContent)).join('');
  }
  all(tag) {
    const own = (k) => (k.tag === tag ? [k] : []);
    return this.kids.flatMap((k) => (typeof k === 'string' ? [] : [...own(k), ...k.all(tag)]));
  }
}
const make = (t) => new Node0(t);
globalThis.document = { createElement: make, createElementNS: (_, t) => make(t) };
globalThis.location = { search: '' };
const dir = process.argv[process.argv.length - 1];
const { cleanBrowserState } = await import(`${dir}/clean.js`);
const { initTakeover } = await import(`${dir}/browser-takeover.js`);
const root = new Node0('div');
const health = { assistant: { name: 'Helper' } };
const takeover = initTakeover(root, health);
const show = (raw) => {
  const s = cleanBrowserState(raw);
  takeover.show(s);
  const buttons = root.all('button').length;
  const text = root.textContent;
  return { mode: s.mode, problem: s.restore_problem, hidden: root.hidden, text, buttons };
};
const base = { enabled: true, running: false, agent: 'idle', tabs: [], guard: {} };
console.log(JSON.stringify({
  restoring: show({ ...base, mode: 'restoring', restore_problem: null }),
  stuck: show({ ...base, mode: 'restoring', restore_problem: 'The browser profile is in use' }),
  long: show({ ...base, mode: 'restoring', restore_problem: 'x'.repeat(500) }),
  odd: show({ ...base, mode: 'restoring', restore_problem: { html: '<b>' } }),
  unknown: show({ ...base, mode: 'cleaning' }),
}));
"""


def _run(tmp_path: Path) -> dict[str, dict[str, object]]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    js = tmp_path / "web" / "js"
    shutil.copytree(WEB_JS, js)
    (tmp_path / "web" / "package.json").write_text('{"type": "module"}\n', encoding="utf-8")
    (js / "api.js").write_text(
        "export const MOCK = false;\nexport async function api() { return {}; }\n", encoding="utf-8"
    )
    done = subprocess.run(
        [node, "--input-type=module", "-e", HARNESS, js.as_uri()],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    out: dict[str, dict[str, object]] = json.loads(done.stdout.strip().splitlines()[-1])
    return out


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, object]]:
    return _run(tmp_path_factory.mktemp("restoring"))


def test_restoring_says_what_is_happening_with_nothing_to_press(
    results: dict[str, dict[str, object]],
) -> None:
    got = results["restoring"]
    assert got["mode"] == "restoring" and got["hidden"] is False and got["buttons"] == 0
    assert "Cleaning up after your window" in str(got["text"])
    assert "Helper gets the browser back" in str(got["text"])  # the name comes from health
    assert "finished yet" not in str(got["text"])


def test_a_restore_problem_is_shown_as_capped_plain_text(
    results: dict[str, dict[str, object]],
) -> None:
    assert "The browser profile is in use" in str(results["stuck"]["text"])
    assert results["stuck"]["buttons"] == 0
    assert len(str(results["long"]["problem"])) == 200
    assert results["odd"]["problem"] == ""  # not a string: nothing is shown


def test_an_unknown_mode_shows_nothing(results: dict[str, dict[str, object]]) -> None:
    assert results["unknown"]["mode"] is None and results["unknown"]["hidden"] is True
