"""Breathing styles (`avatar/web/js/faces/styles.js`): a style name from the server can
only ever pick one of the real styles.

The name arrives as data. A name that is a built-in property of every object
('constructor', '__proto__', 'toString') must fall back to the default instead of
returning something with no `move`, which would break the orb's drawing every frame.
Runs the real module under Node; skipped where Node isn't installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tests.conftest import REPO_ROOT

STYLES = REPO_ROOT / "AI_Agent/src/sasha/avatar/web/js/faces/styles.js"
HARNESS = """
const source = (await import('node:fs')).readFileSync(new URL(process.argv.at(-1)), 'utf8');
const url = 'data:text/javascript,' + encodeURIComponent(source);
const { styleOf, STYLES, STYLE_IDS } = await import(url);
const names = ['constructor', '__proto__', 'toString', 'hasOwnProperty', 'valueOf', '', 'nope',
  null, undefined, 42, ...STYLE_IDS];
const out = names.map((n) => {
  const s = styleOf(n);
  const moved = s.move({ x: 0.1, y: -0.5, z: 0.2, ph: 1, inner: 0 }, 1.3, 1, 0.5);
  return { name: String(n), picked: s.name, isDefault: s === STYLES.glow,
    moveIsFunction: typeof s.move === 'function', moveOk: moved === null || moved.length === 4 };
});
console.log(JSON.stringify(out));
"""
ATTACKS = ["constructor", "__proto__", "toString", "hasOwnProperty", "valueOf", "", "nope"]
OTHER = ["null", "undefined", "42"]


@pytest.fixture(scope="module")
def picked() -> dict[str, dict[str, object]]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    done = subprocess.run(
        [node, "--input-type=module", "-e", HARNESS, STYLES.as_uri()],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return {row["name"]: row for row in json.loads(done.stdout)}


@pytest.mark.parametrize("name", ATTACKS + OTHER)
def test_names_that_are_not_styles_fall_back_to_the_default(
    picked: dict[str, dict[str, object]], name: str
) -> None:
    row = picked[name]
    assert row["isDefault"] is True and row["moveIsFunction"] is True and row["moveOk"] is True


def test_every_real_style_is_found_and_moves(picked: dict[str, dict[str, object]]) -> None:
    real = {n: r for n, r in picked.items() if n not in ATTACKS + OTHER}
    assert len(real) == 8
    for name, row in real.items():
        assert row["moveIsFunction"] is True and row["moveOk"] is True, name
        assert row["isDefault"] is (name == "glow"), name
