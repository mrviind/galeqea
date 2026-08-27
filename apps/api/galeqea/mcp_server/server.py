"""QE Agent's MCP server.

QE Agent is MCP-first: the same registry that backs the built-in chat is exposed
here, so an external host (Claude Code, Cursor, VS Code) gets exactly the same
capabilities, the same schema validation and the same approval gate. There is no
"reduced MCP subset" to drift out of sync with the product.

Security follows the MCP guidance closely:

* **Tool annotations are honest.** ``readOnlyHint``/``destructiveHint``/
  ``openWorldHint`` come from the registry, so a client can decide what to
  confirm without trusting a description string.
* **State-mutating tools cannot mutate state here.** They file an approval
  request and return its id. Even a fully compromised MCP client can, at worst,
  put something in a human's review queue.
* **Least privilege.** A remote deployment authenticates with scoped tokens and
  OAuth 2.1 + PKCE (S256); tools declare the scopes they need.
* **Untrusted output stays data.** Anything fetched from a page, a document or a
  ticket is returned inside an explicit untrusted wrapper.
* **Rate limiting** is per-client and per-tool.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from ..ai.tools import ToolContext, registry
from ..ai.toolset import tool_catalog  # noqa: F401  (import registers the tools)
from ..config import settings
from ..db import session_scope

SERVER_NAME = "galeqea"
PROTOCOL_VERSION = "2025-11-25"


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class RateLimiter:
    """Per-client token bucket. Write tools get a much smaller allowance."""

    read_per_minute: int = 120
    write_per_minute: int = 20
    _events: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))

    def check(self, client_id: str, *, write: bool) -> tuple[bool, str]:
        key = f"{client_id}:{'w' if write else 'r'}"
        limit = self.write_per_minute if write else self.read_per_minute
        now = time.monotonic()
        window = self._events[key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= limit:
            retry = 60 - (now - window[0])
            return False, f"rate limit reached ({limit}/min); retry in {retry:.0f}s"
        window.append(now)
        return True, ""


limiter = RateLimiter()


# --------------------------------------------------------------------------- #
# Descriptors
# --------------------------------------------------------------------------- #
def list_tools() -> list[dict]:
    return [tool.as_mcp() for tool in registry.all()]


def list_resources(project_id: str) -> list[dict]:
    return [
        {"uri": f"galeqea://{project_id}/tests",
         "name": "Test cases",
         "description": "Every test case with category, status, steps and traceability.",
         "mimeType": "application/json"},
        {"uri": f"galeqea://{project_id}/requirements",
         "name": "Requirements",
         "description": "Ingested requirements with risk, acceptance criteria and open questions.",
         "mimeType": "application/json"},
        {"uri": f"galeqea://{project_id}/runs/latest",
         "name": "Latest run",
         "description": "The most recent run with per-test results and regression triage.",
         "mimeType": "application/json"},
        {"uri": f"galeqea://{project_id}/coverage",
         "name": "Coverage",
         "description": "Requirement coverage including the explicit list of gaps.",
         "mimeType": "application/json"},
        {"uri": f"galeqea://{project_id}/flaky",
         "name": "Flaky tests",
         "description": "Instability scores with the evidence behind each one.",
         "mimeType": "application/json"},
        {"uri": f"galeqea://{project_id}/approvals",
         "name": "Pending approvals",
         "description": "Changes awaiting human review.",
         "mimeType": "application/json"},
        {"uri": f"galeqea://{project_id}/app-model",
         "name": "App Model",
         "description": "The discovered screen and element graph of the application under test.",
         "mimeType": "application/json"},
    ]


def list_prompts() -> list[dict]:
    return [
        {"name": "analyze_requirements",
         "description": "Turn a requirement document into testable obligations with risk and gaps.",
         "arguments": [{"name": "doc_id", "description": "requirement document id", "required": False}]},
        {"name": "design_tests",
         "description": "Propose a reviewable test set for a requirement, covering negative and boundary cases.",
         "arguments": [{"name": "requirement_ref", "description": "e.g. REQ-014", "required": True}]},
        {"name": "triage_run",
         "description": "Explain a run's failures: what is new, what is known, what is flaky, what to do first.",
         "arguments": [{"name": "run_id", "description": "run id (defaults to the latest)", "required": False}]},
        {"name": "explain_failure",
         "description": "Evidence-cited root-cause analysis for one failed result.",
         "arguments": [{"name": "run_test_id", "description": "result id", "required": True}]},
        {"name": "coverage_review",
         "description": "Report the highest-risk untested requirements, worst first.",
         "arguments": []},
    ]


PROMPT_BODIES = {
    "analyze_requirements": (
        "Read the project's ingested requirements (resource galeqea://{project}/requirements). "
        "For each: state the obligation precisely, flag ambiguities as open questions rather "
        "than resolving them, and assign risk from consequence rather than wording. "
        "List any requirement that has no approved test."
    ),
    "design_tests": (
        "Design a test set for requirement {requirement_ref}. Cover the happy path, the "
        "meaningful negative paths and the boundaries. Categorise each as automated, manual "
        "or exploratory and justify the choice. Use the create_test tool - it files each "
        "proposal for human review rather than creating anything directly."
    ),
    "triage_run": (
        "Fetch run {run_id} with get_run and explain its outcome. Lead with what is NEW - "
        "known failures and flaky tests are noise for this purpose. For the most important "
        "new failure, call run_rca and summarise the evidence."
    ),
    "explain_failure": (
        "Call run_rca for result {run_test_id}. Present the ranked hypotheses with their "
        "evidence citations, distinguish clearly between a product defect and a test defect, "
        "and end with the single most useful next action."
    ),
    "coverage_review": (
        "Call get_coverage. Lead with the highest-risk requirements that have no approved "
        "test - not with the coverage percentage. Then list requirements that are covered "
        "only weakly and say why each is weak."
    ),
}


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
async def call_tool(
    name: str,
    arguments: dict,
    *,
    project_id: str,
    client_id: str = "mcp",
    scopes: list[str] | None = None,
    user_id: str | None = None,
) -> dict:
    tool = registry.get(name)
    if tool is None:
        return _error(f"unknown tool {name!r}. Available: {', '.join(sorted(t.name for t in registry.all()))}")

    allowed, reason = limiter.check(client_id, write=not tool.read_only)
    if not allowed:
        return _error(reason)

    if scopes is not None and tool.scopes:
        missing = [s for s in tool.scopes if s not in scopes and "*" not in scopes]
        if missing:
            return _error(f"this token lacks the required scope(s): {', '.join(missing)}")

    with session_scope() as db:
        from ..models import User

        user = db.get(User, user_id) if user_id else None
        ctx = ToolContext(
            db=db,
            project_id=project_id,
            user=user,
            actor_kind="agent",     # an MCP caller is never treated as a human approver
            agent_role="mcp_client",
            provider=_provider(),
        )
        result = await registry.invoke(name, arguments, ctx)

    # `_ui` drives QE Agent's own workspace panes and means nothing to an external
    # MCP client. It is also a duplicate of data already in the result, so sending
    # it would both confuse the consumer and violate the declared outputSchema's
    # intent. Stripped at the boundary, in one place.
    payload = {k: v for k, v in result.items() if k != "_ui"}

    return {
        # The spec asks for the serialised JSON in a text block alongside the
        # structured field, for clients that do not read structuredContent.
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}],
        "isError": not payload.get("ok", True),
        "structuredContent": payload,
    }


async def read_resource(uri: str) -> dict:
    try:
        _, _, rest = uri.partition("galeqea://")
        project_id, _, path = rest.partition("/")
    except ValueError:
        return _error(f"malformed resource uri {uri!r}")

    handlers = {
        "tests": ("list_tests", {}),
        "requirements": ("list_requirements", {}),
        "runs/latest": ("get_run", {"latest": True}),
        "coverage": ("get_coverage", {}),
        "flaky": ("get_flaky_tests", {}),
        "approvals": ("get_audit_trail", {"limit": 25}),
    }
    if path == "app-model":
        with session_scope() as db:
            from ..api.routes.intelligence import app_model
            from ..models import Project

            project = db.get(Project, project_id)
            payload = app_model(project=project, db=db)
        return _resource(uri, payload)

    entry = handlers.get(path)
    if entry is None:
        return _error(f"unknown resource path {path!r}")

    tool_name, arguments = entry
    with session_scope() as db:
        ctx = ToolContext(db=db, project_id=project_id, actor_kind="agent",
                          agent_role="mcp_client", provider=_provider())
        result = await registry.invoke(tool_name, arguments, ctx)
    return _resource(uri, result)


def get_prompt(name: str, arguments: dict, *, project_id: str) -> dict:
    body = PROMPT_BODIES.get(name)
    if body is None:
        return _error(f"unknown prompt {name!r}")
    filled = body.format(
        project=project_id,
        requirement_ref=arguments.get("requirement_ref", "the requirement"),
        run_id=arguments.get("run_id", "the latest run"),
        run_test_id=arguments.get("run_test_id", "the failed result"),
    )
    return {
        "description": next(p["description"] for p in list_prompts() if p["name"] == name),
        "messages": [{"role": "user", "content": {"type": "text", "text": filled}}],
    }


def _provider():
    from ..ai.providers.registry import default_provider

    return default_provider() if settings.ai_enabled else None


def _error(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _resource(uri: str, payload: Any) -> dict:
    return {
        "contents": [{
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(payload, indent=2, default=str),
        }]
    }


def server_info() -> dict:
    return {
        "name": SERVER_NAME,
        "version": "0.1.0",
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"subscribe": False, "listChanged": False},
            "prompts": {"listChanged": False},
        },
        "instructions": (
            "QE Agent exposes an AI-driven test automation platform. Read-only tools "
            "return data immediately. Every state-changing tool files a human approval "
            "request and returns its id - it does not perform the change. Confirm with "
            "the user before calling any tool whose annotations mark it destructive or "
            "open-world."
        ),
    }
