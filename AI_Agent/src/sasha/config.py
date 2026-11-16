"""Load the assistant persona, hardware profile, tool config and secrets.

Selection order (highest first): explicit arguments, environment variables
(`SASHA_PROFILE`, `SASHA_ASSISTANT_CONFIG`, `SASHA_ROOT`), then defaults.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values, load_dotenv
from pydantic import BaseModel, Field, SecretStr, field_validator

from sasha.config_app import AppText, WakeWord, WindowConfig
from sasha.config_memory import MemoryConfig
from sasha.config_prompts import PromptLines
from sasha.config_timers import TimersConfig
from sasha.config_wake import ReplyLines as ReplyLines  # re-exported: moved, not renamed
from sasha.config_wake import WakeConfig as WakeConfig

# src/sasha/config.py -> sasha -> src -> AI_Agent -> repo root
_DEFAULT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROFILE = "laptop"

# The only domains the assistant may ever control, whatever tools.yaml says. An allowlist,
# not a denylist: HA adds domains every release, and button/valve/notify/remote/lock
# can open gates, shut off water, message people or unlock doors. Widening this needs
# owner voice confirmation (Phase 3) and a decisions.md entry.
CONTROLLABLE_DOMAINS = frozenset({"light", "switch", "fan", "scene", "media_player"})


class Pronouns(BaseModel):
    subject: str
    object: str
    possessive: str


class Voice(BaseModel):
    tts_speaker: str | None = None
    description: str = ""
    # Kokoro voice id for English replies. It becomes a Hugging Face and a local file
    # name, so only "<lang letter><gender letter>_<name>" is accepted.
    en_voice: str = Field(default="af_heart", pattern=r"^[a-z]{2}_[a-z0-9]+$")
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


class Languages(BaseModel):
    default: str = "en"
    supported: list[str] = Field(default_factory=lambda: ["en"])


class Avatar(BaseModel):
    theme: str = "default"


class AssistantConfig(BaseModel):
    name: str
    pronouns: Pronouns
    wake_word: WakeWord
    voice: Voice = Field(default_factory=Voice)
    languages: Languages = Field(default_factory=Languages)
    avatar: Avatar = Field(default_factory=Avatar)
    app: AppText = Field(default_factory=AppText)
    system_prompt: str

    def render_system_prompt(self) -> str:
        # str.replace, not str.format: a literal { or } in the prompt must not crash.
        values = {
            "{name}": self.name,
            "{subject}": self.pronouns.subject,
            "{object}": self.pronouns.object,
            "{possessive}": self.pronouns.possessive,
        }
        text = self.system_prompt
        for placeholder, value in values.items():
            text = text.replace(placeholder, value)
        return text.strip()


class LLMConfig(BaseModel):
    backend: str = "ollama"
    model: str
    small_model: str | None = None
    num_ctx: int = 4096
    temperature: float = 0.3
    think: bool | None = None
    keep_alive: str | None = None
    timeout_s: float = 120.0
    max_tool_rounds: int = 4
    # Kept in the history (the model sees it, nobody else) when a reply was stopped.
    interrupted_note: str = "[The owner stopped this reply here; it was not finished.]"
    interrupted_keep_chars: int = 2000  # at most this much of a stopped reply is kept
    # The said-check (sasha/said.py) may take a change's target from the owner's messages
    # of the last 2 kept turns that ended within this many seconds ("turn it off").
    target_window_s: float = Field(default=180.0, ge=0)


class TelemetryConfig(BaseModel):
    enabled: bool = True
    log_text: bool = False


_COMMIT = r"^[0-9a-f]{40}$"  # a full git commit hash: branch names like "main" can move


class STTConfig(BaseModel):
    backend: str = "faster-whisper"
    model: str = "small"
    # Commit of Systran/faster-whisper-<model> on Hugging Face. Change it with `model`.
    revision: str = Field(default="536b0662742c02347bc0e980a01041f333bce120", pattern=_COMMIT)
    device: str = "auto"  # auto (try cuda, fall back to cpu) | cuda | cpu
    compute_type: str = "auto"  # auto: float16 on cuda, int8 on cpu
    beam_size: int = 1
    cpu_threads: int = 0  # 0: library default
    vad_filter: bool = False  # the client already sends one trimmed utterance
    max_seconds: float = Field(default=60.0, gt=0)  # longer utterances are refused


class TTSConfig(BaseModel):
    backend: str = "kokoro"
    device: str = "cpu"
    model_repo: str = "onnx-community/Kokoro-82M-v1.0-ONNX"  # official ONNX export, Apache-2.0
    revision: str = Field(default="1939ad2a8e416c0acfeecc08a694d14ef25f2231", pattern=_COMMIT)
    model_file: str = "onnx/model.onnx"  # fp32; the int8 export was 5x slower on CPU
    voices_dir: str = "voices"  # one <voice>.bin (float32, 510x256) per voice
    threads: int = 0  # onnxruntime intra-op threads; 0: library default
    max_chars: int = Field(default=4000, gt=0)  # longer text is cut at a sentence end


class ServerConfig(BaseModel):
    """The brain server (`sasha serve`). v0 has no auth, so it listens on loopback only."""

    host: str = "127.0.0.1"
    port: int = 8765
    allowed_origins: list[str] = Field(default_factory=list)  # besides the server's own
    max_sockets: int = Field(default=4, ge=1, le=64)  # open /ws connections at once


class Profile(BaseModel):
    name: str
    device: str = "cpu"
    llm: LLMConfig
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    wake: WakeConfig = Field(default_factory=WakeConfig)
    timers: TimersConfig = Field(default_factory=TimersConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    window: WindowConfig = Field(default_factory=WindowConfig)
    prompt_lines: dict[str, PromptLines] = Field(default_factory=lambda: {"en": PromptLines()})


class HomeAssistantConfig(BaseModel):
    enabled: bool = True
    timeout_s: float = 10.0
    allowed_services: dict[str, list[str]] = Field(default_factory=dict)
    exposed_entities: list[str] = Field(default_factory=list)

    @field_validator("allowed_services")
    @classmethod
    def _only_controllable_domains(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        refused = sorted(set(value) - CONTROLLABLE_DOMAINS)
        if refused:
            allowed = ", ".join(sorted(CONTROLLABLE_DOMAINS))
            raise ValueError(f"Domains not controllable: {', '.join(refused)} (allowed: {allowed})")
        return value


class ToolsConfig(BaseModel):
    home_assistant: HomeAssistantConfig = Field(default_factory=HomeAssistantConfig)


class Secrets(BaseModel):
    """Values read from the environment / `.env`. Never logged."""

    ollama_host: str = "http://localhost:11434"
    ha_url: str | None = None
    ha_token: SecretStr | None = None


class Paths(BaseModel):
    root: Path
    config_dir: Path
    data_dir: Path

    @property
    def telemetry_dir(self) -> Path:
        return self.data_dir / "logs" / "telemetry"

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"


class Settings(BaseModel):
    assistant: AssistantConfig
    profile: Profile
    tools: ToolsConfig
    secrets: Secrets
    paths: Paths


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected a mapping at the top of {path}")
    return data


def available_profiles(config_dir: Path) -> list[str]:
    return sorted(p.stem for p in (config_dir / "profiles").glob("*.yaml"))


def normalize_host(host: str) -> str:
    """Accept Ollama's own `OLLAMA_HOST` forms (`0.0.0.0`, `127.0.0.1:11434`) as client URLs."""
    host = host.strip().rstrip("/")
    if "://" not in host:  # bare host[:port], as `ollama serve` accepts it
        host = f"http://{host}" if ":" in host else f"http://{host}:11434"
    scheme, rest = host.split("://", 1)
    if rest == "0.0.0.0" or rest.startswith("0.0.0.0:"):  # bind address, not a target
        rest = "127.0.0.1" + rest[len("0.0.0.0") :]
    return f"{scheme}://{rest}"


