"""The capability surface.

Every action GaleQEA can take is declared exactly once, here, with its schema,
its risk annotations and its handler. The chat agent and the MCP server are both
thin adapters over this registry, which means an external MCP host (Claude Code,
Cursor, VS Code) gets *precisely* the same capabilities, the same validation and
the same approval gate as the built-in chat - not a reduced or divergent subset.

Annotations are load-bearing, not documentation:

* ``read_only`` tools skip the gate entirely.
* ``destructive`` / ``external`` / ``costs_money`` tools force it, regardless of
  the configured approval mode and regardless of what the model believes.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..models import RiskTier


@dataclass(slots=True)
class ToolContext:
    """Everything a handler is allowed to touch. Nothing is reachable globally."""

    db: Any
    project_id: str
    user: Any = None
    actor_kind: str = "agent"
    agent_role: str = ""
    trace_id: str | None = None
    provider: Any = None
    session_id: str | None = None

    @property
    def actor_id(self) -> str | None:
        return getattr(self.user, "id", None)


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable
    read_only: bool = True
    destructive: bool = False
    external: bool = False
    costs_money: bool = False
    #: When set, the tool never executes directly - it files an approval request.
    approval_action: str | None = None
    risk: RiskTier = RiskTier.LOW
    scopes: list[str] = field(default_factory=list)
    category: str = "general"
    #: Human-readable name for display. MCP clients show this in a consent
    #: prompt, where "generate_playwright_script" reads as machinery and
    #: "Generate a Playwright script" reads as a decision someone can make.
    title: str = ""
    #: Example inputs, each valid against ``parameters``. Reaches the model as
    #: `input_examples` where the provider supports it.
    input_examples: list[dict] = field(default_factory=list)
    #: JSON Schema for the tool's structured result. The MCP spec is explicit:
    #: where an output schema is declared the server MUST return conforming
    #: structured content and clients SHOULD validate it. Declaring one turns a
    #: silently-changed result shape into a caught error at the boundary.
    output_schema: dict | None = None

    @property
    def requires_confirmation(self) -> bool:
        """MCP clients must confirm before invoking any of these."""
        return bool(
            self.destructive or self.external or self.costs_money or self.approval_action
        )

    def annotations(self) -> dict:
        return {
            "readOnlyHint": self.read_only,
            "destructiveHint": self.destructive,
            "idempotentHint": self.read_only,
            "openWorldHint": self.external,
        }

    def as_mcp(self) -> dict:
        out = {
            "name": self.name,
            "title": self.title or _humanise(self.name),
            "description": self.description,
            "inputSchema": self.parameters,
            "annotations": self.annotations(),
        }
        if self.output_schema:
            out["outputSchema"] = self.output_schema
        return out

    def as_llm_spec(self):
        from .providers.base import ToolSpec

        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            read_only=self.read_only,
            destructive=self.destructive,
            external=self.external,
            costs_money=self.costs_money,
            input_examples=list(self.input_examples),
        )


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        name: str,
        *,
        description: str,
        parameters: dict,
        read_only: bool = True,
        destructive: bool = False,
        external: bool = False,
        costs_money: bool = False,
        approval_action: str | None = None,
        risk: RiskTier = RiskTier.LOW,
        scopes: list[str] | None = None,
        category: str = "general",
        title: str = "",
        output_schema: dict | None = None,
        input_examples: list[dict] | None = None,
    ):
        def decorator(fn: Callable) -> Callable:
            self._tools[name] = Tool(
                name=name,
                description=description,
                parameters=_ensure_schema(parameters),
                handler=fn,
                read_only=read_only,
                destructive=destructive,
                external=external,
                costs_money=costs_money,
                approval_action=approval_action,
                risk=risk,
                scopes=scopes or [],
                category=category,
                title=title,
                output_schema=output_schema,
                input_examples=list(input_examples or []),
            )
            return fn

        return decorator

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def for_roles(self, *, read_only_only: bool = False) -> list[Tool]:
        return [t for t in self._tools.values() if not read_only_only or t.read_only]

    def llm_specs(self, names: list[str] | None = None):
        tools = [t for t in self._tools.values() if names is None or t.name in names]
        return [t.as_llm_spec() for t in tools]

    async def invoke(self, name: str, arguments: dict, ctx: ToolContext) -> dict:
        """Execute a tool, routing state-changing calls through the gate."""
        tool = self._tools.get(name)
        if tool is None:
            return {
                "ok": False,
                "error": f"unknown tool {name!r}",
                "available": sorted(self._tools),
            }

        problems = validate_arguments(tool.parameters, arguments)
        if problems:
            return {"ok": False, "error": "invalid arguments", "problems": problems}

        # --- the gate --------------------------------------------------- #
        if tool.approval_action and ctx.actor_kind != "human_direct":
            from ..core import approvals

            preview = await _maybe_await(
                getattr(tool.handler, "preview", None), arguments, ctx
            ) if hasattr(tool.handler, "preview") else None

            request = approvals.request(
                ctx.db,
                action=tool.approval_action,
                title=preview.get("title") if preview else f"{name}({_brief(arguments)})",
                project_id=ctx.project_id,
                resource_type=preview.get("resource_type", "") if preview else "",
                resource_id=preview.get("resource_id") if preview else None,
                summary=preview.get("summary", tool.description) if preview else tool.description,
                payload={"tool": name, "arguments": arguments},
                diff=preview.get("diff", {}) if preview else {},
                evidence=preview.get("evidence", {}) if preview else {},
                requested_by=ctx.actor_id,
                requested_by_kind=ctx.actor_kind,
                agent_role=ctx.agent_role,
                trace_id=ctx.trace_id,
                risk=tool.risk,
            )
            return {
                "ok": True,
                "status": "awaiting_approval",
                "approval_id": request.id,
                "risk": tool.risk.value if hasattr(tool.risk, "value") else tool.risk,
                "message": (
                    f"This action needs human approval before it takes effect. "
                    f"Request {request.id} is queued for review."
                ),
            }

        try:
            result = await _maybe_await(tool.handler, arguments, ctx)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if isinstance(result, dict) and "ok" not in result:
            result = {"ok": True, **result}
        return result if isinstance(result, dict) else {"ok": True, "result": result}


async def _maybe_await(fn, arguments: dict, ctx: ToolContext):
    if fn is None:
        return None
    out = fn(arguments, ctx)
    if inspect.isawaitable(out):
        return await out
    return out


def _humanise(name: str) -> str:
    """A display title derived from a tool name, when none was given.

    `query_requirements` becomes "Query requirements". Better than showing the
    identifier in a consent dialog, and better than requiring every tool to
    restate its own name.
    """
    words = name.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def _brief(arguments: dict) -> str:
    from ..core.safety import redact

    safe = redact(arguments)
    items = [f"{k}={str(v)[:40]}" for k, v in list(safe.items())[:3]]
    return ", ".join(items)


def _ensure_schema(schema: dict) -> dict:
    schema = dict(schema)
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    # Strictness is not optional: an unvalidated tool argument is the shortest
    # path from a prompt injection to a real side effect.
    schema.setdefault("additionalProperties", False)
    return schema


def validate_arguments(schema: dict, arguments: dict) -> list[str]:
    """Minimal but strict JSON Schema check - no external validator required."""
    problems: list[str] = []
    properties: dict = schema.get("properties", {})
    required: list = schema.get("required", [])

    for key in required:
        if key not in arguments or arguments[key] is None:
            problems.append(f"missing required argument '{key}'")

    if schema.get("additionalProperties") is False:
        for key in arguments:
            if key not in properties:
                problems.append(
                    f"unknown argument '{key}' (accepted: {', '.join(sorted(properties)) or 'none'})"
                )

    type_map = {
        "string": str, "number": (int, float), "integer": int,
        "boolean": bool, "array": list, "object": dict,
    }
    for key, spec in properties.items():
        if key not in arguments or arguments[key] is None:
            continue
        value = arguments[key]
        expected = spec.get("type")
        types = expected if isinstance(expected, list) else [expected]
        types = [t for t in types if t and t != "null"]
        if types and not any(
            isinstance(value, type_map[t]) for t in types if t in type_map
        ):
            problems.append(f"'{key}' should be {'/'.join(types)}, got {type(value).__name__}")
            continue
        if enum := spec.get("enum"):
            if value not in enum:
                problems.append(f"'{key}' must be one of {enum}, got {value!r}")
        if spec.get("type") == "string" and (maximum := spec.get("maxLength")):
            if len(value) > maximum:
                problems.append(f"'{key}' exceeds maxLength {maximum}")
    return problems


registry = ToolRegistry()
