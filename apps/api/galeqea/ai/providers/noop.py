"""The No-AI provider - the default.

GaleQEA's core is a complete test platform without a model: authoring, running,
scheduling, reporting, deterministic locator healing, statistical flake
detection, failure-signature triage and manual RCA all work here. This provider
exists so that AI-only paths fail *loudly and usefully* rather than degrading
into a silent stub that quietly produces nothing.
"""

from __future__ import annotations

from .base import Completion, LLMProvider, Message, NoAIModeError, ToolSpec

MESSAGE = (
    "That needs a model, and none is connected yet. The mechanical layer still runs "
    "without one - running built tests, deterministic healing, scheduling, reporting "
    "and failure triage - so re-runs stay free. To let the agent explore, plan, "
    "generate and reason, open Settings → Model and connect one: any provider's API "
    "key, or a local endpoint (Ollama or OpenAI-compatible) for a fully offline setup."
)


class NoAIProvider(LLMProvider):
    name = "none"
    supports_tools = False
    supports_streaming = False

    def __init__(self, reason: str = "", **_: object):
        super().__init__(model="", api_key="", base_url="")
        # A caller can say *why* there is no model. "Your monthly budget is
        # spent" and "you never configured one" are different problems with
        # different fixes, and reporting both as the latter wastes the user's
        # time looking in the wrong place.
        self.reason = reason

    async def complete(
        self,
        messages: list[Message],
        *,
        system: str = "",
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        response_format: dict | None = None,
    ) -> Completion:
        raise NoAIModeError(self.reason or MESSAGE)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Falls through to the deterministic local encoder instead of failing:
        # semantic search and de-duplication stay useful without a model.
        from ..embeddings import local_embed

        return [local_embed(t) for t in texts]

    async def health(self) -> dict:
        return {
            "provider": self.name,
            "model": "",
            "status": "budget_exhausted" if self.reason else "no_ai_mode",
            "detail": self.reason or "core platform fully operational; AI features disabled",
        }
