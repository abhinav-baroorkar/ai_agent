"""The browser panel says something true when the brain refuses this window (owner bug,
2026-11-17: after a brain restart his window kept an old window token, the panel's
socket was refused before accept, which the page sees only as close code 1006, and the
panel retried quietly 122 times).

The real `browser-link.js` and `browser-panel.js` run under Node with a fake socket and a
fake api. After a failed socket the link asks GET /api/browser with the same token:
- 401 (not this start's token) and 403 (not allowed) stop the retrying with a reason;
- any other answer, or none, keeps the usual retrying.
The words name the assistant from the health check, never from the code.
Skipped where Node isn't installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tests.conftest import REPO_ROOT

WEB_JS = REPO_ROOT / "AI_Agent/src/sasha/avatar/web/js"

FAKE_API = """export const MOCK = false;
export const HAS_TOKEN = true;
export const calls = { sockets: 0, gets: 0 };
export const answer = { status: 0 }; // 0: the GET works
export function openBrowserSocket() {
  calls.sockets += 1;
  const ws = { send() {}, close() {}, readyState: 0 };
  setTimeout(() => ws.onclose && ws.onclose({ code: 1006 }), 0); // the handshake failed
  return ws;
}
export async function api(method, path) {
  calls.gets += 1;
  if (answer.status === -1) throw new Error('No answer.'); // the brain is down: no status
  if (answer.status) throw Object.assign(new Error('refused'), { status: answer.status });
  return { enabled: true };
}
"""

HARNESS = """
globalThis.document = { createElement: () => ({}), createElementNS: () => ({}) };
globalThis.location = { search: '', hash: '' };
globalThis.CustomEvent ??= class extends Event { // Node 18 has Event but no CustomEvent
  constructor(type, opts) { super(type); this.detail = opts?.detail; }
};
const dir = process.argv[process.argv.length - 1];
const { calls, answer } = await import(`${dir}/api.js`);
const { BrowserLink } = await import(`${dir}/browser-link.js`);
const { refusedText } = await import(`${dir}/browser-panel.js`);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const out = {};
for (const [name, status] of [['old_token', 401], ['forbidden', 403], ['server_error', 500],
  ['brain_down', -1], ['get_works', 0]]) {
  answer.status = status;
  calls.sockets = 0;
  calls.gets = 0;
  const link = new BrowserLink();
  link.open();
  await wait(1300); // past the first retry (1 s): a link that keeps trying opens again
  const { sockets, gets } = calls;
  out[name] = { status: link.status, reason: link.reason, sockets, gets };
  link.close();
}
out.text = {
  token: refusedText('token', true, 'Helper'),
  token_without_one: refusedText('token', false, 'Helper'),
  forbidden: refusedText('forbidden', true, 'Helper'),
};
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, object]]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    js = tmp_path_factory.mktemp("refused") / "web" / "js"
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
    out: dict[str, dict[str, object]] = json.loads(done.stdout.strip().splitlines()[-1])
    return out


@pytest.mark.parametrize(("case", "reason"), [("old_token", "token"), ("forbidden", "forbidden")])
def test_a_refused_window_stops_retrying_and_says_why(
    runs: dict[str, dict[str, object]], case: str, reason: str
) -> None:
    assert runs[case] == {"status": "refused", "reason": reason, "sockets": 1, "gets": 1}


@pytest.mark.parametrize("case", ["server_error", "brain_down", "get_works"])
def test_any_other_failure_keeps_retrying(runs: dict[str, dict[str, object]], case: str) -> None:
    got = runs[case]
    assert got["reason"] == "" and got["status"] != "refused"
    assert got["sockets"] == 2  # tried again after the first back-off


def test_the_words_are_true_and_name_the_assistant_from_config(
    runs: dict[str, dict[str, object]],
) -> None:
    text = runs["text"]
    assert text["token"] == (
        "This window was opened for an earlier start of Helper. Close it and open it again."
    )
    assert "sasha window" in str(text["token_without_one"])  # no token at all: how to open one
    assert "refused" in str(text["forbidden"])
