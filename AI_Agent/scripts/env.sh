#!/usr/bin/env bash
# Point uv, Hugging Face and Ollama caches into Dependencies/ (git-ignored).
# Usage: source AI_Agent/scripts/env.sh
_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export SASHA_ROOT="$_root"
export UV_PROJECT_ENVIRONMENT="$_root/Dependencies/.venv"
export HF_HOME="$_root/Dependencies/hf"
export OLLAMA_MODELS="$_root/Dependencies/ollama"
mkdir -p "$HF_HOME" "$OLLAMA_MODELS" "$_root/Data/logs"
unset _root
