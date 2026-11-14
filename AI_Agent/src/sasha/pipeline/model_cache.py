"""Where downloaded model weights live. Always inside the project, never in ~/.cache."""

from __future__ import annotations

import os
from pathlib import Path


def hf_cache_dir(root: Path) -> Path:
    """The Hugging Face hub cache: `$HF_HUB_CACHE`, else `$HF_HOME/hub`,
    else `<root>/Dependencies/hf/hub` (the same layout `scripts/env.sh` sets up)."""
    if hub := os.environ.get("HF_HUB_CACHE"):
        return Path(hub)
    if home := os.environ.get("HF_HOME"):
        return Path(home) / "hub"
    return root / "Dependencies" / "hf" / "hub"


class ModelNotDownloadedError(RuntimeError):
    """Raised at load time instead of downloading silently from a server start."""

    def __init__(self, what: str, cache: Path) -> None:
        super().__init__(
            f"{what} is not in {cache}. Run from AI_Agent/: "
            "uv run python scripts/download_models.py --voice"
        )
