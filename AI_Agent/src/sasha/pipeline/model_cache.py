"""Where downloaded model weights live: always inside the project, never in ~/.cache.

In a git worktree under `<main>/Dependencies/worktrees/<name>/`, the cache belongs to
the main checkout (the same rule as `scripts/env.sh`), so models download once.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_WORKTREES = f"{os.sep}Dependencies{os.sep}worktrees{os.sep}"
_HF_VARS = ("HF_HOME", "HF_HUB_CACHE", "HF_XET_CACHE", "HF_ASSETS_CACHE")


class OutsideProjectError(RuntimeError):
    """A cache path points outside the project folder."""


def project_root(root: Path) -> Path:
    """The main checkout: `root` itself, or the folder above `Dependencies/worktrees/`."""
    text = str(root.resolve())
    return Path(text.split(_WORKTREES, 1)[0]) if _WORKTREES in text else Path(text)


def require_inside(path: Path, root: Path, what: str) -> Path:
    """Return `path` resolved, or refuse if it is outside the project."""
    project = project_root(root)
    resolved = path.expanduser().resolve()
    if not resolved.is_relative_to(project):
        raise OutsideProjectError(
            f"{what} is {resolved}, outside the project {project}. "
            "Unset it or `source AI_Agent/scripts/env.sh`."
        )
    return resolved


def ensure_project_hf_env(root: Path) -> Path:
    """Point every Hugging Face cache inside the project before huggingface_hub is
    imported, and refuse if anything already points elsewhere. Returns HF_HOME.

    huggingface_hub reads these variables once, at first import; with HF_HOME unset
    its hub and xet caches default to ~/.cache/huggingface."""
    for var in _HF_VARS:
        if value := os.environ.get(var):
            require_inside(Path(value), root, f"${var}")
    home = Path(os.environ.setdefault("HF_HOME", str(project_root(root) / "Dependencies" / "hf")))
    os.environ.setdefault("HF_XET_CACHE", str(home / "xet"))
    if (constants := sys.modules.get("huggingface_hub.constants")) is not None:
        for name in _HF_VARS:
            if (value := getattr(constants, name, None)) is not None:
                require_inside(Path(value), root, f"huggingface_hub (already imported) {name}")
    return home


def hf_cache_dir(root: Path) -> Path:
    """The Hugging Face hub cache: `$HF_HUB_CACHE`, else `$HF_HOME/hub`,
    else `<main checkout>/Dependencies/hf/hub`."""
    if hub := os.environ.get("HF_HUB_CACHE"):
        return Path(hub)
    if home := os.environ.get("HF_HOME"):
        return Path(home) / "hub"
    return project_root(root) / "Dependencies" / "hf" / "hub"


class ModelNotDownloadedError(RuntimeError):
    """Raised at load time instead of downloading silently from a server start."""

    def __init__(self, what: str, cache: Path) -> None:
        super().__init__(
            f"{what} is not in {cache}. Run from AI_Agent/: "
            "uv run python scripts/download_models.py --voice --no-llm"
        )
