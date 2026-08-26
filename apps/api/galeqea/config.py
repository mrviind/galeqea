"""Runtime configuration.

GaleQEA is local-first: every setting has a working default that requires no
cloud service, no API key and no outbound network access. The platform boots
in ``NO_AI`` mode unless a model provider is explicitly configured.
"""

from __future__ import annotations

import os
import secrets
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AIMode(StrEnum):
    """The three mandated operating modes."""

    NO_AI = "no_ai"          # zero LLM, zero outbound calls. The default.
    API_KEY = "api_key"      # user-supplied key for a hosted provider
    LOCAL = "local"          # Ollama / any OpenAI-compatible local endpoint
    BYO_AGENT = "byo_agent"  # bridge to a locally installed Claude Code / Agent SDK CLI


class ApprovalMode(StrEnum):
    PER_ACTION = "per_action"    # every write needs its own approval
    GATED_BATCH = "gated_batch"  # writes queue into a batch approved as a unit
    AUTO_LOW_RISK = "auto_low_risk"  # only low-risk tiers auto-pass; writes still logged


def _default_home() -> Path:
    return Path(os.environ.get("GALEQEA_HOME", Path.home() / ".galeqea"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GALEQEA_",
        env_file=(".env", "../../.env"),
        extra="ignore",
    )

    # --- paths -------------------------------------------------------------
    home: Path = _default_home()
    artifacts_dir: Path | None = None
    plugins_dir: Path | None = None

    # --- server ------------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8080
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    public_url: str = "http://localhost:8080"

    # --- security ----------------------------------------------------------
    secret_key: str = ""
    vault_key: str = ""
    jwt_ttl_minutes: int = 60 * 12
    # Single-user desktop installs skip login; server deployments must not.
    single_user_mode: bool = True

    # --- database ----------------------------------------------------------
    database_url: str = ""

    # --- ai ----------------------------------------------------------------
    ai_mode: AIMode = AIMode.NO_AI
    provider: str = "none"           # anthropic | openai | gemini | azure_openai | ollama | openai_compatible | claude_cli | none
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    # Hard ceiling so an agent loop can never silently burn a budget.
    max_tokens_per_run: int = 200_000
    max_agent_steps: int = 40
    # Opt-in only. When false the agent has no web access whatsoever.
    web_research_enabled: bool = False

    # --- governance --------------------------------------------------------
    approval_mode: ApprovalMode = ApprovalMode.PER_ACTION
    # Structural invariant, not a preference: an AI identity may never approve
    # a write it authored. Exposed as config only so it can be made *stricter*.
    allow_ai_self_approval: bool = False

    # --- execution ---------------------------------------------------------
    runner_command: str = "node"
    runner_entry: str = ""
    max_parallel_runs: int = 4
    default_browser: str = "chromium"
    default_timeout_ms: int = 30_000

    # --- telemetry ---------------------------------------------------------
    telemetry_enabled: bool = False  # off by default, forever

    def model_post_init(self, __context) -> None:  # noqa: D105
        self.home = Path(self.home).expanduser()
        self.home.mkdir(parents=True, exist_ok=True)
        if self.artifacts_dir is None:
            self.artifacts_dir = self.home / "artifacts"
        if self.plugins_dir is None:
            self.plugins_dir = self.home / "plugins"
        Path(self.artifacts_dir).mkdir(parents=True, exist_ok=True)
        Path(self.plugins_dir).mkdir(parents=True, exist_ok=True)

        if not self.database_url:
            self.database_url = f"sqlite:///{self.home / 'galeqea.db'}"

        # Keys are generated once and persisted with 0600 so a fresh install is
        # secure without the operator having to think about it.
        self.secret_key = self.secret_key or _persisted_secret(self.home / "secret.key")
        self.vault_key = self.vault_key or _persisted_secret(self.home / "vault.key")

        if not self.runner_entry:
            repo_runner = Path(__file__).resolve().parents[2] / "runner" / "src" / "cli.mjs"
            self.runner_entry = str(repo_runner)

    @property
    def ai_enabled(self) -> bool:
        return self.ai_mode != AIMode.NO_AI and self.provider != "none"


def _persisted_secret(path: Path) -> str:
    if path.exists():
        return path.read_text().strip()
    value = secrets.token_urlsafe(48)
    path.write_text(value)
    path.chmod(0o600)
    return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
