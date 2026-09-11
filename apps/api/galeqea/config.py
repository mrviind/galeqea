"""Runtime configuration.

GaleQEA is model-agnostic and safe by default: every setting has a working
default that requires no cloud service, no API key and no outbound network
access. The platform boots in ``NO_AI`` mode until a model provider is
explicitly configured, then runs on whichever one you point it at.
"""

from __future__ import annotations

import os
import secrets
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
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
    # Force the Secure flag on session cookies even on a plain-HTTP request. Set
    # this true behind a TLS-terminating proxy that talks HTTP to the app.
    session_cookie_secure: bool = False
    # Single-user desktop installs skip login; server deployments must not.
    single_user_mode: bool = True

    # --- OIDC single sign-on (optional) ------------------------------------
    # Set issuer + client id/secret to turn on "Sign in with SSO". Works with any
    # OpenID Connect provider (Keycloak, Okta, Entra, Authentik, Google…).
    auth_oidc_issuer: str = ""          # e.g. https://keycloak.example/realms/galeqea
    auth_oidc_client_id: str = ""
    auth_oidc_client_secret: str = ""
    auth_oidc_scopes: str = "openid email profile"
    auth_oidc_groups_claim: str = "groups"     # claim holding the user's groups/roles
    auth_oidc_role_map: dict = {}       # {"galeqea-admins": "admin", …} → GaleQEA role
    auth_oidc_default_role: str = "author"     # role for a JIT user with no mapped group
    auth_oidc_redirect_base: str = ""   # public base URL for the callback (proxy override)

    # --- database ----------------------------------------------------------
    database_url: str = ""

    # --- artifact storage --------------------------------------------------
    # "local" (disk under GALEQEA_HOME) or "s3" (any S3-compatible endpoint).
    storage_backend: str = "local"
    s3_endpoint: str = ""            # empty → AWS default; set for SeaweedFS/R2/Ceph/MinIO
    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_force_path_style: bool = False  # required by most non-AWS endpoints
    # Presigned-URL lifetime when the API hands a browser a direct S3 link.
    s3_url_ttl_seconds: int = 3600

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
    # --- job queue ---------------------------------------------------------
    # "auto" = in-process (asyncio) on SQLite, durable Postgres queue on Postgres.
    # Force with "inprocess" | "procrastinate". The zero-config SQLite install never
    # needs an external broker; the in-process queue IS the event loop.
    queue_backend: str = "auto"        # auto | inprocess | procrastinate
    worker_concurrency: int = 4
    default_browser: str = "chromium"
    default_timeout_ms: int = 30_000

    # --- logging / audit streaming -----------------------------------------
    # "auto" renders human-readable console logs on a TTY and JSON everywhere else
    # (containers, CI); "json"/"console" force one. Every line carries the request id.
    log_format: str = "auto"           # auto | json | console
    log_level: str = "INFO"
    # Mirror every audit-ledger entry as a JSON line on stdout for a SIEM collector.
    audit_siem: bool = False
    # OpenTelemetry tracing (needs apps/api[otel] + OTEL_EXPORTER_OTLP_ENDPOINT).
    otel_enabled: bool = False

    # --- demo / default testing target -------------------------------------
    # The site the product tests out of the box: "test" with no URL, a new project's
    # first run, or the demo button all point here. Any URL (including a localhost
    # app) overrides it per request.
    demo_target_url: str = "https://www.aravindarumugam.com"
    demo_page_budget: int = 6          # pages to analyse for the demo floor (5-7)

    # --- telemetry ---------------------------------------------------------
    telemetry_enabled: bool = False  # off by default, forever

    @field_validator(
        "s3_force_path_style", "web_research_enabled", "allow_ai_self_approval",
        "telemetry_enabled", "session_cookie_secure", "audit_siem", "otel_enabled",
        mode="before",
    )
    @classmethod
    def _blank_bool_is_false(cls, v):
        # Compose/env commonly pass an unset variable through as an empty string
        # (``FOO: ${FOO:-}``). For a bool that is a crash (pydantic can't parse
        # ""), so treat a blank string as the field default (all of these default
        # to False). The zero-config path must never fail to boot on an empty env.
        if isinstance(v, str) and v.strip() == "":
            return False
        return v

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

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith(("postgres://", "postgresql", "postgresql+"))

    @property
    def queue_kind(self) -> str:
        """Resolve the effective queue backend ('inprocess' | 'procrastinate')."""
        if self.queue_backend in ("inprocess", "procrastinate"):
            return self.queue_backend
        return "procrastinate" if self.is_postgres else "inprocess"

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.auth_oidc_issuer and self.auth_oidc_client_id
                    and self.auth_oidc_client_secret)


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
