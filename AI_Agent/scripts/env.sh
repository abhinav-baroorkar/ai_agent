#!/usr/bin/env bash
# Keep the venv, caches and temp files inside the project's Dependencies/ folder (git-ignored).
# Bash only. Safe to source from the main checkout or from a worktree under
# Dependencies/worktrees/<name>/. Ollama keeps its own model store.
# TMPDIR applies to everything run from this shell, not only this project's tools;
# that is intended. Entries in Dependencies/tmp are judged by their own modification
# time and removed after 7 days, so a directory still used by a process that runs
# for more than a week could be removed.
# Usage: source AI_Agent/scripts/env.sh
_bad() { echo "env.sh: run with bash from the repo (source AI_Agent/scripts/env.sh)" >&2; unset -f _bad; unset _root _main; }
if [ -n "$ZSH_VERSION" ]; then _bad; return 1 2>/dev/null || exit 1; fi
_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)"
case "$_root" in
  */Dependencies/worktrees/*) _main="${_root%%/Dependencies/worktrees/*}"; _wt=1 ;;  # own .venv, shared caches
  *) _main="$_root"; _wt= ;;
esac
if [ -z "$_main" ] || [ ! -f "$_main/AI_Agent/scripts/env.sh" ] || [ ! -f "$_main/CLAUDE.md" ]; then
  _bad; return 1 2>/dev/null || exit 1
fi
unset -f _bad
export SASHA_ROOT="$_root"
if [ -n "$_wt" ]; then unset UV_PROJECT_ENVIRONMENT; else export UV_PROJECT_ENVIRONMENT="$_main/Dependencies/.venv"; fi
export HF_HOME="$_main/Dependencies/hf"
export UV_CACHE_DIR="$_main/Dependencies/uv-cache"
export XDG_CACHE_HOME="$_main/Dependencies/cache"
export TMPDIR="$_main/Dependencies/tmp"
mkdir -p "$HF_HOME" "$UV_CACHE_DIR" "$XDG_CACHE_HOME" "$TMPDIR" "$_main/Data/logs"
if [ "$(cd "$TMPDIR" && pwd -P)" = "$(cd "$_main" && pwd -P)/Dependencies/tmp" ]; then
  find "$TMPDIR" -mindepth 1 -maxdepth 1 -mtime +7 -exec rm -rf {} +
fi
unset _root _main _wt