def load_secrets(environ: Mapping[str, str] | None = None) -> Secrets:
    env = os.environ if environ is None else environ
    token = env.get("HA_TOKEN") or None
    return Secrets(
        ollama_host=normalize_host(env.get("OLLAMA_HOST") or "http://localhost:11434"),
        ha_url=env.get("HA_URL") or None,
        ha_token=SecretStr(token) if token else None,
    )


def reload_secrets(root: Path) -> Secrets:
    """Re-read the root dotenv file, so a variable added there works without a restart.

    Same rule as at startup (`load_dotenv(override=False)`): the process environment
    wins. The file only fills variables the environment leaves unset or empty, so
    changing a value that was already loaded still needs a restart.
    """
    in_file = {k: v for k, v in dotenv_values(root / ".env").items() if v}
    in_env = {k: v for k, v in os.environ.items() if v}
    return load_secrets({**in_file, **in_env})


def load_settings(
    profile: str | None = None,
    assistant_path: Path | None = None,
    root: Path | None = None,
    load_env_file: bool = True,
) -> Settings:
    """Load everything the assistant needs to start."""
    root = Path(root or os.environ.get("SASHA_ROOT") or _DEFAULT_ROOT).resolve()
    config_dir = root / "AI_Agent" / "config"
    if load_env_file:
        load_dotenv(root / ".env", override=False)

    profile_name = profile or os.environ.get("SASHA_PROFILE") or DEFAULT_PROFILE
    profile_path = config_dir / "profiles" / f"{profile_name}.yaml"
    if not profile_path.is_file():
        choices = ", ".join(available_profiles(config_dir)) or "none"
        raise FileNotFoundError(f"Unknown profile '{profile_name}'. Available: {choices}")

    env_assistant = os.environ.get("SASHA_ASSISTANT_CONFIG")
    assistant_file = Path(assistant_path or env_assistant or config_dir / "assistant.yaml")
    tools_file = config_dir / "tools.yaml"

    return Settings(
        assistant=AssistantConfig.model_validate(_read_yaml(assistant_file)),
        profile=Profile.model_validate(_read_yaml(profile_path)),
        tools=ToolsConfig.model_validate(_read_yaml(tools_file) if tools_file.is_file() else {}),
        secrets=load_secrets(),
        paths=Paths(root=root, config_dir=config_dir, data_dir=root / "Data"),
    )
