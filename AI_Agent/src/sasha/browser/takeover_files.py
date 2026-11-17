"""The take-over's files and flags (browser-design.md §9 with C1-C3): pure, no Chrome.

- `visible_flags`: the owner's visible window on the assistant's own profile, with no
  DevTools connection of any kind, no extensions, no sync, no background mode, and
  everything through the guard proxy (loopback included).
- C1, nothing lasting from his window: the configuration files are snapshotted before
  and restored after; extension folders are removed, and the next headless start is
  refused while any remain.
- `seed_takeover_preferences`: permissions blocked, downloads blocked (and, if Chrome
  ignores that, kept in a folder that is deleted at hand-back), autofill and password
  saving off. Unlike the counts-only seed,
  a failure here refuses the take-over.
- `TAKEOVER_WIPE`: removed at hand-back on top of the usual history wipe.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from sasha.browser.chrome import BACKGROUND_FEATURES_OFF, BACKGROUND_FLAGS
from sasha.server.launch import ISOLATION_FLAGS
from sasha.server.window_token import write_private

CONFIG_FILES = ("Default/Preferences", "Default/Secure Preferences", "Local State")
EXTENSION_DIRS = (
    "Default/Extensions",
    "Default/Local Extension Settings",
    "Default/Extension State",
    "Default/Extension Rules",
    "Default/Extension Scripts",  # review S2
    "Default/Sync Extension Settings",
    "Default/Managed Extension Settings",
)
DOWNLOADS = "takeover-downloads"
# The owner's window opens on this local page when a person check is waiting: it
# forwards to the search that met the check, so the query never reaches a command line
# (C2: /proc is readable by other users). 0600 in the 0700 profile; wiped at hand-back.
CHECK_PAGE = "takeover-check.html"
TAKEOVER_WIPE = (DOWNLOADS, "Default/Web Data", "Default/Web Data-journal",
                 "Default/Login Data", "Default/Login Data-journal", CHECK_PAGE)  # fmt: skip
BLOCKED = ("media_stream_mic", "media_stream_camera", "geolocation", "notifications",
           "usb_guard", "serial_guard", "hid_guard", "midi_sysex", "clipboard",
           # review S1
           "file_system_write_guard", "bluetooth_guard", "bluetooth_scanning", "nfc",
           "idle_detection", "sensors", "window_placement", "local_fonts", "ar", "vr",
           "automatic_downloads", "protocol_handlers", "payment_handler",
           "storage_access")  # fmt: skip
BLOCK_ALL_DOWNLOADS = 3  # Chrome's download_restrictions: block every download (S2)


class TakeoverRefused(RuntimeError):
    """The take-over can't start safely (shown to the owner as is)."""


def engine_home(search_template: str | None) -> str:
    """The search engine's home page: scheme and host only, never a query (C2)."""
    parts = urlsplit(search_template or "")
    if parts.scheme != "https" or not parts.hostname:
        return "about:blank"
    return f"https://{parts.hostname}/"


def start_page(root: Path, search_template: str | None, blocked: str | None) -> str:
    """What the owner's window opens (found live, 2026-11-16: it opened the engine's home
    page, so the check he was asked to solve wasn't there). With a search that met a
    person check on the engine's own https site: a private local page that forwards to
    that search, where the engine shows its check again. Otherwise the home page (C2).
    The address is the server's own (search_url), never a page's or the model's."""
    home = engine_home(search_template)
    if blocked is None or home == "about:blank":
        return home
    parts = urlsplit(blocked)
    if parts.scheme != "https" or parts.hostname != urlsplit(home).hostname:
        return home
    # "<" escaped, so no "</script>" can ever end the script early (review nit)
    target = json.dumps(blocked).replace("<", "\\u003c")
    html = (
        "<!doctype html><meta charset=utf-8><title>Opening the check…</title>"
        f"<script>location.replace({target});</script>"
    )
    return write_private(root / CHECK_PAGE, html.encode()).as_uri()


