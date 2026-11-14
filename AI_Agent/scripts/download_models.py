"""Download the models a profile needs. Weights are never committed to git.

Usage (from AI_Agent/): uv run python scripts/download_models.py [--profile pc] [--small]
Phase 0 only needs the Ollama LLM. Later phases add Hugging Face models here.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

from sasha.config import load_settings


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", default=None)
    p.add_argument("--small", action="store_true", help="Also pull the profile's small_model")
    args = p.parse_args()

    settings = load_settings(profile=args.profile)
    llm = settings.profile.llm
    models = [llm.model] + ([llm.small_model] if args.small and llm.small_model else [])

    if shutil.which("ollama") is None:
        print("ollama not found. Install: https://ollama.com/download/linux", file=sys.stderr)
        return 1
    for model in models:
        print(f"Pulling {model} ...")
        if subprocess.run(["ollama", "pull", model], check=False).returncode != 0:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
