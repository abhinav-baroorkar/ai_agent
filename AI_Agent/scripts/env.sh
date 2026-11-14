#!/usr/bin/env bash
# Keep the venv, caches and temp files inside the project's Dependencies/ folder (git-ignored).
# Safe to source from the main checkout or from a worktree under
# Dependencies/worktrees/<name>/. Ollama keeps its own model store.
# TMPDIR applies to everything run from this shell, not only this project's tools;
# that is intended. Entries in Dependencies/tmp older than 7 days are removed on source.
# Usage: source AI_Agent/scripts/env.sh
_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export SASHA_ROOT="$_root"
case "$_root" in
  */Dependencies/worktrees/*)
    _main="${_root%%/Dependencies/worktrees/*}"  # worktree: own .venv, shared caches
    unset UV_PROJECT_ENVIRONMENT ;;
  *)
    _main="$_root"
    export UV_PROJECT_ENVIRONMENT="$_main/Dependencies/.venv" ;;
esac
export HF_HOME="$_main/Dependencies/hf"
export UV_CACHE_DIR="$_main/Dependencies/uv-cache"
export XDG_CACHE_HOME="$_main/Dependencies/cache"
export TMPDIR="$_main/Dependencies/tmp"
mkdir -p "$HF_HOME" "$UV_CACHE_DIR" "$XDG_CACHE_HOME" "$TMPDIR" "$_main/Data/logs"
case "$TMPDIR" in
  */Dependencies/tmp) find "$TMPDIR" -mindepth 1 -maxdepth 1 -mtime +7 -exec rm -rf {} + ;;
esac
unset _root _main
