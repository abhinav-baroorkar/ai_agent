"""Download the models a profile needs. Weights are never committed to git.

Usage (from AI_Agent/):
  uv run python scripts/download_models.py [--profile pc] [--small]   # Ollama LLM
  uv run python scripts/download_models.py --voice --no-llm            # STT + TTS only

Hugging Face files (hub and xet caches) go to `$HF_HOME`, which must be inside the
project; when unset it becomes `<main checkout>/Dependencies/hf` (also from a
worktree). The script refuses to run if any HF cache variable points outside the
project. `--voice` needs `uv sync --extra voice`.

Never run a Hugging Face cache prune (`hf cache prune`) on this cache: models are
downloaded by pinned commit hash, so a prune treats whole repos as detached and
deletes them. To drop one model use `hf cache rm <repo>`.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

from sasha.config import Settings, load_settings
from sasha.pipeline.model_cache import OutsideProjectError, ensure_project_hf_env, hf_cache_dir


def pull_llm(settings: Settings, small: bool) -> int:
    llm = settings.profile.llm
    models = [llm.model] + ([llm.small_model] if small and llm.small_model else [])
    if shutil.which("ollama") is None:
        print("ollama not found. Install: https://ollama.com/download/linux", file=sys.stderr)
        return 1
    for model in models:
        print(f"Pulling {model} ...")
        if subprocess.run(["ollama", "pull", model], check=False).returncode != 0:
            return 1
    return 0


def download_voice(settings: Settings) -> int:
    try:
        from faster_whisper.utils import download_model

        from sasha.pipeline.tts_kokoro import model_files
    except ImportError as e:
        print(f"Missing voice packages ({e.name}). Run: uv sync --extra voice", file=sys.stderr)
        return 1
    cache = hf_cache_dir(settings.paths.root)
    stt = settings.profile.stt
    print(f"Whisper '{stt.model}'@{stt.revision[:8]} -> {cache}")
    download_model(stt.model, cache_dir=str(cache), revision=stt.revision)
    tts = settings.profile.tts
    print(f"Kokoro {tts.model_repo}@{tts.revision[:8]} -> {cache}")
    model_files(tts, settings.assistant.voice.en_voice, settings.paths.root, download=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--profile", default=None)
    p.add_argument("--small", action="store_true", help="Also pull the profile's small_model")
    p.add_argument("--voice", action="store_true", help="Download STT and TTS weights")
    p.add_argument("--no-llm", action="store_true", help="Skip the Ollama pull")
    args = p.parse_args()

    settings = load_settings(profile=args.profile)
    try:  # first, before anything imports huggingface_hub
        ensure_project_hf_env(settings.paths.root)
    except OutsideProjectError as e:
        print(e, file=sys.stderr)
        return 1
    if not args.no_llm and (rc := pull_llm(settings, args.small)) != 0:
        return rc
    return download_voice(settings) if args.voice else 0


if __name__ == "__main__":
    raise SystemExit(main())
