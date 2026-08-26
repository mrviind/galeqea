"""The agent loop.

Owned by GaleQEA rather than delegated to a provider SDK, because the loop must
be identical across every provider and must pause at three points the SDK
runners do not expose: the approval gate, the event stream, and the token
budget. Each iteration emits a timestamped status the chat UI renders live, so
a long-running task shows its work instead of spinning.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from ..config import settings
from ..core.events import Ev, Event, bus
from ..models import AgentRole, AgentTrace, UsageLedger
from ..models.base import utcnow
from .providers.base import (
    Completion,
    LLMProvider,
    Message,
    NoAIModeError,
    ProviderError,
    Role,
    Usage,
)
from .tools import ToolContext, ToolRegistry


@dataclass(slots=True)
class AgentResult:
    text: str
    steps: list[dict] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    trace_id: str = ""
    stopped_reason: str = "completed"
    pending_approvals: list[str] = field(default_factory=list)
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "steps": self.steps,
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
                "cost_usd": round(self.usage.cost_usd, 6),
            },
            "trace_id": self.trace_id,
            "stopped_reason": self.stopped_reason,
            "pending_approvals": self.pending_approvals,
            "error": self.error,
        }


class Agent:
    """One specialist role, running a bounded tool loop."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        registry: ToolRegistry,
        role: str = AgentRole.ORCHESTRATOR,
        system_prompt: str = "",
        tool_names: list[str] | None = None,
        max_steps: int | None = None,
        max_tokens_budget: int | None = None,
    ):
        self.provider = provider
        self.registry = registry
        self.role = role
        self.system_prompt = system_prompt
        self.tool_names = tool_names
        self.max_steps = max_steps or settings.max_agent_steps
        self.max_tokens_budget = max_tokens_budget or settings.max_tokens_per_run

    # ------------------------------------------------------------------ #
    async def run(
        self,
        goal: str,
        ctx: ToolContext,
        *,
        history: list[Message] | None = None,
        stream_to_bus: bool = True,
    ) -> AgentResult:
        trace = AgentTrace(
            project_id=ctx.project_id,
            session_id=ctx.session_id,
            goal=goal[:4000],
            agent_role=self.role,
            provider=getattr(self.provider, "name", ""),
            model=getattr(self.provider, "model", ""),
            status="running",
        )
        ctx.db.add(trace)
        ctx.db.flush()
        ctx.trace_id = trace.id
        ctx.agent_role = self.role

        messages: list[Message] = list(history or [])
        messages.append(Message(role=Role.USER, content=goal))

        specs = self.registry.llm_specs(self.tool_names)
        steps: list[dict] = []
        total = Usage()
        pending: list[str] = []
        started = time.monotonic()
        text = ""
        stopped = "completed"
        #: (tool, arguments) -> times it has failed this run, so a repeated
        #: failing call earns an escalating hint instead of silent repetition.
        failed_calls: dict[str, int] = {}

        if stream_to_bus:
            await self._emit(ctx, Ev.AGENT_STARTED, {"role": self.role, "goal": goal[:200]})

        try:
            for step_index in range(self.max_steps):
                if total.input_tokens + total.output_tokens > self.max_tokens_budget:
                    stopped = "token_budget_exhausted"
                    text += (
                        f"\n\nI stopped after {step_index} steps because this task hit the "
                        f"configured token budget ({self.max_tokens_budget:,}). "
                        "Raise it in Settings → Model, or narrow the request."
                    )
                    break

                completion = await self._complete(
                    messages, specs, ctx=ctx, stream=stream_to_bus, turn=step_index,
                )
                total = total + completion.usage

                if completion.text:
                    text = completion.text

                if not completion.tool_calls:
                    break

                messages.append(Message(
                    role=Role.ASSISTANT,
                    content=completion.text,
                    tool_calls=completion.tool_calls,
                ))

                for call in completion.tool_calls:
                    record = await self._invoke(call, ctx, step_index, stream_to_bus)
                    steps.append(record)
                    if record.get("approval_id"):
                        pending.append(record["approval_id"])

                    failed = not record["result"].get("ok", True)
                    signature = _call_signature(call)
                    if failed:
                        failed_calls[signature] = failed_calls.get(signature, 0) + 1
                    content = _tool_result_for_model(
                        record["result"],
                        repair=_repair_note(call["name"], record["result"],
                                            failed_calls.get(signature, 0)) if failed else "",
                    )
                    messages.append(Message(
                        role=Role.TOOL,
                        tool_call_id=call["id"],
                        name=call["name"],
                        content=content,
                        is_error=failed,
                    ))
            else:
                stopped = "step_limit_reached"
                text += (
                    f"\n\nI stopped after {self.max_steps} steps without finishing. "
                    "That usually means the request needs to be broken into smaller pieces - "
                    "tell me which part to do first."
                )

        except (NoAIModeError, ProviderError) as exc:
            trace.status = "error"
            trace.error = str(exc)
            stopped = "provider_error"
            text = str(exc)
            if stream_to_bus:
                await self._emit(ctx, Ev.AGENT_ERROR, {"message": str(exc)})

        trace.steps = steps
        trace.input_tokens = total.input_tokens
        trace.output_tokens = total.output_tokens
        trace.cost_usd = total.cost_usd
        trace.latency_ms = int((time.monotonic() - started) * 1000)
        trace.status = trace.status if trace.status == "error" else "completed"
        trace.finished_at = utcnow()

        ctx.db.add(UsageLedger(
            project_id=ctx.project_id,
            user_id=ctx.actor_id,
            provider=trace.provider,
            model=trace.model,
            agent_role=self.role,
            operation="agent_run",
            input_tokens=total.input_tokens,
            output_tokens=total.output_tokens,
            cached_tokens=total.cached_tokens,
            cost_usd=total.cost_usd,
            trace_id=trace.id,
        ))
        ctx.db.flush()

        if stream_to_bus:
            await self._emit(ctx, Ev.AGENT_FINISHED, {
                "role": self.role, "steps": len(steps), "stopped_reason": stopped,
                "cost_usd": round(total.cost_usd, 4),
            })

        return AgentResult(
            text=text, steps=steps, usage=total, trace_id=trace.id,
            stopped_reason=stopped, pending_approvals=pending,
            error=trace.error or "",
        )

    # ------------------------------------------------------------------ #
    async def _complete(
        self, messages: list[Message], specs, *, ctx: ToolContext | None = None,
        stream: bool = False, turn: int = 0,
    ) -> Completion:
        """One model turn - streamed to the browser token by token when possible.

        The reply used to arrive in the dock only when the whole turn had
        finished, which for a long answer meant twenty seconds of a spinner
        followed by a wall of text. Streaming sends each text delta over the
        event bus as it is generated; the browser renders a draft that fills in
        live, then swaps it for the persisted message when the turn completes.

        Tool calls are still assembled from the completed stream: a tool must
        not be invoked on a half-parsed argument object.
        """
        can_stream = stream and ctx is not None and getattr(self.provider, "supports_streaming", False)
        if not can_stream:
            return await self.provider.complete(
                messages, system=self.system_prompt, tools=specs or None, max_tokens=8000,
            )

        text_parts: list[str] = []
        tool_calls: list[dict] = []
        usage = Usage()
        async for delta in self.provider.stream(
            messages, system=self.system_prompt, tools=specs or None, max_tokens=8000,
        ):
            if delta.text:
                text_parts.append(delta.text)
                await self._emit(ctx, Ev.CHAT_DELTA, {"text": delta.text, "turn": turn})
            if delta.tool_call:
                tool_calls.append(delta.tool_call)
            if delta.done and delta.usage is not None:
                usage = delta.usage
        return Completion(
            text="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason="tool_use" if tool_calls else "stop",
            usage=usage,
            model=getattr(self.provider, "model", ""),
            provider=getattr(self.provider, "name", ""),
        )

    async def _invoke(
        self, call: dict, ctx: ToolContext, step_index: int, stream: bool
    ) -> dict:
        name = call["name"]
        arguments = call.get("arguments") or {}
        started = time.monotonic()

        if stream:
            tool = self.registry.get(name)
            await self._emit(ctx, Ev.AGENT_TOOL_CALL, {
                "step": step_index,
                "tool": name,
                "summary": _describe_call(name, arguments),
                "read_only": tool.read_only if tool else True,
                "requires_approval": bool(tool.approval_action) if tool else False,
            })

        result = await self.registry.invoke(name, arguments, ctx)
        duration_ms = int((time.monotonic() - started) * 1000)

        record = {
            "step": step_index,
            "tool": name,
            "arguments": arguments,
            "result": result,
            "duration_ms": duration_ms,
            "at": utcnow().isoformat(),
        }
        if isinstance(result, dict) and result.get("approval_id"):
            record["approval_id"] = result["approval_id"]

        if stream:
            # A tool may opt into driving the workspace by returning a `_ui`
            # projection. Only that projection crosses the socket — never the
            # whole result. A requirements query can carry fifty items of prose,
            # and pushing raw tool output to every connected browser would both
            # flood the stream and leak fields the tool never meant to publish.
            payload = {
                "step": step_index,
                "tool": name,
                "ok": bool(result.get("ok", True)),
                "duration_ms": duration_ms,
                "summary": _describe_result(name, result),
            }
            projection = result.get("_ui") if isinstance(result, dict) else None
            if isinstance(projection, dict):
                payload["ui"] = projection
            await self._emit(ctx, Ev.AGENT_STEP, payload)
        return record

    async def _emit(self, ctx: ToolContext, event_type: str, payload: dict) -> None:
        await bus.publish(Event(
            type=event_type,
            project_id=ctx.project_id,
            session_id=ctx.session_id,
            trace_id=ctx.trace_id,
            payload=payload,
        ))


