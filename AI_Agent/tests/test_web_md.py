"""The chat's formatting renderer (`avatar/web/js/md.js`) must stay fast on hostile lines.

It runs in the page on every streamed reply, so a pattern that backtracks on a long run of
spaces would freeze the window. The check runs the real file under Node with a minimal
stand-in for the DOM; it is skipped where Node is not installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tests.conftest import REPO_ROOT

MD = REPO_ROOT / "AI_Agent/src/sasha/avatar/web/js/md.js"
HARNESS = """
class Node {
  constructor(tag) {
    this.tagName = (tag || '#frag').toUpperCase();
    this.kids = [];
    this.text = '';
  }
  append(...xs) { this.kids.push(...xs); }
  get lastChild() { return this.kids[this.kids.length - 1]; }
  set textContent(v) { this.text = String(v); }
  get textContent() { return this.text; }
  flat() {
    return this.text + this.kids.map((k) => (typeof k === 'string' ? k : k.flat())).join('');
  }
  tags() {
    return this.kids.flatMap((k) => (typeof k === 'string' ? [] : [k.tagName, ...k.tags()]));
  }
}
globalThis.document = {
  createElement: (t) => new Node(t),
  createDocumentFragment: () => new Node(),
};
const [file, input] = process.argv.slice(-2);
// Node treats a bare .js file as CommonJS, so the module is loaded from its text.
const source = (await import('node:fs')).readFileSync(new URL(file), 'utf8');
const { renderMarkdown } = await import('data:text/javascript,' + encodeURIComponent(source));
const out = JSON.parse(input).map((text) => {
  const t0 = performance.now();
  const tree = renderMarkdown(text);
  return { ms: performance.now() - t0, tags: tree.tags(), text: tree.flat() };
});
console.log(JSON.stringify(out));
"""


def _render(cases: list[str]) -> list[dict[str, object]]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    done = subprocess.run(
        [node, "--input-type=module", "-e", HARNESS, MD.as_uri(), json.dumps(cases)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    result: list[dict[str, object]] = json.loads(done.stdout)
    return result


def test_hostile_lines_render_quickly() -> None:
    spaces = " " * 10_000
    cases = [
        f"# a{spaces}b",  # the heading that used to take seconds
        f"# a{spaces}",
        f"- item{spaces}x",
        f"1. item{spaces}x",
        "*" * 5_000,
        "_" * 5_000,
        "`" * 5_000,
        "[" * 3_000 + "](" * 1_000,
        f"**bold{spaces}",
        "```\n" + "x\n" * 5_000,
    ]
    for case, got in zip(cases, _render(cases), strict=True):
        assert float(str(got["ms"])) < 50, f"{case[:12]!r}... took {got['ms']} ms"


def test_subset_renders_as_expected_and_html_stays_text() -> None:
    text = (
        "## Title ##\n\nSome **bold** and *soft* `code`.\n\n"
        "1. one\n2. two\n\n<b>raw</b> [x](y.example)"
    )
    (got,) = _render([text])
    assert got["tags"] == ["H3", "P", "STRONG", "EM", "CODE", "OL", "LI", "LI", "P", "SPAN"]
    flat = str(got["text"])
    assert flat.startswith("Title") and "Title #" not in flat  # closing hashes dropped
    assert "<b>raw</b>" in flat and "(y.example)" in flat  # shown as written, not as markup
