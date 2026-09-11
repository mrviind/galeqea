"""Provider construction and per-workspace resolution.

The single point where "which model am I talking to" is decided, so every agent
in the system inherits the same answer and switching providers stays a one-line
config change.
"""

from __future__ import annotations

from typing import Any

from ...config import AIMode, settings
from .base import LLMProvider, ProviderError
from .noop import NoAIProvider

_PROVIDERS: dict[str, Any] = {}


def _load(provider: str):
    """Import lazily so an unused provider's dependency is never required."""
    if provider in _PROVIDERS:
        return _PROVIDERS[provider]

    if provider == "anthropic":
        from .anthropic_provider import AnthropicProvider as cls
    elif provider in {"openai", "openai_compatible"}:
        from .openai_compat import OpenAICompatibleProvider as cls
    elif provider == "azure_openai":
        from .openai_compat import AzureOpenAIProvider as cls
    elif provider == "ollama":
        from .openai_compat import OllamaProvider as cls
    elif provider == "gemini":
        from .gemini import GeminiProvider as cls
    elif provider == "claude_cli":
        from .claude_cli import ClaudeCLIProvider as cls
    elif provider in {"none", "no_ai"}:
        cls = NoAIProvider
    else:
        raise ProviderError(
            f"unknown provider {provider!r}. Available: anthropic, openai, "
            "openai_compatible, azure_openai, ollama, gemini, claude_cli, none"
        )

    _PROVIDERS[provider] = cls
    return cls