# --------------------------------------------------------------------------- #
def _describe_call(name: str, arguments: dict) -> str:
    """A human-readable line for the live status feed."""
    from ..core.safety import redact

    safe = redact(arguments)
    verbs = {
        "list_tests": "Looking up test cases",
        "get_test": "Reading test",
        "get_run": "Fetching run results",
        "get_coverage": "Computing requirement coverage",
        "list_requirements": "Reading requirements",
        "get_flaky_tests": "Scoring test stability",
        "select_tests_for_change": "Ranking tests against the change",
        "run_rca": "Analysing the failure",
        "run_tests": "Starting a run",
        "cancel_run": "Cancelling the run",
        "create_test": "Proposing a new test",
        "update_test": "Proposing a test edit",
        "approve_heal": "Proposing a healed locator",
        "schedule_run": "Proposing a schedule",
        "create_jira_ticket": "Proposing a Jira ticket",
        "push_results_to_xray": "Proposing an Xray push",
        "fetch_ci_report": "Fetching the CI report",
        "remember": "Saving a project fact",
        "recall": "Recalling project knowledge",
        "get_audit_trail": "Reading the audit ledger",
    }
    verb = verbs.get(name, f"Calling {name}")
    hint = ""
    for key in ("selection", "title", "query", "test_id_or_key", "run_id", "summary", "search"):
        if safe.get(key):
            hint = f" — {str(safe[key])[:60]}"
            break
    return f"{verb}{hint}"


