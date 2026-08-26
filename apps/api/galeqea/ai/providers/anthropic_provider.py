"""Anthropic provider, built on the official ``anthropic`` Python SDK.

GaleQEA owns the agent loop rather than delegating to the SDK's beta tool
runner, because the loop has to be identical across six providers and must stop
at the approval gate and stream to the event bus between every turn. That is a
control requirement the runner does not expose, so the manual loop is the right
call here - but only the loop is hand-rolled; every request goes through the SDK.

Two current-API details are easy to get wrong and are handled explicitly below:

* ``temperature``/``top_p`` are **removed** on Opus 5, Opus 4.8/4.7, Sonnet 5 and
  Fable 5 - sending them is a 400, not a warning.
* ``stop_reason == "refusal"`` arrives as a **200**, so it must be checked before
  reading content. Server-side fallbacks are enabled by default on the models
  that support them so a refusal reroutes instead of surfacing as a dead end.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from .base import (
    Completion,
    Delta,
    LLMProvider,
    Message,
    ProviderError,
    Role,
    ToolSpec,
    Usage,
)
from .strict import NotStrictable, to_strict

DEFAULT_MODEL = "claude-opus-5"

#: USD per 1M tokens (input, output). Used for the cost ledger only.
PRICING: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

#: Models where sampling parameters were removed (sending them returns 400).
NO_SAMPLING = {
    "claude-fable-5", "claude-mythos-5", "claude-opus-5",
    "claude-opus-4-8", "claude-opus-4-7", "claude-sonnet-5",
}

#: Models that accept server-side refusal fallbacks.
SUPPORTS_FALLBACKS = {"claude-opus-5", "claude-fable-5", "claude-mythos-5"}

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    supports_tools = True
    supports_vision = True
    supports_streaming = True

    def __init__(self, *, model: str = DEFAULT_MODEL, api_key: str = "", base_url: str = "", **opts):
        super().__init__(model=model or DEFAULT_MODEL, api_key=api_key, base_url=base_url, **opts)
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ProviderError(
                "the Anthropic provider needs the official SDK: pip install anthropic"
            ) from exc

        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        # The SDK resolves ANTHROPIC_API_KEY / an `ant auth login` profile when
        # no explicit key is supplied, so a bare client is a valid configuration.
        self._client = AsyncAnthropic(**kwargs)
        self.effort: str = opts.get("effort", "high")

    # ------------------------------------------------------------------ #
    # Request construction
    # ------------------------------------------------------------------ #
    def _to_sdk_messages(self, messages: list[Message]) -> list[dict]:
        out: list[dict] = []
        for msg in messages:
            if msg.role is Role.SYSTEM:
                continue  # hoisted into the top-level `system` parameter
            if msg.role is Role.TOOL:
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.tool_call_id,
                                "content": msg.content,
                                # Anthropic's documented signal for a failed
                                # tool. Omitting it makes a failure read as a
                                # successful call that returned an error string.
                                **({"is_error": True} if msg.is_error else {}),
                            }
                        ],
                    }
                )
                continue

            blocks: list[dict] = []
            for image_b64 in msg.images:
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64,
                        },
                    }
                )
            if msg.content:
                blocks.append({"type": "text", "text": msg.content})
            for call in msg.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["name"],
                        "input": call.get("arguments", {}),
                    }
                )
            if blocks:
                out.append({"role": msg.role.value, "content": blocks})
        return out

    @staticmethod
    def _to_sdk_tools(tools: list[ToolSpec] | None) -> list[dict]:
        """Tool definitions as the Messages API wants them.

        Three things happen here beyond a field rename:

        * **Strict mode where the schema allows it.** A tool whose schema fits
          the strict subset is sent with ``strict: true``, so its arguments are
          guaranteed schema-valid by grammar-constrained sampling rather than by
          hoping. A tool with a free-form object in its contract stays
          non-strict and is validated locally instead.
        * **Input examples** go through when a tool declares them.
        * **A cache breakpoint on the last tool.** Tool definitions are the
          largest stable prefix of every request — 25 schemas that do not change
          between turns — and a single ``cache_control`` marker on the final one
          caches all of them. Without it the whole block is re-billed at full
          price on every turn of every conversation.
        """
        out: list[dict] = []
        for t in tools or []:
            entry: dict = {"name": t.name, "description": t.description}
            try:
                entry["input_schema"] = to_strict(t.parameters)
                entry["strict"] = True
            except NotStrictable:
                entry["input_schema"] = t.parameters
            if t.input_examples:
                entry["input_examples"] = t.input_examples
            out.append(entry)
        if out:
            out[-1]["cache_control"] = {"type": "ephemeral"}
        return out

    @staticmethod
    def _system_blocks(system: str) -> list[dict]:
        """The system prompt as a cacheable content block.

        The persona is identical on every turn; marking it caches it. The
        marker goes on the system block *and* the last tool, because the two
        together form the stable prefix — a breakpoint on only one of them
        caches only up to that point.
        """
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

    def _base_kwargs(self, *, max_tokens: int, temperature: float) -> dict:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            # Adaptive thinking: the model decides how much reasoning a step
            # needs. `budget_tokens` is rejected on every model we default to.
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort},
        }
        if self.model not in NO_SAMPLING:
            kwargs["temperature"] = temperature
        return kwargs

    def _cost(self, usage: Usage) -> float:
        rate_in, rate_out = PRICING.get(self.model, (0.0, 0.0))
        return (usage.input_tokens * rate_in + usage.output_tokens * rate_out) / 1_000_000

    # ------------------------------------------------------------------ #
    # Completion
    # ------------------------------------------------------------------ #
    async def complete(
        self,
        messages: list[Message],
        *,
        system: str = "",
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 16_000,
        response_format: dict | None = None,
    ) -> Completion:
        kwargs = self._base_kwargs(max_tokens=max_tokens, temperature=temperature)
        kwargs["messages"] = self._to_sdk_messages(messages)
        if system:
            kwargs["system"] = self._system_blocks(system)
        if tools:
            kwargs["tools"] = self._to_sdk_tools(tools)
        if response_format:
            kwargs["output_config"] = {
                **kwargs["output_config"],
                "format": {"type": "json_schema", "schema": response_format},
            }

        try:
            if self.model in SUPPORTS_FALLBACKS:
                # A safety refusal reroutes to a capable alternative instead of
                # failing the user's task outright.
                response = await self._client.beta.messages.create(
                    **kwargs, betas=[FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = await self._client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"Anthropic request failed: {type(exc).__name__}: {exc}") from exc

        # A refusal is an HTTP 200 - check before touching content.
        if getattr(response, "stop_reason", None) == "refusal":
            detail = getattr(response, "stop_details", None)
            category = getattr(detail, "category", None) if detail else None
            raise ProviderError(
                f"Anthropic declined this request (category: {category or 'unspecified'}). "
                "Rephrase the task or route it to a different model."
            )

        text_parts: list[str] = []
        tool_calls: list[dict] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({"id": block.id, "name": block.name, "arguments": block.input})

        usage = Usage(
            input_tokens=getattr(response.usage, "input_tokens", 0),
            output_tokens=getattr(response.usage, "output_tokens", 0),
            cached_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        )
        usage.cost_usd = self._cost(usage)

        return Completion(
            text="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=response.stop_reason or "stop",
            usage=usage,
            model=self.model,
            provider=self.name,
        )

    # ------------------------------------------------------------------ #
    # Streaming
    # ------------------------------------------------------------------ #
    async def stream(
        self,
        messages: list[Message],
        *,
        system: str = "",
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 64_000,
    ) -> AsyncIterator[Delta]:
        kwargs = self._base_kwargs(max_tokens=max_tokens, temperature=temperature)
        kwargs["messages"] = self._to_sdk_messages(messages)
        if system:
            kwargs["system"] = self._system_blocks(system)
        if tools:
            kwargs["tools"] = self._to_sdk_tools(tools)

        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield Delta(text=text)
                final = await stream.get_final_message()
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"Anthropic stream failed: {type(exc).__name__}: {exc}") from exc

        if getattr(final, "stop_reason", None) == "refusal":
            raise ProviderError("Anthropic declined this request mid-stream.")

        for block in final.content:
            if block.type == "tool_use":
                yield Delta(
                    tool_call={"id": block.id, "name": block.name, "arguments": block.input}
                )

        usage = Usage(
            input_tokens=getattr(final.usage, "input_tokens", 0),
            output_tokens=getattr(final.usage, "output_tokens", 0),
            cached_tokens=getattr(final.usage, "cache_read_input_tokens", 0) or 0,
        )
        usage.cost_usd = self._cost(usage)
        yield Delta(done=True, usage=usage)

    # ------------------------------------------------------------------ #
    async def health(self) -> dict:
        try:
            await self._client.messages.create(
                model=self.model,
                max_tokens=16,
                messages=[{"role": "user", "content": "ok"}],
            )
            return {"provider": self.name, "model": self.model, "status": "ready"}
        except Exception as exc:  # noqa: BLE001
            return {
                "provider": self.name,
                "model": self.model,
                "status": "error",
                "detail": f"{type(exc).__name__}: {exc}",
            }

    async def aclose(self) -> None:
        await self._client.close()


def parse_json_response(text: str) -> Any:
    """Tolerant JSON extraction for models answering without a schema."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0]
    return json.loads(text.strip())