#: What the Copilot's model picker offers per provider.
#:
#: A curated list rather than a live query, because the picker must render
#: instantly and must work in No-AI mode with no network at all. It is a
#: *starting set*, not a whitelist: any model id a vault credential names is
#: merged in on top, so using a model newer than this file requires only
#: configuring it, never editing code.
MODEL_CATALOGUE: dict[str, list[dict]] = {
    "anthropic": [
        {"id": "claude-opus-5", "label": "Claude Opus 5", "context": 1_000_000},
        {"id": "claude-sonnet-5", "label": "Claude Sonnet 5", "context": 1_000_000},
        {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5", "context": 200_000},
    ],
    "openai": [
        {"id": "gpt-4o", "label": "GPT-4o", "context": 128_000},
        {"id": "gpt-4o-mini", "label": "GPT-4o mini", "context": 128_000},
        {"id": "o3-mini", "label": "o3-mini", "context": 200_000},
    ],
    "gemini": [
        {"id": "gemini-2.0-flash", "label": "Gemini 2.0 Flash", "context": 1_048_576},
        {"id": "gemini-1.5-pro", "label": "Gemini 1.5 Pro", "context": 2_097_152},
    ],
    "azure_openai": [
        {"id": "gpt-4o", "label": "GPT-4o (Azure deployment)", "context": 128_000},
    ],
    "ollama": [
        {"id": "llama3.2", "label": "Llama 3.2", "context": 128_000},
        {"id": "qwen2.5-coder", "label": "Qwen2.5 Coder", "context": 32_768},
    ],
    "openai_compatible": [
        {"id": "local-model", "label": "Local model", "context": 0},
    ],
    "claude_cli": [
        {"id": "claude-code", "label": "Claude Code CLI (your own session)", "context": 0},
    ],
}

#: Providers that need no credential because the endpoint is the operator's own.
LOCAL_PROVIDERS = {"ollama", "openai_compatible", "claude_cli"}


DEFAULT_MODELS = {
    "anthropic": "claude-opus-5",
    "openai": "gpt-4.1",
    "openai_compatible": "local-model",
    "azure_openai": "gpt-4o",
    "ollama": "llama3.1",
    "gemini": "gemini-2.5-pro",
    "claude_cli": "",
}


def build_provider(
    *,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    **options,
) -> LLMProvider:
    provider = provider or settings.provider
    if settings.ai_mode is AIMode.NO_AI and provider in (None, "", "none"):
        return NoAIProvider()

    cls = _load(provider)
    if cls is NoAIProvider:
        return NoAIProvider()

    resolved_model = model or settings.model or DEFAULT_MODELS.get(provider, "")
    return cls(
        model=resolved_model,
        api_key=api_key if api_key is not None else settings.api_key,
        base_url=base_url if base_url is not None else settings.base_url,
        **options,
    )


_default: LLMProvider | None = None


def for_project(db, project_id: str | None) -> LLMProvider:
    """Resolve the provider for one project: its own key, else the global one.

    This is what makes bring-your-own-key real rather than a settings field. A
    stored key is per-project with a global fallback, so one workspace can run
    against a local model while another uses a hosted one, and the key comes
    from the vault, not from a value that lives only until the next restart.
    """
    from .. import keys

    provider = settings.provider
    if settings.ai_mode is AIMode.NO_AI or provider in (None, "", "none"):
        return NoAIProvider()

    try:
        api_key = keys.resolve(db, provider=provider, project_id=project_id)
        config = keys.config_for(db, provider=provider, project_id=project_id)
        # Refuse before spending, not after: a budget you discover you have
        # exceeded is a bill.
        keys.check_budget(db, provider=provider, project_id=project_id)
    except keys.KeyError_ as exc:
        # Degrade rather than crash: a spent budget must not fail a run whose
        # other 90% needs no model at all. Carry the real reason, so the
        # user is not sent looking for a missing configuration that is fine.
        return NoAIProvider(reason=str(exc))
    except Exception:  # noqa: BLE001 - vault trouble must not break No-AI paths
        api_key, config = None, {}

    # A local provider (Ollama, an OpenAI-compatible endpoint) authenticates with
    # nothing: its endpoint is the operator's own. Requiring a key here is what
    # made `for_project` fall to No-AI for exactly the air-gapped setup the whole
    # local mode exists to serve. Only hosted providers need a credential.
    if provider not in LOCAL_PROVIDERS and api_key is None and not settings.api_key:
        return NoAIProvider()

    try:
        return build_provider(
            provider=provider,
            model=config.get("model") or settings.model or None,
            api_key=api_key or settings.api_key,
            base_url=config.get("base_url") or settings.base_url or None,
        )
    except ProviderError:
        return NoAIProvider()


#: Which cost bucket each agent role routes to. Planner = frontier reasoning;
#: grounder = the cheap locating/healing model; judge = a mid model for verdicts.
_ROLE_BUCKET = {
    "orchestrator": "planner", "requirement_analyst": "planner", "test_designer": "planner",
    "script_generator": "planner", "explorer": "planner", "coverage_cartographer": "planner",
    "data_architect": "planner",
    "healer": "grounder", "locator": "grounder",
    "judge": "judge", "rca_analyst": "judge",
}


def bucket_for_role(role: str) -> str:
    """Map an agent role to its model bucket (planner/grounder/judge)."""
    return _ROLE_BUCKET.get(role, "planner")


def for_role(db, project_id: str | None, role: str) -> LLMProvider:
    """Resolve the provider for one agent *role*, honouring per-role model routing.

    A tester's locating work (the grounder) can run on a small/cheap/local model
    while the planner keeps a frontier model, configured as
    ``role_models: {planner, grounder, judge}`` on the project's provider config.
    Falls back to the project default when a role has no override (WO#8-C)."""
    base = for_project(db, project_id)
    if isinstance(base, NoAIProvider):
        return base
    from .. import keys

    config = keys.config_for(db, provider=settings.provider, project_id=project_id)
    role_models = (config.get("role_models") or {})
    model = role_models.get(_ROLE_BUCKET.get(role, "planner"))
    if not model or model == getattr(base, "model", None):
        return base
    try:
        api_key = keys.resolve(db, provider=settings.provider, project_id=project_id)
    except Exception:  # noqa: BLE001
        api_key = None
    try:
        return build_provider(
            provider=settings.provider, model=model,
            api_key=api_key or settings.api_key,
            base_url=config.get("base_url") or settings.base_url or None)
    except ProviderError:
        return base


def for_selection(db, project_id: str | None, provider: str | None, model: str | None) -> LLMProvider:
    """Build a provider for a model the *client* chose.

    This is the server half of the Copilot's model picker, and the reason the
    picker is safe to expose in a browser at all. The client sends two strings:
    a provider name and a model id. Neither is a credential. The key is unsealed
    from the vault here, on the server, and never travels in either direction.

    An unknown or uncredentialed provider falls back to the project's configured
    one rather than erroring: a stale selection in someone's browser (a model
    that was removed, a key that was rotated away) should degrade to the working
    default, not break the chat.
    """
    if not provider:
        return for_project(db, project_id)

    from .. import keys

    config = keys.config_for(db, provider=provider, project_id=project_id)
    if not config.get("api_key") and provider not in LOCAL_PROVIDERS:
        # Nothing sealed for this provider. Fall back rather than fail.
        return for_project(db, project_id)

    keys.check_budget(db, provider=provider, project_id=project_id)
    return build_provider(
        provider=provider,
        model=model or config.get("model") or None,
        api_key=config.get("api_key"),
        base_url=config.get("base_url"),
    )


def default_provider() -> LLMProvider:
    """Process-wide provider derived from settings. Cached; reset on config change.

    Used where no project is in scope. Prefer ``for_project``: a per-project
    key cannot be honoured by a process-global instance.
    """
    global _default
    if _default is None:
        try:
            _default = build_provider()
        except ProviderError:
            # A misconfigured provider must not take the whole platform down -
            # No-AI mode is always a valid place to land.
            _default = NoAIProvider()
    return _default


def reset_default() -> None:
    global _default
    _default = None


def describe_modes() -> list[dict]:
    """Everything the settings UI needs to render the model chooser."""
    return [
        {
            "mode": AIMode.API_KEY.value,
            "label": "Bring your own model",
            "description": (
                "Any hosted LLM: Anthropic, OpenAI, Gemini, Azure. The agent explores, "
                "plans, generates tests and reasons; the key is sealed in the local vault "
                "and capped by a budget. Recommended."
            ),
            "requires": ["provider", "api_key", "model"],
            "providers": ["anthropic", "openai", "gemini", "azure_openai", "openai_compatible"],
            "recommended": True,
        },
        {
            "mode": AIMode.LOCAL.value,
            "label": "Local model (offline)",
            "description": (
                "Ollama or any OpenAI-compatible endpoint. The full agent, with nothing "
                "leaving the machine. Genuinely air-gapped, and every run is free."
            ),
            "requires": ["base_url", "model"],
            "providers": ["ollama", "openai_compatible"],
        },
        {
            "mode": AIMode.NO_AI.value,
            "label": "No model (deterministic only)",
            "description": (
                "No LLM connected. The mechanical layer still runs: execution, "
                "deterministic healing, scheduling, reporting, flake detection, so "
                "built tests re-run for free. Connect a model above for exploring, "
                "planning, generating and reasoning."
            ),
            "requires": [],
            "default": True,
        },
        {
            "mode": AIMode.BYO_AGENT.value,
            "label": "Local agent CLI bridge",
            "description": (
                "Optionally drive a coding-agent CLI you have already installed and "
                "authenticated locally. GaleQEA never sees, stores or forwards those "
                "credentials, and this mode runs only on loopback deployments."
            ),
            "requires": ["agent CLI on PATH"],
            "providers": ["claude_cli"],
        },
    ]
