"""The wake entrance (`avatar/web/js/faces/entrance.js`) and when it plays, under Node.

Visual only, so what is tested is the shape the design review tunes: a calm arrival of
about 0.9 s that starts dimmed and a little scattered and ends in place at full
brightness, never overshooting; a brighten-only version (no shrink) when the window was
already in view on Home; and the shell's decision between the two. Skipped where Node
is not installed.
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
const at = (ms, full = true) => entranceAt(1000, 1000 + ms, full);
const ks = Array.from({ length: 101 }, (_, i) => at((ENTRANCE.ms * i) / 100).k);
const { ms, scatter, twist, startScale, fadeMs } = ENTRANCE;
console.log(JSON.stringify({
  values: { ms, scatter, twist, startScale, fadeMs },
  start: at(0), mid: at(ENTRANCE.ms / 2), end: at(ENTRANCE.ms), after: at(ENTRANCE.ms * 3),
  fade: at(ENTRANCE.fadeMs).bright,
  soft_start: at(0, false), soft_end: at(ENTRANCE.fadeMs, false),
  brighten_from: ENTRANCE.brightenFrom,
  monotonic: ks.every((k, i) => i === 0 || k >= ks[i - 1]),
  bounded: ks.every((k) => k >= 0 && k <= 1),
}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_the_entrance_is_a_calm_arrival(tmp_path: Path) -> None:
    """Design review: about 0.9 s, ease-out quadratic, a smaller shrink and scatter."""
    module = tmp_path / "entrance.mjs"  # Node reads a .js without package.json as CommonJS
    module.write_text(ENTRANCE.read_text(encoding="utf-8"), encoding="utf-8")
    out = subprocess.run(
        ["node", "--input-type=module", "-e", SCRIPT, str(module)],
        capture_output=True, text=True, timeout=30, check=True,
    )  # fmt: skip
    r = json.loads(out.stdout)
    assert r["values"] == {
        "ms": 900, "scatter": 2.2, "twist": 1.0, "startScale": 0.6, "fadeMs": 450,
    }  # fmt: skip
    assert r["start"]["k"] == 0 and r["start"]["bright"] == 0 and not r["start"]["done"]
    assert abs(r["mid"]["k"] - 0.75) < 1e-9  # quadratic ease-out: 1 - 0.5 ** 2
    assert r["end"]["done"] and r["end"]["k"] == 1 and r["end"]["bright"] == 1
    assert r["after"]["k"] == 1 and r["fade"] == 1
    assert r["monotonic"] and r["bounded"]
    # already in view: no shrink (k = 1 throughout), only a brightening from brightenFrom
    assert r["soft_start"]["k"] == 1 and abs(r["soft_start"]["bright"] - r["brighten_from"]) < 1e-9
    assert r["soft_end"]["bright"] == 1


WAKE_HARNESS = """
const events = [];
globalThis.location = { hash: process.argv[2] };
globalThis.window = { dispatchEvent: (e) => events.push(e.detail) };
globalThis.CustomEvent = class { constructor(t, o) { this.type = t; this.detail = o.detail; } };
eval(process.argv[1]);
console.log(JSON.stringify({ events, hash: location.hash, early: window.assistantWake.entrance }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
@pytest.mark.parametrize(
    ("just_shown", "hash_before", "entrance"),
    [
        (True, "#home", True),  # brought up from hidden or behind: the full entrance
        (False, "#home", False),  # already in view on Home: brighten only, no shrink
        (False, "", False),  # Home is the default screen
        (False, "#bots", True),  # in view, but on another screen: Home comes in
    ],
)
def test_the_shell_says_when_the_full_entrance_plays(
    just_shown: bool, hash_before: str, entrance: bool
) -> None:
    from sasha.app.shell import wake_js

    out = subprocess.run(
        ["node", "-e", WAKE_HARNESS, wake_js(just_shown), hash_before],
        capture_output=True, text=True, timeout=30, check=True,
    )  # fmt: skip
    r = json.loads(out.stdout)
    assert r["events"] == [{"entrance": entrance}] and r["early"] is entrance
    assert r["hash"] == "home"
