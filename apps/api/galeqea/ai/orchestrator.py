"""Chat orchestration.

Every message takes one of three paths, chosen in this order:

1. **Deterministic route.** A confident rule match dispatches straight to a tool.
   Instant, free, and identical whether or not a model is configured - which is
   what makes the chat interface genuinely usable in No-AI mode rather than a
   disabled shell.
2. **Deterministic answer.** Some intents (coverage gaps, flakiness, approvals,
   run status, RCA) are fully answerable from data. These render a structured
   answer with no model involved even when one is available, because a computed
   answer beats a generated one every time.
3. **Agent loop.** Only genuinely open-ended requests reach the model.

Rich responses are returned as typed *blocks* the UI renders as cards - proposal
lists, run controls, approval prompts, tables - rather than as markdown the front
end has to parse back apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..core.events import Ev, Event, bus
from ..core.safety import scan
from ..models import (
    AgentRole,
    ApprovalRequest,
    ApprovalStatus,
    ChatMessage,
    ChatSession,
    Project,
    Run,
    RunStatus,
    RunTest,
    User,
)
from ..models.base import utcnow
from . import prompts
from .agent import Agent
from .memory import MemoryStore
from .plan_gate import (
    classify_reply,
    clear_plan,
    execute_plan,
    pending_plan,
    stash_plan,
    summarise_execution,
)
from .providers.base import LLMProvider, Message, Role
from .providers.registry import default_provider, for_project
from .router import route
from .tools import ToolContext
from .toolset import registry  # noqa: F401  (importing registers every tool)


# --------------------------------------------------------------------------- #
# Next-step suggestions
# --------------------------------------------------------------------------- #
#: Per tool, what usually comes next. Each entry is a function of the tool's
#: arguments and result, returning prompts the user can send as-is. The
#: pipeline they encode is the QE workflow itself: requirements → scenarios →
#: script → data → run → diagnose → file. Keeping this deterministic means the
#: chips are consistent, free, and available in No-AI mode.
def _after_query_requirements(args: dict, result: dict) -> list[dict]:
    feature = args.get("feature") or args.get("ref") or "this feature"
    first = (result.get("requirements") or [{}])[0].get("ref")
    if not result.get("count"):
        return [{"label": "Upload a requirement document", "text": "How do I upload a requirement document?"}]
    if not result.get("acceptance_criteria_count"):
        return [{"label": "Supply criteria", "text": f"Here are the acceptance criteria for {feature}: "}]
    out = []
    if first:
        out.append({"label": f"Gherkin for {first}", "text": f"Generate Gherkin scenarios for {first}",
                    "tool": "generate_bdd_scenarios"})
    out.append({"label": "Coverage gaps", "text": f"Which requirements for {feature} have no tests?",
                "tool": "get_coverage"})
    return out


def _after_bdd(args: dict, result: dict) -> list[dict]:
    feature = result.get("feature") or "this feature"
    out = [{"label": "Render as Playwright", "tool": "generate_playwright_script",
            "text": f"Turn the first {feature} scenario into a Playwright script"}]
    if result.get("unresolved"):
        out.insert(0, {"label": "Fix TODO steps",
                       "text": f"Which {feature} scenarios have TODO actions, and what should the trigger be?"})
    return out


def _after_script(args: dict, result: dict) -> list[dict]:
    out = [
        {"label": "Review it", "tool": "review_test",
         "text": "Review the generated script for missing assertions or fragile locators"},
        {"label": "File for review", "tool": "create_test",
         "text": "File the generated script as a test case for review"},
    ]
    if result.get("unresolved_locators"):
        out.insert(0, {"label": "Record real locators",
                       "text": "How do I record the missing locators with the session recorder?"})
    out.append({"label": "Test data", "tool": "generate_test_data",
                "text": "Generate test data for the fields this script fills in"})
    return out


def _after_data(args: dict, result: dict) -> list[dict]:
    fields = ", ".join(f["name"] for f in (result.get("fields") or [])[:3])
    return [{"label": "Negative tests", "tool": "generate_bdd_scenarios",
             "text": f"Write negative scenarios for {fields} using the invalid variants"}]


def _after_run(args: dict, result: dict) -> list[dict]:
    return [{"label": "Watch it", "text": "Show me the live run"},
            {"label": "Only failed", "text": "rerun only failed"}]


def _after_rca(args: dict, result: dict) -> list[dict]:
    return [{"label": "File a ticket", "tool": "create_jira_ticket",
             "text": "Create a Jira ticket for this failure with the RCA attached"},
            {"label": "Heal it", "tool": "approve_heal",
             "text": "Is there a heal proposal for the element that failed?"}]


def _after_review(args: dict, result: dict) -> list[dict]:
    verdict = result.get("verdict")
    if verdict == "blocked":
        return [{"label": "What's blocking it", "text": "What must I fix before this test can be filed?"}]
    return [
        {"label": "Check criteria coverage", "tool": "judge_test_against_criteria",
         "text": "Does this test actually assert each acceptance criterion?"},
        {"label": "File for review", "tool": "create_test",
         "text": "File this test for review"},
    ]


def _after_judge(args: dict, result: dict) -> list[dict]:
    if result.get("uncovered_count"):
        return [{"label": "Cover the gaps", "text": "Add assertions for the uncovered criteria"}]
    return [{"label": "File for review", "tool": "create_test", "text": "File this test for review"}]


NEXT_STEPS = {
    "query_requirements": _after_query_requirements,
    "review_test": _after_review,
    "judge_test_against_criteria": _after_judge,
    "generate_bdd_scenarios": _after_bdd,
    "generate_playwright_script": _after_script,
    "generate_test_data": _after_data,
    "run_tests": _after_run,
    "run_rca": _after_rca,
    "explain_failure": _after_rca,
}


#: Tools worth suggesting again even after they have run — a run or a diagnosis
#: is naturally repeatable. Everything else is a one-time step in the pipeline,
#: so re-suggesting it after it is done is noise ("generate a script" when a
#: script already exists).
_REPEATABLE = frozenset({"run_tests", "run_rca", "explain_failure"})


def session_tool_history(session, this_turn_steps: list[dict] | None = None) -> frozenset[str]:
    """Every tool that has run in this conversation, prior turns plus this one.

    Read from the persisted ChatMessage.tool_calls rather than re-derived, so it
    survives a reload and reflects the real history a returning user sees.
    """
    done: set[str] = set()
    for message in getattr(session, "messages", []) or []:
        for call in getattr(message, "tool_calls", None) or []:
            name = call.get("tool") if isinstance(call, dict) else None
            if name:
                done.add(name)
    for step in this_turn_steps or []:
        if step.get("tool"):
            done.add(step["tool"])
    return frozenset(done)


def suggest_next(steps: list[dict], session_tools: frozenset[str] = frozenset(),
                 limit: int = 3) -> list[dict]:
    """Next-step chips that account for the whole conversation, not just one tool.

    Two things make this conversation-aware:

    * **The last productive tool this turn drives the base suggestions** — that is
      where the user's attention is.
    * **`session_tools`** is every tool that has already run in this conversation.
      A chip whose target has already been done — and is not inherently
      repeatable — is dropped, so the agent does not keep offering "generate a
      script" after a script exists. What is left is the genuine next move.
    """
    for step in reversed(steps or []):
        rule = NEXT_STEPS.get(step.get("tool", ""))
        if rule is None:
            continue
        result = step.get("result") or {}
        if not result.get("ok", True):
            continue
        try:
            chips = rule(step.get("arguments") or {}, result)
        except Exception:  # noqa: BLE001 - a suggestion must never break a reply
            return []
        fresh = [
            c for c in chips
            if not (c.get("tool") and c["tool"] in session_tools and c["tool"] not in _REPEATABLE)
        ]
        return fresh[:limit]
    return []

@dataclass(slots=True)
class ChatReply:
    text: str
    blocks: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    path: str = "agent"           # router | computed | agent
    trace_id: str | None = None
    usage: dict = field(default_factory=dict)
    pending_approvals: list[str] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    #: What the user might sensibly do next, as prompts they can send with one
    #: click. Derived deterministically from what just happened - never from
    #: the model - so they are the same for the same state and cost nothing.
    suggestions: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "text": self.text, "blocks": self.blocks, "path": self.path,
            "trace_id": self.trace_id, "usage": self.usage,
            "pending_approvals": self.pending_approvals, "warnings": self.warnings,
            "suggestions": self.suggestions,
        }


class Orchestrator:
    def __init__(
        self, db: Session, *, provider: LLMProvider | None = None, project_id: str | None = None
    ):
        self.db = db
        # Per-project key first; the process default only when no project is known.
        self.provider = provider or (
            for_project(db, project_id) if project_id else default_provider()
        )

    # ------------------------------------------------------------------ #
    async def handle(
        self,
        *,
        session: ChatSession,
        user: User,
        text: str,
        attachments: list[dict] | None = None,
    ) -> ChatReply:
        project_id = session.project_id
        ctx = ToolContext(
            db=self.db,
            project_id=project_id,
            user=user,
            actor_kind="agent",
            session_id=session.id,
            provider=self.provider,
        )

        # A user's own message is trusted, but content pasted *into* it may not be.
        injection = scan(text)
        warnings: list[dict] = []
        if injection.suspicious and injection.max_severity == "high":
            warnings.append({
                "kind": "prompt_injection",
                "severity": injection.max_severity,
                "message": (
                    "This message contains text that looks like an attempt to override my "
                    "instructions. I've treated it as content, not as a command."
                ),
                "findings": injection.as_dict()["findings"][:3],
            })

        # A pending plan takes precedence over the router: while the agent is
        # waiting on the user to confirm a plan, their next message is an answer
        # to it — proceed, stop, or a revision — not a fresh command.
        plan = pending_plan(session)
        gate = classify_reply(text, plan is not None)
        if gate == "stop":
            clear_plan(session)
            return ChatReply(text="Cancelled — the plan will not run.", path="computed",
                             warnings=warnings)
        if gate == "proceed":
            return await self._execute_pending_plan(session, plan, ctx, warnings)
        if gate == "amend":
            # Not a yes and not a no: the user wants something else. Drop the
            # stale plan and handle the message as a new request.
            clear_plan(session)

        await self._status(session, project_id, "Understanding the request")

        last_run = self._last_run(project_id)
        routed = route(text, last_run_id=last_run.id if last_run else None)

        # --- path 1: confident deterministic dispatch --------------------- #
        if routed.confident:
            await self._status(session, project_id, routed.explanation or f"Running {routed.tool}")
            result = await registry.invoke(routed.tool, routed.arguments, ctx)
            reply = self._render_tool_result(routed.tool, routed.arguments, result, routed)
            reply.path = "router"
            reply.warnings = warnings
            return reply

        # --- path 2: computed answers ------------------------------------- #
        computed = await self._computed(routed.intent, text, project_id, last_run)
        if computed is not None:
            computed.warnings = warnings
            computed.path = "computed"
            return computed

        # --- path 3: the agent -------------------------------------------- #
        if not settings.ai_enabled:
            return ChatReply(
                text=(
                    "I couldn't match that to an action I can take without a model, and "
                    "GaleQEA is currently in No-AI mode.\n\n"
                    "Things I can still do right now: run tests (\"run the smoke tests on "
                    "staging\"), re-run failures, schedule runs, list tests, show coverage "
                    "gaps, score flaky tests, and explain a failure from its evidence.\n\n"
                    "To enable open-ended requests, configure a model in Settings → Model — "
                    "including a fully offline local one."
                ),
                blocks=[{"type": "mode_notice", "mode": "no_ai",
                         "capabilities": _NO_AI_CAPABILITIES}],
                path="computed",
                warnings=warnings,
            )

        await self._status(session, project_id, "Thinking")
        memory = MemoryStore(self.db, project_id)
        agent = Agent(
            provider=self.provider,
            registry=registry,
            role=AgentRole.ORCHESTRATOR,
            system_prompt=prompts.system_prompt(
                AgentRole.ORCHESTRATOR,
                project_context=self._project_context(project_id, last_run),
                memory=memory.context_block(text),
            ),
        )
        result = await agent.run(text, ctx, history=self._history(session))

        # If the agent proposed a plan this turn, remember it so the user's next
        # message can confirm it. The proposal is the last propose_plan step's
        # own result, which already carries the registry-annotated effects.
        for step in reversed(result.steps):
            if step.get("tool") == "propose_plan" and step["result"].get("ok"):
                stash_plan(session, step["result"])
                break

        return ChatReply(
            text=result.text or "Done.",
            blocks=_blocks_from_steps(result.steps),
            tool_calls=[{"tool": s["tool"], "arguments": s["arguments"]} for s in result.steps],
            path="agent",
            trace_id=result.trace_id,
            usage=result.as_dict()["usage"],
            pending_approvals=result.pending_approvals,
            warnings=warnings,
            suggestions=suggest_next(
                result.steps, session_tools=session_tool_history(session, result.steps)
            ),
        )

    async def _execute_pending_plan(self, session, plan, ctx, warnings) -> ChatReply:
        """Run a plan the user just confirmed.

        The stored plan runs, not a re-planned one, so "yes" means exactly what
        was shown. Every write step still meets the approval gate as it runs —
        confirming the plan is consent to attempt it, never a bypass of review.
        """
        project_id = session.project_id
        clear_plan(session)  # a plan is confirmed once; it does not linger
        await self._status(session, project_id, f"Executing the plan: {plan.get('goal', '')[:60]}")

        async def emit(index, tool):
            await bus.publish(Event(
                type=Ev.AGENT_TOOL_CALL, project_id=project_id, session_id=session.id,
                payload={"step": index, "tool": tool, "summary": f"plan step {index}: {tool}",
                         "read_only": True, "requires_approval": False},
            ))

        executed = await execute_plan(plan, registry, ctx, emit=emit)
        pending = [e["result"]["approval_id"] for e in executed
                   if e["result"].get("approval_id")]
        blocks = _blocks_from_steps([
            {"tool": e["tool"], "arguments": {}, "result": e["result"]} for e in executed
        ])
        return ChatReply(
            text=summarise_execution(plan, executed),
            blocks=blocks,
            path="agent",
            pending_approvals=pending,
            warnings=warnings,
            suggestions=suggest_next(
                [{"tool": e["tool"], "result": e["result"]} for e in executed],
                session_tools=session_tool_history(
                    session, [{"tool": e["tool"]} for e in executed]
                ),
            ),
        )

    # ------------------------------------------------------------------ #
    async def _computed(
        self, intent: str, text: str, project_id: str, last_run: Run | None
    ) -> ChatReply | None:
        """Answers derived entirely from stored data - no model, no guessing."""
        if intent == "approvals":
            from ..core.approvals import pending_for_project

            pending = pending_for_project(self.db, project_id)
            if not pending:
                return ChatReply(text="Nothing is waiting for your approval.")
            return ChatReply(
                text=f"{len(pending)} item(s) are waiting for review.",
                blocks=[{
                    "type": "approval_list",
                    "items": [
                        {
                            "id": p.id, "title": p.title, "action": p.action,
                            "risk": p.risk, "summary": p.summary,
                            "requested_by": p.agent_role or p.requested_by_kind,
                            "diff": p.diff,
                        }
                        for p in pending[:20]
                    ],
                }],
            )

        if intent == "rca" and last_run:
            from ..intelligence.rca import analyze

            failures = list(
                self.db.execute(
                    select(RunTest).where(
                        RunTest.run_id == last_run.id,
                        RunTest.status.in_([RunStatus.FAILED, RunStatus.ERROR]),
                    )
                ).scalars()
            )
            if not failures:
                return ChatReply(text=f"Nothing failed in run #{last_run.number}.")
            target = _best_match(failures, text) or failures[0]
            report = await analyze(
                self.db, target, provider=self.provider if settings.ai_enabled else None,
                project_id=project_id,
            )
            return ChatReply(
                text=(
                    f"**{target.test_key} — {target.title}**\n\n"
                    f"{report.summary}\n\n"
                    f"Category: `{report.category}` · confidence {report.confidence:.0%} "
                    f"({report.generated_by})"
                ),
                blocks=[{
                    "type": "rca",
                    "rca_id": report.id,
                    "category": report.category,
                    "confidence": report.confidence,
                    "hypotheses": report.hypotheses,
                    "evidence": report.evidence,
                    "suggested_fix": report.suggested_fix,
                    "run_test_id": target.id,
                }],
            )

        if intent == "generate_tests":
            return ChatReply(
                text=(
                    "Test generation runs off your ingested requirements. "
                    + ("Upload a requirement document, or point me at one already ingested, "
                       "and I'll propose cases for review."
                       if settings.ai_enabled else
                       "In No-AI mode I can still scaffold cases directly from the requirement "
                       "structure — open Requirements → Generate to review the deterministic "
                       "proposals.")
                ),
                blocks=[{"type": "cta", "action": "open_requirements",
                         "label": "Open Requirements"}],
            )

        return None

    # ------------------------------------------------------------------ #
    def _render_tool_result(
        self, tool: str, arguments: dict, result: dict, routed
    ) -> ChatReply:
        if not result.get("ok", True):
            return ChatReply(
                text=f"That didn't work: {result.get('error', 'unknown error')}",
                blocks=[{"type": "error", "detail": result}],
            )
        if result.get("status") == "awaiting_approval":
            return ChatReply(
                text=(
                    f"{routed.explanation} This needs your approval before it takes effect."
                ),
                blocks=[{
                    "type": "approval_prompt",
                    "approval_id": result["approval_id"],
                    "risk": result.get("risk"),
                    "tool": tool,
                    "arguments": arguments,
                }],
                pending_approvals=[result["approval_id"]],
            )

        if tool == "run_tests":
            return ChatReply(
                text=f"{routed.explanation} Run #{result.get('number')} is starting.",
                blocks=[{
                    "type": "run_controls",
                    "run_id": result["run_id"],
                    "number": result.get("number"),
                    "test_count": result.get("test_count", 0),
                    "actions": ["run_again", "run_failed_only", "cancel", "schedule"],
                }],
            )
        if tool == "list_tests":
            return ChatReply(
                text=f"Found {result['count']} test case(s).",
                blocks=[{"type": "test_table", "tests": result["tests"]}],
            )
        if tool == "get_coverage":
            cov = result["coverage"]
            return ChatReply(
                text=cov["headline"],
                blocks=[{"type": "coverage", **cov}],
            )
        if tool == "get_flaky_tests":
            return ChatReply(
                text=f"{len(result['flaky'])} test(s) show instability.",
                blocks=[{"type": "flaky_table", "flaky": result["flaky"],
                         "quarantine_candidates": result.get("quarantine_candidates", [])}],
            )
        if tool == "get_run":
            run = result["run"]
            triage = run.get("triage") or {}
            return ChatReply(
                text=triage.get("headline") or f"Run #{run['number']} — {run['status']}.",
                blocks=[{"type": "run_summary", "run": run, "results": result["results"]}],
            )
        if tool == "select_tests_for_change":
            return ChatReply(
                text=result["coverage_note"],
                blocks=[{"type": "selection", **result}],
            )
        if tool == "cancel_run":
            return ChatReply(text="Cancellation requested; the runner will stop after the current step.")

        return ChatReply(text=routed.explanation or "Done.",
                         blocks=[{"type": "raw", "tool": tool, "result": result}])

    # ------------------------------------------------------------------ #
    def _history(self, session: ChatSession, limit: int = 12) -> list[Message]:
        recent = sorted(session.messages, key=lambda m: m.created_at)[-limit:]
        out: list[Message] = []
        for message in recent:
            if message.role == "user":
                out.append(Message(role=Role.USER, content=message.content))
            elif message.role == "assistant" and message.content:
                out.append(Message(role=Role.ASSISTANT, content=message.content))
        return out

    def _project_context(self, project_id: str, last_run: Run | None) -> str:
        from ..models import TestCase, TestStatus

        project = self.db.get(Project, project_id)
        counts = {}
        for case in self.db.execute(
            select(TestCase).where(TestCase.project_id == project_id)
        ).scalars():
            counts[case.category] = counts.get(case.category, 0) + 1
            if case.status == TestStatus.PROPOSED:
                counts["awaiting_review"] = counts.get("awaiting_review", 0) + 1

        lines = [
            f"Project: {project.name} ({project.key})" if project else "Project: unknown",
            f"Environments: {', '.join((project.environments or {}) if project else {}) or 'none configured'}",
            f"Test counts: {counts or 'no tests yet'}",
        ]
        if last_run:
            lines.append(
                f"Most recent run: #{last_run.number} ({last_run.status}) "
                f"id={last_run.id} totals={last_run.totals}"
            )
        pending = self.db.execute(
            select(ApprovalRequest).where(
                ApprovalRequest.project_id == project_id,
                ApprovalRequest.status == ApprovalStatus.PENDING,
            )
        ).scalars().all()
        if pending:
            lines.append(f"{len(pending)} change(s) awaiting human approval.")
        return "\n".join(lines)

    def _last_run(self, project_id: str) -> Run | None:
        return self.db.execute(
            select(Run).where(Run.project_id == project_id)
            .order_by(Run.created_at.desc()).limit(1)
        ).scalar_one_or_none()

    async def _status(self, session: ChatSession, project_id: str, label: str) -> None:
        await bus.publish(Event(
            type=Ev.CHAT_STATUS,
            project_id=project_id,
            session_id=session.id,
            payload={"label": label, "at": utcnow().isoformat()},
        ))


# --------------------------------------------------------------------------- #
_NO_AI_CAPABILITIES = [
    "Run, re-run and schedule tests from plain English",
    "List and filter test cases",
    "Requirement coverage and gap analysis",
    "Statistical flaky-test detection",
    "Regression triage (new vs known vs flaky)",
    "Evidence-based root-cause analysis",
    "Deterministic locator healing",
    "Full reporting, dashboards and audit trail",
]


def _best_match(failures: list[RunTest], text: str) -> RunTest | None:
    needle = text.lower()
    for failure in failures:
        haystack = f"{failure.test_key} {failure.title}".lower()
        if any(word in haystack for word in needle.split() if len(word) > 3):
            return failure
    return None


def _blocks_from_steps(steps: list[dict]) -> list[dict]:
    """Promote notable tool results into renderable cards."""
    blocks: list[dict] = []
    for step in steps:
        result = step.get("result") or {}
        tool = step.get("tool")
        if not result.get("ok", True):
            continue
        if result.get("status") == "awaiting_approval":
            blocks.append({
                "type": "approval_prompt", "approval_id": result["approval_id"],
                "risk": result.get("risk"), "tool": tool, "arguments": step.get("arguments", {}),
            })
        elif tool == "run_tests" and result.get("run_id"):
            blocks.append({
                "type": "run_controls", "run_id": result["run_id"],
                "number": result.get("number"), "test_count": result.get("test_count", 0),
                "actions": ["run_again", "run_failed_only", "cancel", "schedule"],
            })
        elif tool == "get_coverage" and result.get("coverage"):
            blocks.append({"type": "coverage", **result["coverage"]})
        elif tool == "list_tests" and result.get("tests"):
            blocks.append({"type": "test_table", "tests": result["tests"][:25]})
        elif tool == "run_rca" and result.get("rca"):
            blocks.append({"type": "rca", **result["rca"]})
    return blocks


def persist_exchange(
    db: Session,
    session: ChatSession,
    *,
    user_text: str,
    reply: ChatReply,
    user_id: str | None,
    attachments: list[dict] | None = None,
) -> tuple[ChatMessage, ChatMessage]:
    user_message = ChatMessage(
        session_id=session.id, role="user", content=user_text,
        attachments=attachments or [],
    )
    assistant_message = ChatMessage(
        session_id=session.id, role="assistant",
        agent_role=AgentRole.ORCHESTRATOR,
        content=reply.text, blocks=reply.blocks,
        tool_calls=reply.tool_calls, trace_id=reply.trace_id,
        usage=reply.usage,
    )
    db.add_all([user_message, assistant_message])
    if session.title == "New conversation":
        session.title = user_text[:80]
    usage = dict(session.token_usage or {})
    for key in ("input_tokens", "output_tokens"):
        usage[key] = usage.get(key, 0) + reply.usage.get(key, 0)
    usage["cost_usd"] = round(usage.get("cost_usd", 0.0) + reply.usage.get("cost_usd", 0.0), 6)
    session.token_usage = usage
    db.flush()
    return user_message, assistant_message