#: Ceiling on what one tool result contributes to the context window.
MAX_TOOL_RESULT_CHARS = 12_000


def _call_signature(call: dict) -> str:
    """A stable key for one tool call, so an identical retry is recognised."""
    return f"{call.get('name', '')}:{json.dumps(call.get('arguments') or {}, sort_keys=True, default=str)}"


def _repair_note(tool_name: str, result: dict, failure_count: int) -> str:
    """What the model should read first when a tool fails.

    Built from the tool's own recovery hint when it has one — many QE tools
    return a `guidance` field telling the caller exactly what to do next — and
    escalated when the *same* call has now failed more than once, which is the
    signature of a model stuck repeating a failing action instead of adapting.
    """
    error = result.get("error") or "the tool reported a failure"
    hint = (result.get("guidance") or "").strip()

    if failure_count >= 2:
        # The model already tried this exact call and it failed the same way.
        return (
            f"[repair] The call to {tool_name} FAILED AGAIN with the same error: {error}. "
            f"You have now made this identical call {failure_count} times. Do NOT repeat it. "
            + (f"Recovery: {hint} " if hint else "")
            + "Change the arguments, use a different tool, or call escalate_to_human with a "
            "specific question if you cannot proceed."
        )
    return (
        f"[repair] The call to {tool_name} failed: {error}. "
        + (f"{hint} " if hint else "")
        + "Adapt before trying again — do not repeat the same call unchanged."
    )


