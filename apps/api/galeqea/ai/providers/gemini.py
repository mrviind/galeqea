"""Google Gemini provider (generateContent REST API)."""

from __future__ import annotations

from typing import Any

import httpx

from .base import Completion, LLMProvider, Message, ProviderError, Role, ToolSpec, Usage

PRICING: dict[str, tuple[float, float]] = {
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.0-flash": (0.10, 0.40),
}


class GeminiProvider(LLMProvider):
    name = "gemini"
    supports_tools = True
    supports_vision = True

    def __init__(
        self,
        *,
        model: str = "gemini-2.5-pro",
        api_key: str = "",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        **opts,
    ):
        super().__init__(model=model, api_key=api_key, base_url=base_url.rstrip("/"), **opts)
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0))

    @staticmethod
    def _to_contents(messages: list[Message]) -> list[dict]:
        contents: list[dict] = []
        for msg in messages:
            if msg.role is Role.SYSTEM:
                continue
            if msg.role is Role.TOOL:
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": msg.name or "tool",
                                    "response": {"result": msg.content},
                                }
                            }
                        ],
                    }
                )
                continue
            parts: list[dict] = []
            for image_b64 in msg.images:
                parts.append(
                    {"inlineData": {"mimeType": "image/png", "data": image_b64}}
                )
            if msg.content:
                parts.append({"text": msg.content})
            for call in msg.tool_calls:
                parts.append(
                    {"functionCall": {"name": call["name"], "args": call.get("arguments", {})}}
                )
            if parts:
                # Gemini names the assistant role "model".
                role = "model" if msg.role is Role.ASSISTANT else "user"
                contents.append({"role": role, "parts": parts})
        return contents

    @staticmethod
    def _clean_schema(schema: dict) -> dict:
        """Gemini rejects several JSON Schema keywords; drop them rather than 400."""
        unsupported = {"additionalProperties", "$schema", "definitions", "$defs", "examples"}
        if not isinstance(schema, dict):
            return schema
        out = {k: v for k, v in schema.items() if k not in unsupported}
        if "properties" in out:
            out["properties"] = {
                k: GeminiProvider._clean_schema(v) for k, v in out["properties"].items()
            }
        if "items" in out:
            out["items"] = GeminiProvider._clean_schema(out["items"])
        return out

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
            "contents": self._to_contents(messages),
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": self._clean_schema(t.parameters),
                        }
                        for t in tools
                    ]
                }
            ]
        if response_format:
            body["generationConfig"]["responseMimeType"] = "application/json"
            body["generationConfig"]["responseSchema"] = self._clean_schema(response_format)

        url = f"{self.base_url}/models/{self.model}:generateContent"
        try:
            resp = await self._client.post(
                url, params={"key": self.api_key}, json=body
            )
            resp.raise_for_status()
            raw = resp.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"Gemini returned {exc.response.status_code}: {exc.response.text[:400]}"
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(f"Gemini request failed: {exc}") from exc

        candidates = raw.get("candidates") or []
        if not candidates:
            reason = (raw.get("promptFeedback") or {}).get("blockReason", "no candidates returned")
            raise ProviderError(f"Gemini produced no output ({reason})")

        text_parts: list[str] = []
        tool_calls: list[dict] = []
        for i, part in enumerate(candidates[0].get("content", {}).get("parts", [])):
            if "text" in part:
                text_parts.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                tool_calls.append(
                    {"id": f"call_{i}", "name": fc["name"], "arguments": fc.get("args", {})}
                )

        meta = raw.get("usageMetadata") or {}
        usage = Usage(
            input_tokens=meta.get("promptTokenCount", 0),
            output_tokens=meta.get("candidatesTokenCount", 0),
            cached_tokens=meta.get("cachedContentTokenCount", 0),
        )
        rate_in, rate_out = PRICING.get(self.model, (0.0, 0.0))
        usage.cost_usd = (usage.input_tokens * rate_in + usage.output_tokens * rate_out) / 1_000_000

        return Completion(
            text="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=candidates[0].get("finishReason", "stop"),
            usage=usage,
            model=self.model,
            provider=self.name,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        model = self.options.get("embedding_model", "text-embedding-004")
        out: list[list[float]] = []
        for text in texts:
            resp = await self._client.post(
                f"{self.base_url}/models/{model}:embedContent",
                params={"key": self.api_key},
                json={"content": {"parts": [{"text": text}]}},
            )
            resp.raise_for_status()
            out.append(resp.json()["embedding"]["values"])
        return out

    async def health(self) -> dict:
        try:
            resp = await self._client.get(
                f"{self.base_url}/models/{self.model}", params={"key": self.api_key}
            )
            return {
                "provider": self.name,
                "model": self.model,
                "status": "ready" if resp.status_code < 400 else "error",
                "detail": "" if resp.status_code < 400 else resp.text[:200],
            }
        except httpx.RequestError as exc:
            return {"provider": self.name, "model": self.model, "status": "unreachable",
                    "detail": str(exc)}

    async def aclose(self) -> None:
        await self._client.aclose()
