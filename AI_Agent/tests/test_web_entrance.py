"""The wake entrance's timing (`avatar/web/js/faces/entrance.js`), run under Node.

Visual only, so what is tested is the shape the design review tunes: it starts
scattered and dim, ends in place at full brightness after `ms`, never overshoots, and
its numbers live in one object. Skipped where Node is not installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

ENTRANCE = REPO_ROOT / "AI_Agent/src/sasha/avatar/web/js/faces/entrance.js"
SCRIPT = """
const { ENTRANCE, entranceAt } = await import('file://' + process.argv[1]);
const at = (ms) => entranceAt(1000, 1000 + ms);
const ks = Array.from({ length: 101 }, (_, i) => at((ENTRANCE.ms * i) / 100).k);
console.log(JSON.stringify({
  ms: ENTRANCE.ms,
  start: at(0), mid: at(ENTRANCE.ms / 2), end: at(ENTRANCE.ms), after: at(ENTRANCE.ms * 3),
  fade: at(ENTRANCE.fadeMs).alpha, before: at(-50),
  monotonic: ks.every((k, i) => i === 0 || k >= ks[i - 1]),
  bounded: ks.every((k) => k >= 0 && k <= 1),
  keys: Object.keys(ENTRANCE),
}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_the_entrance_starts_scattered_and_settles_in_about_half_a_second(tmp_path: Path) -> None:
    module = tmp_path / "entrance.mjs"  # Node reads a .js without package.json as CommonJS
    module.write_text(ENTRANCE.read_text(encoding="utf-8"), encoding="utf-8")
    out = subprocess.run(
        ["node", "--input-type=module", "-e", SCRIPT, str(module)],
        capture_output=True, text=True, timeout=30, check=True,
    )  # fmt: skip
    r = json.loads(out.stdout)
    assert 300 <= r["ms"] <= 800  # "about half a second"
    assert r["start"] == {"p": 0, "k": 0, "alpha": 0, "done": False}
    assert r["before"]["p"] == 0 and r["before"]["alpha"] <= 0
    assert 0.5 < r["mid"]["k"] < 1  # ease-out: most of the way by half time
    assert r["end"]["done"] and r["end"]["k"] == 1 and r["end"]["alpha"] == 1
    assert r["after"]["k"] == 1 and r["fade"] == 1
    assert r["monotonic"] and r["bounded"]
    assert {"ms", "scatter", "twist", "startScale", "fadeMs", "ease"} <= set(r["keys"])
