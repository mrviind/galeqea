"""Bring-Your-Own-Agent bridge to a locally installed Claude Code CLI.

Why this exists in this exact shape
-----------------------------------
Many users hold a Claude Pro or Max subscription and reasonably ask "can QE Agent
just use that?". Routing those credentials through QE Agent is not permitted.
Anthropic's Claude Code legal and compliance documentation (updated 20 February
2026, enforcement from 4 April 2026) states that using OAuth tokens obtained
through Claude Free, Pro or Max accounts *in any other product, tool or service,
including the Agent SDK, is not permitted*, and that Anthropic does not permit
third-party developers to offer Claude.ai login inside their own applications or
to route requests through Free/Pro/Max plan credentials on behalf of users.

So QE Agent does the only compliant thing: it never touches those credentials at
all. It shells out to the ``claude`` binary **the user installed and
authenticated themselves, on their own machine**, exactly as if they had typed
the command. QE Agent supplies a prompt on stdin and reads stdout. It does not
read credential files, does not extract or forward tokens, and does not offer a
Claude.ai login.

Three guardrails enforce that boundary in code:

* the bridge refuses to run unless the server is bound to a loopback address -
  a hosted QE Agent cannot use a user's local subscription by proxy;
* the subprocess environment is scrubbed of every Anthropic credential variable
  QE Agent itself might hold, so the CLI can only ever use its own auth;
* every invocation is written to the audit ledger as a local-agent call.

Cloud and SaaS deployments default to API-key auth and this provider is
unavailable there by construction.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Any

from ...config import settings
from .base import Completion, LLMProvider, Message, ProviderError, Role, ToolSpec, Usage

LOOPBACK = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}

#: Scrubbed from the subprocess environment so the CLI cannot inherit a key that
#: belongs to QE Agent rather than to the user.
CREDENTIAL_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "GALEQEA_API_KEY",
    "OPENAI_API_KEY",
)


class ClaudeCLIProvider(LLMProvider):
    name = "claude_cli"
    supports_tools = False  # the CLI runs its own tool loop; QE Agent consumes text
    supports_streaming = False
    supports_vision = False

    def __init__(self, *, model: str = "", api_key: str = "", base_url: str = "", **opts):
        super().__init__(model=model, api_key="", base_url="", **opts)
        self.binary = opts.get("binary") or shutil.which("claude") or "claude"
        self.timeout = float(opts.get("timeout", 600))
        self.cwd = opts.get("cwd") or str(settings.home)
        self._assert_local_only()

    # ------------------------------------------------------------------ #
    @staticmethod
    def _assert_local_only() -> None:
        if settings.host not in LOOPBACK:
            raise ProviderError(
                "the Bring-Your-Own-Agent bridge only runs on a loopback-bound "
                f"server (host is {settings.host!r}). A hosted QE Agent must not "
                "drive a user's local Claude subscription - configure an API key "
                "provider for server deployments."
            )

    def _child_env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in CREDENTIAL_VARS}
        # Marks the call in the CLI's own telemetry as agent-initiated.
        env["CLAUDE_CODE_ENTRYPOINT"] = "galeqea-byo-bridge"
        return env

    def available(self) -> bool:
        return shutil.which(self.binary) is not None or os.path.exists(self.binary)

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
        if not self.available():
            raise ProviderError(
                f"'{self.binary}' was not found on PATH. Install Claude Code and run "
                "`claude` once to authenticate, then retry. QE Agent never handles "
                "those credentials itself."
            )

        prompt = self._render_prompt(messages, system, response_format)
        argv = [self.binary, "-p", "--output-format", "json"]
        if self.model:
            argv += ["--model", self.model]

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env=self._child_env(),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode()), timeout=self.timeout
            )
        except TimeoutError as exc:
            raise ProviderError(
                f"the local Claude Code CLI did not respond within {self.timeout:.0f}s"
            ) from exc
        except OSError as exc:
            raise ProviderError(f"could not launch '{self.binary}': {exc}") from exc

        if proc.returncode != 0:
            detail = stderr.decode(errors="replace").strip()[:600]
            hint = ""
            if "login" in detail.lower() or "auth" in detail.lower():
                hint = " Run `claude` in a terminal to authenticate, then retry."
            raise ProviderError(f"claude CLI exited {proc.returncode}: {detail}.{hint}")

        return self._parse_output(stdout.decode(errors="replace"))

    # ------------------------------------------------------------------ #
    @staticmethod
    def _render_prompt(
        messages: list[Message], system: str, response_format: dict | None
    ) -> str:
        """Flatten the conversation into the single prompt the CLI accepts."""
        parts: list[str] = []
        if system:
            parts.append(f"<role_instructions>\n{system}\n</role_instructions>")
        for msg in messages:
            if msg.role is Role.SYSTEM:
                parts.append(f"<role_instructions>\n{msg.content}\n</role_instructions>")
            elif msg.role is Role.USER:
                parts.append(msg.content)
            elif msg.role is Role.ASSISTANT and msg.content:
                parts.append(f"<previous_response>\n{msg.content}\n</previous_response>")
            elif msg.role is Role.TOOL:
                parts.append(f"<tool_result name=\"{msg.name}\">\n{msg.content}\n</tool_result>")
        if response_format:
            parts.append(
                "Respond with a single JSON object and nothing else. It must validate "
                f"against this JSON Schema:\n{json.dumps(response_format, indent=2)}"
            )
        return "\n\n".join(parts)

    def _parse_output(self, stdout: str) -> Completion:
        text = stdout.strip()
        usage = Usage()
        raw: dict[str, Any] = {}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None

        if isinstance(payload, dict):
            raw = payload
            text = payload.get("result") or payload.get("text") or text
            meta = payload.get("usage") or {}
            usage = Usage(
                input_tokens=meta.get("input_tokens", 0),
                output_tokens=meta.get("output_tokens", 0),
                cached_tokens=meta.get("cache_read_input_tokens", 0) or 0,
            )
            # Cost is the user's own subscription usage; QE Agent does not bill it.
            usage.cost_usd = 0.0
            if payload.get("is_error"):
                raise ProviderError(f"claude CLI reported an error: {text[:400]}")

        return Completion(
            text=text if isinstance(text, str) else json.dumps(text),
            usage=usage,
            model=self.model or "claude-code-cli",
            provider=self.name,
            raw=raw,
        )

    async def health(self) -> dict:
        if not self.available():
            return {
                "provider": self.name,
                "status": "not_installed",
                "detail": f"'{self.binary}' is not on PATH",
            }
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._child_env(),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
            return {
                "provider": self.name,
                "status": "ready" if proc.returncode == 0 else "error",
                "detail": stdout.decode(errors="replace").strip()[:120],
                "note": "runs under the user's own local Claude Code authentication",
            }
        except (TimeoutError, OSError) as exc:
            return {"provider": self.name, "status": "error", "detail": str(exc)}
