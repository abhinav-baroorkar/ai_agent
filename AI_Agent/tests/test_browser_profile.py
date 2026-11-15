"""The browser profile is our own marked folder; the history wipe never leaves it (H1, H2)."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from sasha.browser.history import forget_history, forget_when_free
from sasha.browser.profile import (
    MARKER,
    ProfileError,
    in_use,
    prepare_profile,
    profile_dir,
    verify_profile,
)


def _fill(folder: Path) -> None:
    (folder / "Sessions").mkdir(parents=True, exist_ok=True)
    (folder / "Sessions" / "Session_1").write_text("https://x/?q=secret")
    (folder / "Cache").mkdir(exist_ok=True)
    for name in ("History", "History-journal", "Favicons", "Cookies", "Preferences"):
        (folder / name).write_text("data")


def _outside(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    _fill(outside)
    return outside


def test_a_new_profile_gets_the_marker_and_is_cleaned(tmp_path: Path) -> None:
    root = prepare_profile(tmp_path)
    assert root == profile_dir(tmp_path).resolve() and (root / MARKER).is_file()
    _fill(root / "Default")
    assert forget_history(root) == 5
    assert sorted(p.name for p in (root / "Default").iterdir()) == ["Cookies", "Preferences"]
    assert prepare_profile(tmp_path) == root  # a marked folder is reused


def test_default_linked_out_of_the_profile_is_left_alone(tmp_path: Path) -> None:
    root = prepare_profile(tmp_path)
    outside = _outside(tmp_path)
    (root / "Default").symlink_to(outside)
    assert forget_history(root) == 0
    assert (outside / "History").exists() and (outside / "Cache").is_dir()


def test_a_linked_profile_folder_is_refused(tmp_path: Path) -> None:
    other = tmp_path / "real-chrome"
    other.mkdir()
    (other / MARKER).write_text("even with a marker")
    _fill(other / "Default")
    data = tmp_path / "data"
    data.mkdir()
    profile_dir(data).symlink_to(other)
    with pytest.raises(ProfileError, match="link"):
        prepare_profile(data)
    assert forget_history(profile_dir(data)) == 0
    assert (other / "Default" / "History").exists()


def test_an_unmarked_folder_is_refused_with_a_reset_hint(tmp_path: Path) -> None:
    _fill(profile_dir(tmp_path) / "Default")  # e.g. a profile from before the marker
    with pytest.raises(ProfileError, match="remove the folder"):
        prepare_profile(tmp_path)
    assert not (profile_dir(tmp_path) / MARKER).exists()  # never marks a non-empty folder
    assert forget_history(profile_dir(tmp_path)) == 0
    assert (profile_dir(tmp_path) / "Default" / "History").exists()
    (profile_dir(tmp_path) / MARKER).symlink_to(tmp_path / "x")
    with pytest.raises(ProfileError):
        verify_profile(profile_dir(tmp_path))


def test_leaf_links_are_only_unlinked(tmp_path: Path) -> None:
    root = prepare_profile(tmp_path)
    outside = _outside(tmp_path)
    (root / "Default").mkdir()
    (root / "Default" / "History").symlink_to(outside / "History")
    (root / "Default" / "Cache").symlink_to(outside / "Cache")
    assert forget_history(root) == 2
    assert (outside / "History").exists() and (outside / "Cache").is_dir()
    assert not (root / "Default" / "History").exists()


def test_no_wipe_while_a_process_uses_the_profile(tmp_path: Path) -> None:
    root = prepare_profile(tmp_path)
    _fill(root / "Default")
    assert not in_use(root)
    sleeper = subprocess.Popen(["sleep", "30"], cwd=root)
    try:
        assert in_use(root)
        assert asyncio.run(forget_when_free(root, timeout=0.3)) == 0
        assert (root / "Default" / "History").exists()
    finally:
        sleeper.kill()
        sleeper.wait()
    os.symlink(f"host-{os.getpid()}", root / "SingletonLock")  # a live pid holds the lock
    assert in_use(root)
    (root / "SingletonLock").unlink()
    os.symlink("host-999999999", root / "SingletonLock")  # a stale lock
    assert not in_use(root)
    assert asyncio.run(forget_when_free(root)) == 5
