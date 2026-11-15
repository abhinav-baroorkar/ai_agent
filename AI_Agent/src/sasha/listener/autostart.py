"""`sasha autostart on|off|show`: start the brain and the listener at graphical login.

The unit files are generated inside the project (`Data/autostart/`). `show` and `on`
print both files in full, the exact link paths, and the undo command; `on` then links
and enables them only with `--yes`. The only write outside the project is what
`systemctl --user link` and `enable` create under `~/.config/systemd/user/`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

BRAIN = "sasha-brain.service"
LISTEN = "sasha-listen.service"
UNDO = f"systemctl --user disable --now {LISTEN} {BRAIN}"
Runner = Callable[[Sequence[str]], int]


def _quote(value: object) -> str:
    """systemd double-quoting: backslash, double quote and `%` (a specifier) escaped."""
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{text}"'


def _environment(root: Path) -> list[str]:
    deps = root / "Dependencies"
    values = {
        "SASHA_ROOT": root,
        "HF_HOME": deps / "hf",
        "XDG_CACHE_HOME": deps / "cache",
        "TMPDIR": deps / "tmp",
        "UV_CACHE_DIR": deps / "uv-cache",
    }
    return [f"Environment={_quote(f'{k}={v}')}" for k, v in values.items()]


def _service(root: Path, python: Path, command: str) -> list[str]:
    return [
        "[Service]",
        "Type=simple",
        f"WorkingDirectory={str(root).replace('%', '%%')}",  # a path: no quotes here
        *_environment(root),
        "UMask=0077",
        f"ExecStart={_quote(python)} -m sasha {command}",
        "KillMode=control-group",
        "Restart=on-failure",
        "RestartSec=5",
        "",
        "[Install]",
        "WantedBy=graphical-session.target",
        "",
    ]


def unit_texts(root: Path, python: Path) -> dict[str, str]:
    """Both units, exactly as written to disk."""
    limits = ["StartLimitIntervalSec=600", "StartLimitBurst=5"]
    brain = [
        "[Unit]",
        "Description=Local voice assistant: brain server (sasha serve)",
        "PartOf=graphical-session.target",
        "After=graphical-session.target",
        *limits,
        "",
        *_service(root, python, "serve"),
    ]
    listen = [
        "[Unit]",
        "Description=Local voice assistant: wake word listener (sasha listen)",
        f"Wants={BRAIN}",
        f"After={BRAIN} graphical-session.target pipewire.service wireplumber.service",
        "PartOf=graphical-session.target",
        *limits,
        "",
        *_service(root, python, "listen"),
    ]
    return {BRAIN: "\n".join(brain), LISTEN: "\n".join(listen)}


def unit_dir(root: Path) -> Path:
    return root / "Data" / "autostart"


def link_paths(home: Path) -> list[Path]:
    """Where `systemctl --user link` puts the symlinks (the `enable` symlinks go under
    `graphical-session.target.wants/` in the same folder)."""
    base = home / ".config" / "systemd" / "user"
    return [
        base / BRAIN,
        base / LISTEN,
        base / "graphical-session.target.wants" / BRAIN,
        base / "graphical-session.target.wants" / LISTEN,
    ]


def check_python(root: Path, python: Path) -> None:
    # Not resolved: a venv's bin/python is a symlink to uv's interpreter outside the
    # project, and the unit must start the venv's own path.
    path, base = Path(os.path.abspath(python)), Path(os.path.abspath(root))
    hint = "Run from the main checkout after `source AI_Agent/scripts/env.sh`."
    if not path.is_relative_to(base):
        raise ValueError(
            f"The Python running this ({python}) is outside the project {root}. {hint}"
        )
    if path.is_relative_to(base / "Dependencies" / "worktrees"):
        raise ValueError(f"The Python running this ({python}) belongs to a worktree. {hint}")


def write_units(root: Path, python: Path) -> list[Path]:
    folder = unit_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, text in unit_texts(root, python).items():
        path = folder / name
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    return paths


def _run(cmd: Sequence[str]) -> int:
    return subprocess.run(list(cmd), check=False).returncode


def autostart(
    action: str,  # root: the main checkout (`model_cache.project_root`)
    root: Path,
    yes: bool = False,
    python: Path | None = None,
    home: Path | None = None,
    run: Runner = _run,
    out: Callable[[str], None] = print,
) -> int:
    python = python or Path(sys.executable)
    home = home or Path.home()
    if action == "off":
        out(f"Running: {UNDO}")
        return run(UNDO.split())
    try:
        check_python(root, python)
    except ValueError as e:
        out(f"[refused] {e}")
        return 2
    paths = write_units(root, python)
    for path in paths:
        out(f"----- {path} -----")
        out(path.read_text(encoding="utf-8"))
    out("Links that `on --yes` creates (nothing else outside the project):")
    for link in link_paths(home):
        out(f"  {link}")
    out(f"Undo: {UNDO}  (or: sasha autostart off)")
    out("`enable` takes effect at the next graphical login; nothing starts now.")
    if action == "show":
        return 0
    if not yes:
        out("[not linked] Re-run with --yes to link and enable these units.")
        return 1
    for path in paths:
        if (rc := run(["systemctl", "--user", "link", str(path)])) != 0:
            return rc
    rc = run(["systemctl", "--user", "enable", BRAIN, LISTEN])
    if rc == 0:
        out("Enabled. They start at your next graphical login (nothing was started now).")
    return rc