def visible_flags(profile: Path, proxy_port: int, home: str) -> list[str]:
    replaced = ("--host-resolver-rules=", "--disable-features=")
    isolation = [f for f in ISOLATION_FLAGS if not f.startswith(replaced)]
    window_off = next(
        (f.split("=", 1)[1] for f in ISOLATION_FLAGS if f.startswith("--disable-features=")), ""
    )
    features = [*window_off.split(","), *BACKGROUND_FEATURES_OFF]
    disabled = ",".join(dict.fromkeys(x for x in features if x))
    return [
        f"--user-data-dir={profile}",
        *isolation,
        f"--disable-features={disabled}",
        *BACKGROUND_FLAGS,
        "--disable-extensions",  # C1: no extension can be installed or run
        "--disable-sync",
        "--disable-background-mode",  # S1: closing the window ends the process
        "--disable-quic",
        "--deny-permission-prompts",
        "--no-first-run",
        f"--proxy-server=http://127.0.0.1:{proxy_port}",
        "--proxy-bypass-list=<-loopback>",  # even loopback goes through the guard
        "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
        home,
    ]


def snapshot_config(root: Path, keep: Path) -> None:
    """Copy the configuration files aside (C1). `keep` is a fresh 0700 folder."""
    keep.mkdir(parents=True, exist_ok=False, mode=0o700)
    for rel in CONFIG_FILES:
        src = root / rel
        if src.is_file() and not src.is_symlink():
            (keep / rel).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(src, keep / rel)


def restore_config(root: Path, keep: Path) -> None:
    """Put the configuration back as it was before the take-over (C1); a file that
    didn't exist before is removed."""
    for rel in CONFIG_FILES:
        target, saved = root / rel, keep / rel
        with contextlib.suppress(FileNotFoundError):
            if target.is_symlink() or target.is_file():
                target.unlink()
        if saved.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(saved, target)
    shutil.rmtree(keep, ignore_errors=True)


# Where an installed extension lives: one folder per extension id. Chrome itself makes
# the other EXTENSION_DIRS (Extension State, Rules, Scripts: empty LevelDBs) at every
# start, also headless with no extension at all (measured 2026-11-16), so they never
# count; "Temp" is Chrome's own unpack folder.
INSTALLED = "Default/Extensions"
NOT_AN_EXTENSION = frozenset({"Temp"})


def extensions_present(root: Path) -> bool:
    """C1: a real installed extension (a folder in Default/Extensions), never Chrome's
    own state databases; a link there counts too (fails closed)."""
    folder = root / INSTALLED
    if folder.is_symlink():
        return True
    if not folder.is_dir():
        return False
    return any(entry.name not in NOT_AN_EXTENSION for entry in folder.iterdir())


def clear_extensions(root: Path) -> None:
    for rel in EXTENSION_DIRS:
        path = root / rel
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def wipe_takeover_files(root: Path) -> None:
    for rel in TAKEOVER_WIPE:
        path = root / rel
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def seed_takeover_preferences(root: Path) -> None:
    """Block permissions, send downloads to a folder that is deleted at hand-back, and
    turn off autofill and password saving. Raises TakeoverRefused on any failure."""
    default = root / "Default"
    path = default / "Preferences"
    try:
        default.mkdir(mode=0o700, exist_ok=True)
        if default.is_symlink() or default.resolve(strict=True) != default or path.is_symlink():
            raise TakeoverRefused("The browser profile has a link in it")
        prefs = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if not isinstance(prefs, dict):
            prefs = {}
        profile = prefs.setdefault("profile", {})
        profile.setdefault("default_content_setting_values", {}).update(dict.fromkeys(BLOCKED, 2))
        profile["password_manager_enabled"] = False
        prefs["credentials_enable_service"] = False
        prefs.setdefault("autofill", {}).update(profile_enabled=False, credit_card_enabled=False)
        downloads = root / DOWNLOADS
        downloads.mkdir(mode=0o700, exist_ok=True)
        prefs["download_restrictions"] = BLOCK_ALL_DOWNLOADS  # a challenge needs none
        prefs["download"] = {
            "default_directory": str(downloads),
            "prompt_for_download": False,
            "directory_upgrade": True,
        }
        tmp = default / ".Preferences.takeover-tmp"
        tmp.unlink(missing_ok=True)
        tmp.write_text(json.dumps(prefs), encoding="utf-8")
        os.replace(tmp, path)
    except (OSError, ValueError) as exc:
        raise TakeoverRefused(
            f"The browser's settings could not be prepared ({type(exc).__name__})"
        ) from None
