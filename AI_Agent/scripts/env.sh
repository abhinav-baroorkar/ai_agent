#!/usr/bin/env bash
# Point uv and Hugging Face caches into Dependencies/ (git-ignored).
# Ollama keeps its models in its own default location (it runs as a system service).
# Usage: source AI_Agent/scripts/env.sh
_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export SASHA_ROOT="$_root"
export UV_PROJECT_ENVIRONMENT="$_root/Dependencies/.venv"
export HF_HOME="$_root/Dependencies/hf"
mkdir -p "$HF_HOME" "$_root/Data/logs"
unset _root
