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


class WakeWord(BaseModel):
    phrase: str
    model: str | None = None
    threshold: float = 0.5


class Voice(BaseModel):
    tts_speaker: str | None = None
    description: str = ""


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


class TelemetryConfig(BaseModel):
    enabled: bool = True
    log_text: bool = False


class ServerConfig(BaseModel):
    """The brain server (`sasha serve`). v0 has no auth, so it listens on loopback only."""

    host: str = "127.0.0.1"
    port: int = 8765
    allowed_origins: list[str] = Field(default_factory=list)  # besides the server's own


class Profile(BaseModel):
    name: str
    device: str = "cpu"
    llm: LLMConfig
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)


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
    """Re-read the root dotenv file so edits apply without a restart. Values there win."""
    in_file = {k: v for k, v in dotenv_values(root / ".env").items() if v}
    return load_secrets({**os.environ, **in_file})


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
