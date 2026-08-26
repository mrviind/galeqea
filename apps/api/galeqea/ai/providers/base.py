"""Provider-agnostic LLM interface.

Everything above this layer speaks only in ``Message``/``ToolSpec``/``Completion``.
Swapping Anthropic for a local Ollama model is a config change, never a code
change, and the No-AI provider satisfies the same interface by refusing loudly
instead of silently degrading.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(slots=True)
class Message:
    role: Role
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    # Base64 images for vision-capable providers (visual judging, healing).
    images: list[str] = field(default_factory=list)
    #: Set on a TOOL message whose tool failed. Providers translate it to the
    #: wire flag their API expects (`is_error` on Anthropic). Without it a failed
    #: tool is indistinguishable from a successful one that happened to return
    #: the word "error", and the model has to infer failure from JSON it may not
    #: read carefully — which is exactly when it invents a recovery that did not
    #: happen.
    is_error: bool = False

    def as_dict(self) -> dict:
        return {
            "role": self.role.value,
            "content": self.content,
            "tool_calls": self.tool_calls,
            "tool_call_id": self.tool_call_id,
            "name": self.name,
        }


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict          # JSON Schema
    # MCP-style annotations. ``destructive``/``external`` force the approval gate
    # regardless of what the model thinks it is doing.
    read_only: bool = True
    destructive: bool = False
    external: bool = False
    costs_money: bool = False
    #: Schema-valid example inputs. Sent to providers that support them; the
    #: documentation's guidance is that descriptions matter most but examples
    #: help for format-sensitive or nested inputs.
    input_examples: list[dict] = field(default_factory=list)


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cached_tokens + other.cached_tokens,
            self.cost_usd + other.cost_usd,
        )


@dataclass(slots=True)
class Completion:
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    provider: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Delta:
    """One streamed chunk."""

    text: str = ""
    tool_call: dict | None = None
    done: bool = False
    usage: Usage | None = None


class ProviderError(RuntimeError):
    """Base class for every provider failure, so callers can degrade gracefully."""


class NoAIModeError(ProviderError):
    """Raised when an AI-only path is reached while running in No-AI mode."""


class LLMProvider(abc.ABC):
    name: str = "base"
    supports_tools: bool = True
    supports_vision: bool = False
    supports_streaming: bool = True

    def __init__(self, *, model: str, api_key: str = "", base_url: str = "", **options: Any):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.options = options

    @abc.abstractmethod
    async def complete(
        self,
        messages: list[Message],
        *,
        system: str = "",
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        response_format: dict | None = None,
    ) -> Completion: ...

    async def stream(
        self,
        messages: list[Message],
        *,
        system: str = "",
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> AsyncIterator[Delta]:
        """Default: fall back to a single non-streamed call."""
        result = await self.complete(
            messages, system=system, tools=tools, temperature=temperature, max_tokens=max_tokens
        )
        yield Delta(text=result.text)
        for call in result.tool_calls:
            yield Delta(tool_call=call)
        yield Delta(done=True, usage=result.usage)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Optional. Providers without embeddings let the local encoder take over."""
        raise NotImplementedError(f"{self.name} does not provide embeddings")

    async def health(self) -> dict:
        return {"provider": self.name, "model": self.model, "status": "unknown"}

    async def aclose(self) -> None:
        return None