def _tool_result_for_model(result: dict, *, repair: str = "") -> str:
    """Serialise a tool result for the model — and only what the model needs.

    Two things happen here that did not before.

    **The `_ui` projection is dropped.** It exists to drive the workspace panes
    in the browser and is a *duplicate* of data already in the result: for a
    generated script it is a second full copy of both files, which measured at
    48% of the payload. Anthropic's guidance is explicit that bloated tool
    responses waste context and make it harder for the model to extract what
    matters, and paying twice for the same TypeScript is the clearest possible
    case of that.

    **Oversized results are truncated cleanly.** The previous code sliced the
    JSON string at a fixed offset, which cuts mid-token and hands the model
    malformed JSON to guess at. Now the payload is replaced by a valid object
    that says plainly what was dropped, so the model can narrow its query
    instead of hallucinating the missing half.
    """
    payload = {k: v for k, v in result.items() if k != "_ui"}
    encoded = json.dumps(payload, default=str)
    # A repair note is prepended, not merged into the JSON, so the model reads
    # the recovery instruction before the raw result rather than having to dig a
    # `guidance` field out of an error object it may skim.
    prefix = f"{repair}\n\n" if repair else ""
    if len(prefix) + len(encoded) <= MAX_TOOL_RESULT_CHARS:
        return prefix + encoded

    return prefix + json.dumps({
        "ok": payload.get("ok", True),
        "truncated": True,
        "original_size_chars": len(encoded),
        "note": (
            f"This result was {len(encoded)} characters, over the "
            f"{MAX_TOOL_RESULT_CHARS} limit, and has been withheld rather than cut "
            "mid-structure. Call the tool again with a narrower query — a specific "
            "ref, a smaller limit, or a filter — instead of guessing at the content."
        ),
        # Whatever the tool put in `guidance` is the one field worth its space:
        # it is the tool telling the model what to do next.
        "guidance": payload.get("guidance", ""),
    })


def _describe_result(name: str, result: dict) -> str:
    if not result.get("ok", True):
        return f"failed: {result.get('error', 'unknown error')}"[:200]
    if result.get("status") == "awaiting_approval":
        return "queued for human approval"
    if "count" in result:
        return f"{result['count']} result(s)"
    if "run_id" in result:
        return f"run #{result.get('number', '?')} started"
    # Verdict-carrying tools (review_test, judge_test_against_criteria,
    # check_run_health) — describe by the verdict/recommendation they returned.
    if "verdict" in result:
        extra = (f", {result['uncovered_count']} uncovered" if result.get("uncovered_count") else "")
        return f"{result['verdict']}{extra}"
    if "recommendation" in result:
        return str(result["recommendation"]).replace("_", " ")
    # `coverage` from the requirements report is a dict; from
    # judge_test_against_criteria it is a list. Guard the shape before reading it,
    # or a list here raises AttributeError mid-summary (found by the e2e test).
    if isinstance(result.get("coverage"), dict):
        cov = result["coverage"]
        return f"{cov.get('covered_requirements', 0)}/{cov.get('total_requirements', 0)} requirements covered"
    if "rca" in result:
        return f"{result['rca'].get('category')} (confidence {result['rca'].get('confidence', 0):.2f})"
    if "flaky" in result:
        return f"{len(result['flaky'])} unstable test(s)"
    return "done"
