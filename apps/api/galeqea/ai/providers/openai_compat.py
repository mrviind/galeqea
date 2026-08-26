"""OpenAI-compatible provider.

One code path covers OpenAI, Azure OpenAI, Ollama, vLLM, LM Studio, LiteLLM and
anything else that speaks ``/v1/chat/completions``. That is deliberate: the
OpenAI wire format is the de-facto interop layer for local models, so talking it
directly over HTTP buys air-gapped support for free and keeps the offline
install free of a vendor SDK it would never call.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import Completion, Delta, LLMProvider, Message, ProviderError, Role, ToolSpec, Usage

PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.4, 1.6),
    "o3": (2.0, 8.0),
    "o4-mini": (1.1, 4.4),
}


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"
    supports_tools = True
    supports_vision = True

    #: Local endpoints are free; never invent a cost for them.
    local_hosts = ("localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal")

    def __init__(
        self,
        *,
        model: str,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        **opts,
    ):
        super().__init__(model=model, api_key=api_key, base_url=base_url.rstrip("/"), **opts)
        self.flavor: str = opts.get("flavor", "openai")  # openai|azure|ollama|generic
        self.api_version: str = opts.get("api_version", "2024-10-21")
        self.deployment: str = opts.get("deployment", model)
        timeout = httpx.Timeout(opts.get("timeout", 180.0), connect=15.0)
        self._client = httpx.AsyncClient(timeout=timeout)

    # ------------------------------------------------------------------ #
    def _endpoint(self, path: str) -> str:
        if self.flavor == "azure":
            return (
                f"{self.base_url}/openai/deployments/{self.deployment}"
                f"{path}?api-version={self.api_version}"
            )
        return f"{self.base_url}{path}"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if not self.api_key:
            return headers  # local endpoints usually need no auth at all
        if self.flavor == "azure":
            headers["api-key"] = self.api_key
        else:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @property
    def is_local(self) -> bool:
        return any(host in self.base_url for host in self.local_hosts)

    @staticmethod
    def _to_wire(messages: list[Message], system: str) -> list[dict]:
        wire: list[dict] = []
        if system:
            wire.append({"role": "system", "content": system})
        for msg in messages:
            if msg.role is Role.TOOL:
                # The Chat Completions API has no `is_error` field the way
                # Anthropic's tool_result does, so a failed tool is otherwise
                # indistinguishable from a success whose content happens to say
                # "error". Prefixing the content is the documented way to give
                # the model an unmissable signal — matching the Anthropic path.
                content = msg.content
                if msg.is_error and not content.startswith("[tool error]"):
                    content = "[tool error] " + content
                wire.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.tool_call_id,
                        "content": content,
                    }
                )
                continue
            entry: dict[str, Any] = {"role": msg.role.value}
            if msg.images:
                parts: list[dict] = [{"type": "text", "text": msg.content}] if msg.content else []
                parts += [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}}
                    for img in msg.images
                ]
                entry["content"] = parts
            else:
                entry["content"] = msg.content
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {
                            "name": c["name"],
                            "arguments": json.dumps(c.get("arguments", {})),
                        },
                    }
                    for c in msg.tool_calls
                ]
                entry.setdefault("content", None)
            wire.append(entry)
        return wire

    @staticmethod
    def _tools_to_wire(tools: list[ToolSpec] | None) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in (tools or [])
        ]

    def _usage(self, raw: dict) -> Usage:
        u = raw.get("usage") or {}
        usage = Usage(
            input_tokens=u.get("prompt_tokens", 0),
            output_tokens=u.get("completion_tokens", 0),
            cached_tokens=(u.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
        )
        if not self.is_local:
            rate_in, rate_out = PRICING.get(self.model, (0.0, 0.0))
            usage.cost_usd = (
                usage.input_tokens * rate_in + usage.output_tokens * rate_out
            ) / 1_000_000
        return usage

    # ------------------------------------------------------------------ #
    async def complete(
        self,
        messages: list[Message],
        *,
        system: str = "",
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 8192,
        response_format: dict | None = None,
    ) -> Completion:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_wire(messages, system),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = self._tools_to_wire(tools)
            body["tool_choice"] = "auto"
        if response_format:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "schema": response_format,
                    "strict": True,
                },
            }

        try:
            resp = await self._client.post(
                self._endpoint("/chat/completions"), headers=self._headers(), json=body
            )
            resp.raise_for_status()
            raw = resp.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"{self.flavor} endpoint returned {exc.response.status_code}: "
                f"{exc.response.text[:400]}"
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                f"cannot reach {self.base_url} - is the local model server running? ({exc})"
            ) from exc

        choice = (raw.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        tool_calls = [
            {
                "id": c.get("id", f"call_{i}"),
                "name": c["function"]["name"],
                "arguments": _loads_or_empty(c["function"].get("arguments", "{}")),
            }
            for i, c in enumerate(message.get("tool_calls") or [])
        ]
        return Completion(
            text=message.get("content") or "",
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason", "stop"),
            usage=self._usage(raw),
            model=self.model,
            provider=self.flavor,
        )

    async def stream(
        self,
        messages: list[Message],
        *,
        system: str = "",
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 8192,
    ) -> AsyncIterator[Delta]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_wire(messages, system),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = self._tools_to_wire(tools)

        # Tool-call arguments stream in fragments and must be reassembled by index.
        pending: dict[int, dict] = {}
        usage = Usage()
        try:
            async with self._client.stream(
                "POST", self._endpoint("/chat/completions"), headers=self._headers(), json=body
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    chunk = line[6:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        event = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    if event.get("usage"):
                        usage = self._usage(event)
                    for choice in event.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            yield Delta(text=delta["content"])
                        for tc in delta.get("tool_calls") or []:
                            idx = tc.get("index", 0)
                            slot = pending.setdefault(
                                idx, {"id": tc.get("id") or f"call_{idx}", "name": "", "args": ""}
                            )
                            # Most providers put the id only in the first
                            # fragment, but some send it a fragment late; adopt
                            # the real one over the synthetic fallback if so.
                            if tc.get("id") and slot["id"].startswith("call_"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["args"] += fn["arguments"]
        except httpx.RequestError as exc:
            raise ProviderError(f"stream to {self.base_url} failed: {exc}") from exc

        for slot in sorted(pending.items()):
            _, data = slot
            if not data["name"]:
                # A fragment stream that never carried a name is malformed; a
                # tool call with an empty name would 400 on the next turn. Skip
                # it rather than emit something the loop cannot dispatch.
                continue
            yield Delta(
                tool_call={
                    "id": data["id"],
                    "name": data["name"],
                    "arguments": _loads_or_empty(data["args"]),
                }
            )
        yield Delta(done=True, usage=usage)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        model = self.options.get("embedding_model") or (
            "nomic-embed-text" if self.flavor == "ollama" else "text-embedding-3-small"
        )
        resp = await self._client.post(
            self._endpoint("/embeddings"),
            headers=self._headers(),
            json={"model": model, "input": texts},
        )
        resp.raise_for_status()
        return [row["embedding"] for row in resp.json()["data"]]

    async def health(self) -> dict:
        try:
            resp = await self._client.get(f"{self.base_url}/models", headers=self._headers())
            ok = resp.status_code < 400
            return {
                "provider": self.flavor,
                "model": self.model,
                "status": "ready" if ok else "error",
                "detail": "" if ok else resp.text[:200],
                "local": self.is_local,
            }
        except httpx.RequestError as exc:
            return {
                "provider": self.flavor,
                "model": self.model,
                "status": "unreachable",
                "detail": str(exc),
            }

    async def aclose(self) -> None:
        await self._client.aclose()


def _loads_or_empty(raw: str) -> dict:
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {"_unparsed": raw}


class OllamaProvider(OpenAICompatibleProvider):
    """Convenience preset for a local Ollama daemon."""

    name = "ollama"

    def __init__(self, *, model: str = "llama3.1", base_url: str = "", **opts):
        super().__init__(
            model=model,
            api_key="",
            base_url=base_url or "http://localhost:11434/v1",
            flavor="ollama",
            **opts,
        )


class AzureOpenAIProvider(OpenAICompatibleProvider):
    name = "azure_openai"

    def __init__(self, *, model: str, api_key: str = "", base_url: str = "", **opts):
        super().__init__(
            model=model, api_key=api_key, base_url=base_url, flavor="azure", **opts
        )
