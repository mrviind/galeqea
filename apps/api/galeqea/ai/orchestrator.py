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
from .prompt_slots import clear_prompt, is_cancel, pending_prompt, set_prompt
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


#: Tools worth suggesting again even after they have run. A run or a diagnosis
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

    * **The last productive tool this turn drives the base suggestions**: that is
      where the user's attention is.
    * **`session_tools`** is every tool that has already run in this conversation.
      A chip whose target has already been done (and is not inherently
      repeatable) is dropped, so the agent does not keep offering "generate a
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
        page: str | None = None,
    ) -> ChatReply:
        self._page = page
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

        # If the chat asked the user for one input (e.g. a URL), their next
        # message is the answer to that question, handled before anything else.
        resumed = await self._resume_prompt(session, text, ctx, warnings)
        if resumed is not None:
            return resumed

        # A proposed website test plan is waiting for approval, so this message is
        # the answer to it (approve / cancel / revise).
        resumed_plan = await self._resume_website_plan(session, text, ctx, warnings)
        if resumed_plan is not None:
            return resumed_plan

        # A pending plan takes precedence over the router: while the agent is
        # waiting on the user to confirm a plan, their next message is an answer
        # to it (proceed, stop, or a revision), not a fresh command.
        plan = pending_plan(session)
        gate = classify_reply(text, plan is not None)
        if gate == "stop":
            clear_plan(session)
            return ChatReply(text="Cancelled. The plan will not run.", path="computed",
                             warnings=warnings)
        if gate == "proceed":
            return await self._execute_pending_plan(session, plan, ctx, warnings)
        if gate == "amend":
            # Not a yes and not a no: the user wants something else. Drop the
            # stale plan and handle the message as a new request.
            clear_plan(session)

        # The first-run on-ramp: a URL in the message (or an explicit "test my
        # site") means the user wants to point GaleQEA at something and test it
        # right now. This works with no model, so a fresh user is never stuck.
        # Release-management verbs (create release, exit criteria, environment, plan,
        # start cycle, readiness, sign off, report) are deterministic, no model. Matched
        # BEFORE the URL on-ramp so "add environment staging http://…" creates an
        # environment rather than being read as a "test this URL" request.
        from .. import models
        from ..services import release_chat

        project = self.db.get(models.Project, project_id)
        if project is not None:
            handled = release_chat.try_handle(self.db, project, text, user)
            if handled is not None:
                reply_text, blocks = handled
                return ChatReply(text=reply_text, blocks=blocks, path="release",
                                 warnings=warnings)

            # "file a bug for <failure>" → a gated defect approval (deterministic).
            from ..services import defect_chat
            handled = defect_chat.try_handle(self.db, project, text, user)
            if handled is not None:
                reply_text, blocks = handled
                return ChatReply(text=reply_text, blocks=blocks, path="defect",
                                 warnings=warnings)

            # Jira daily loop: connect / import stories / check stale / write back.
            from ..services import jira_chat
            handled = jira_chat.try_handle(self.db, project, text, user)
            if handled is not None:
                reply_text, blocks = handled
                return ChatReply(text=reply_text, blocks=blocks, path="jira",
                                 warnings=warnings)

            # "push run #N to xray/zephyr/testrail" → a gated results push.
            from ..services import results_chat
            handled = results_chat.try_handle(self.db, project, text, user)
            if handled is not None:
                reply_text, blocks = handled
                return ChatReply(text=reply_text, blocks=blocks, path="results",
                                 warnings=warnings)

            # "connect slack" / "notify slack on failures" → notification targets.
            from ..services import notify_chat
            handled = notify_chat.try_handle(self.db, project, text, user)
            if handled is not None:
                reply_text, blocks = handled
                return ChatReply(text=reply_text, blocks=blocks, path="notify",
                                 warnings=warnings)

            # Tester-authored exploratory sessions: start / note:/bug: / end.
            from ..services import manual_session_chat
            handled = manual_session_chat.try_handle(self.db, project, text, user)
            if handled is not None:
                reply_text, blocks = handled
                return ChatReply(text=reply_text, blocks=blocks, path="sbtm",
                                 warnings=warnings)

            # "use <model> for locating" → per-role model routing; "cap locating at
            # N tokens" → a per-role per-call ceiling that stops-and-asks.
            from ..services import model_chat
            handled = model_chat.try_handle(self.db, project, text, user)
            if handled is not None:
                reply_text, blocks = handled
                return ChatReply(text=reply_text, blocks=blocks, path="model",
                                 warnings=warnings)

            # "generate tests from <doc>", "what's ambiguous in <doc>?", "export tests
            # for REQ-014 as testrail csv", "show traceability", "archive/dedupe
            # requirement docs" (WO#9-C).
            from ..services import requirements_chat
            handled = await requirements_chat.try_handle(self.db, project, text, user)
            if handled is not None:
                reply_text, blocks = handled
                return ChatReply(text=reply_text, blocks=blocks, path="requirements",
                                 warnings=warnings)

            # Page-aware Q&A: answer questions about whatever page is open, from the
            # database, with no model required (owner directive: chat is context-aware).
            from ..services import page_chat
            handled = page_chat.try_handle(self.db, project, text, user, page)
            if handled is not None:
                reply_text, blocks = handled
                return ChatReply(text=reply_text, blocks=blocks, path="page",
                                 warnings=warnings)

        onramp = await self._detect_onramp(session, text, ctx, warnings)
        if onramp is not None:
            return onramp

        await self._status(session, project_id, "Understanding the request")

        last_run = self._last_run(project_id)

        # Deterministic report/planning commands, no model: "coverage report as
        # markdown", "export last run as junit", "copy the run report for AI",
        # "plan coverage", "status brief", "quality retro last 30 days".
        command = await self._detect_chat_command(session, text, ctx, last_run)
        if command is not None:
            command.warnings = warnings
            command.path = "computed"
            return command

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
                    "That one needs a model to reason about, and none is connected yet.\n\n"
                    "Without one I can still run built tests (\"run the smoke tests on "
                    "staging\"), re-run failures, schedule runs, list tests, show coverage "
                    "gaps, score flaky tests, and explain a failure from its evidence, all "
                    "for free.\n\n"
                    "To let me explore, plan, generate and reason, connect a model in "
                    "Settings → Model (any provider, or a fully offline local one)."
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
        was shown. Every write step still meets the approval gate as it runs.
        Confirming the plan is consent to attempt it, never a bypass of review.
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
    # The first-run on-ramp: enter a URL, test it now. No model required.
    # ------------------------------------------------------------------ #
    async def _resume_prompt(self, session, text, ctx, warnings) -> ChatReply | None:
        """If the chat is waiting on one answer (a URL), consume this message as it."""
        from ..services.onramp import find_url

        prompt = pending_prompt(session)
        if not prompt or prompt.get("slot") != "smoke_url":
            return None
        if is_cancel(text):
            clear_prompt(session)
            return ChatReply(
                text="No problem. Paste a URL whenever you'd like to test a site.",
                path="onramp", warnings=warnings,
            )
        url = find_url(text)
        if not url:
            # Keep the slot open and ask again rather than losing their place.
            return ChatReply(
                text=("That doesn't look like a web address. Paste the full URL, "
                      "e.g. https://example.com, or say 'cancel'."),
                path="onramp", warnings=warnings,
            )
        clear_prompt(session)
        return await self._run_onramp(session, url, ctx, warnings)

    async def _detect_onramp(self, session, text, ctx, warnings) -> ChatReply | None:
        """Spot a fresh 'test this URL' request, or ask for the URL if it's missing."""
        from ..services.onramp import WANTS_TO_TEST, find_url

        url = find_url(text)
        if url:
            return await self._run_onramp(session, url, ctx, warnings)
        if WANTS_TO_TEST.search(text):
            set_prompt(session, slot="smoke_url", question="Which URL should I test?")
            return ChatReply(
                text=("Sure, which URL should I test? Paste the full address, "
                      "e.g. https://example.com. I'll open it in a real browser and "
                      "check it loads cleanly."),
                path="onramp", warnings=warnings,
            )
        return None

    async def _run_onramp(self, session, url, ctx, warnings) -> ChatReply:
        """Explore the URL, propose a test plan, and wait for approval in the chat.

        The plan itself never runs here. The human approves it first (the same gate
        every write goes through). Crawl is deterministic; a configured model sharpens
        the plan (hybrid).
        """
        from ..services import website_test as wt
        from ..services.onramp import normalize_url

        target = normalize_url(url)
        await self._status(session, session.project_id, f"Exploring {target}")
        # Browser-driven discovery shells out to the runner (seconds); keep it off
        # the event loop so the SSE stream and other requests stay responsive.
        import asyncio
        discovery = await asyncio.to_thread(wt.discover_pages, url)
        if not discovery.get("ok"):
            return ChatReply(
                text=f"I couldn't reach {target}: {discovery.get('error', 'no response')}. "
                     "Check the address and try again.",
                path="onramp", warnings=warnings,
            )
        plan = wt.build_plan(discovery)
        plan = await wt.enrich_plan_with_model(plan, self.provider)  # hybrid: model if present

        # Track the Golden Path journey for this target. The rail resumes on reload
        # and gives "continue"/"what's next" a referent. The plan is held on the
        # journey and only becomes the *pending* (approvable) plan once the plan card
        # is actually shown, so "continue" at Guardrails advances rather than runs.
        from ..models import JourneyStage
        from ..services import journeys
        journey = journeys.start(self.db, session.project_id, plan["target"], created_by=ctx.actor_id)
        first_time = not (journey.completed or [])  # never been stopped at guardrails yet

        # First encounter with a target is a real stop: an auth wall becomes an
        # Access card; otherwise the Guardrails card (what we will and won't do)
        # gets one explicit acknowledgement, remembered per target thereafter.
        auth_gated = (discovery.get("skipped", {}) or {}).get("auth", 0)
        if first_time and auth_gated:
            journeys.advance(self.db, journey, JourneyStage.ACCESS,
                             ran=False, discovery=discovery, plan=plan, plan_version=1)
            return self._access_reply(journey, discovery, warnings)
        if first_time:
            journeys.set_guardrails(self.db, journey,
                                    **journeys.default_guardrails(plan["target"], journey.environment))
            journeys.advance(self.db, journey, JourneyStage.GUARDRAILS,
                             discovery=discovery, plan=plan, plan_version=1)
            return self._guardrails_reply(journey, discovery, warnings)

        wt.stash_plan(session, plan)  # returning target: plan is approvable now
        journeys.advance(self.db, journey, JourneyStage.PLAN,
                         discovery=discovery, plan=plan, plan_version=1)
        return self._website_plan_reply(plan, journey, warnings)

    def _website_plan_reply(self, plan, journey, warnings) -> ChatReply:
        from ..services import journeys
        from ..services import test_plan as tp

        # The typed plan (every test type as a reviewable row) is derived from the
        # discovery and stored on the journey so toggles version it.
        stored = (journey.plan or {}).get("typed")
        typed = stored or tp.build_typed_plan(journey.discovery or {},
                                              version=journey.plan_version or 1)
        merged = {**(journey.plan or {}), "typed": typed}
        journeys.advance(self.db, journey, journey.stage, ran=False,
                         plan=merged, plan_version=typed["version"])

        # File the plan as a first-class approval request (idempotent), so the plan
        # a person approves is an audited gate reachable from HTTP/CLI/MCP too, not
        # only this chat reply.
        from .. import models
        from ..services import plan_approval
        project = self.db.get(models.Project, journey.project_id)
        plan_approval.request_plan_approval(self.db, project, journey)

        t = typed["totals"]
        n = plan["test_count"]
        return ChatReply(
            text=(f"Test plan v{typed['version']} for {plan['target']}: "
                  f"{t['types_enabled']} test type(s), {t['test_count']} test(s), "
                  f"~{t['est_minutes']} min, ~{t['est_build_tokens']:,} build tokens "
                  "(re-runs cost 0). Approve to run, toggle a type, or say what to change."),
            blocks=[{
                "type": "website_plan",
                "target": plan["target"],
                "pages": plan["pages"],
                "functional": plan["functional"],
                "non_functional": plan["non_functional"],
                "test_count": n,
                "discovered": (journey.discovery or {}).get("discovered", n),
                "enriched": plan.get("enriched", False),
                "notes": plan.get("notes", []),
                "auth_gated": (journey.discovery or {}).get("skipped", {}).get("auth", 0),
                "test_types": typed["types"],
                "totals": t,
                "plan_version": typed["version"],
            }],
            path="onramp", warnings=warnings,
            suggestions=[
                {"label": "Approve & run", "text": "approve"},
                {"label": "Test all pages", "text": "test all pages"},
            ],
        )

    def _guardrails_reply(self, journey, discovery, warnings) -> ChatReply:
        from ..services import journeys
        g = journey.guardrails or {}
        return ChatReply(
            text=(f"Before I test {journey.target}, here are the guardrails I'll work under "
                  f"({g.get('reason', '')}). Say 'continue' to accept, or adjust them."),
            blocks=[{"type": "guardrails_card", "target": journey.target,
                     "environment": journey.environment, "guardrails": g,
                     **journeys.describe(journey)}],
            path="onramp", warnings=warnings,
            suggestions=[
                {"label": "Continue", "text": "continue"},
                {"label": "Allow writes" if g.get("read_only") else "Make read-only",
                 "text": "allow writes" if g.get("read_only") else "make it read-only"},
            ],
        )

    def _access_reply(self, journey, discovery, warnings) -> ChatReply:
        from ..services import access, journeys
        auth = (discovery.get("skipped", {}) or {}).get("auth", 0)
        gated = [f["url"] for f in (discovery.get("findings") or []) if f.get("kind") == "auth_gated"][:5]
        kinds = access.detected_kinds(journey)
        kind_note = (f" It uses {' and '.join(kinds)} auth." if kinds else "")
        return ChatReply(
            text=(f"{journey.target} has {auth} page(s) behind a login.{kind_note} "
                  "I can log in for you, use test credentials, or skip the gated pages."),
            blocks=[{"type": "access_card", "target": journey.target, "auth_count": auth,
                     "gated": gated, "auth_kinds": kinds, **journeys.describe(journey)}],
            path="onramp", warnings=warnings,
            suggestions=[
                {"label": "Log in for me", "text": "log in for me"},
                {"label": "Add test credentials", "text": "add test credentials"},
                {"label": "Skip gated pages", "text": "skip gated pages"},
            ],
        )

    async def _resume_website_plan(self, session, text, ctx, warnings) -> ChatReply | None:
        """A proposed website plan is pending: this message approves, cancels, or revises it."""
        from ..services import website_test as wt

        plan = wt.pending_plan(session)
        if not plan:
            return None
        gate = classify_reply(text, True)
        if gate == "stop":
            wt.clear_plan(session)
            return ChatReply(text="Cancelled. Nothing was run.", path="onramp", warnings=warnings)
        if gate == "amend":
            # Not a yes/no, so drop the plan and handle the message fresh.
            wt.clear_plan(session)
            return None
        # proceed: approve the plan through the ONE gate (the same decide service
        # HTTP/CLI/MCP use: one audited human decision, SelfApprovalError for agents),
        # then run the tests that approval built.
        wt.clear_plan(session)
        from ..core import approvals
        from ..models import User
        from ..services import journeys, plan_approval

        journey = journeys.for_target(self.db, session.project_id, plan["target"])
        req = plan_approval.pending_for_journey(self.db, journey) if journey else None
        decider = self.db.get(User, ctx.actor_id) if ctx.actor_id else None
        built_keys: list[str] = []
        if req is not None and decider is not None and not decider.is_machine:
            try:
                outcome = approvals.approve(self.db, req.id, decider)
                built_keys = (outcome.result or {}).get("keys", [])
            except approvals.SelfApprovalError:
                pass  # a machine can't approve; fall back to the direct website run

        await self._status(session, session.project_id, f"Testing {plan['target']}")
        if built_keys:
            result = await wt.run_approved_keys(
                self.db, project_id=session.project_id, keys=built_keys,
                target=plan["target"], triggered_by=ctx.actor_id,
            )
        else:
            result = await wt.run_website_test(
                self.db, project_id=session.project_id, plan=plan, triggered_by=ctx.actor_id,
            )
        text_out = result.get("summary") or "Done."
        if result.get("timed_out"):
            text_out = (f"Testing {plan['target']} is still running (run "
                        f"#{result.get('run_number')}). Watch it in the run view.")
        # Advance the journey to the report stage, recording the run it produced.
        from ..models import JourneyStage
        from ..services import journeys
        journey = journeys.for_target(self.db, session.project_id, plan["target"])
        if journey is not None:
            journeys.advance(self.db, journey, JourneyStage.REPORT, run_id=result.get("run_id"))
        blocks = [{
            "type": "website_result",
            **{k: result.get(k) for k in (
                "ok", "status", "target", "passed", "failed", "pages",
                "run_id", "run_number")},
        }]
        return ChatReply(
            text=text_out, blocks=blocks, path="onramp", warnings=warnings,
            suggestions=[
                {"label": "Open the report", "text": "show the run report"},
                {"label": "Keep it green", "text": "keep it green"},
            ],
        )

    # ------------------------------------------------------------------ #
    async def _detect_chat_command(self, session, text, ctx, last_run) -> ChatReply | None:
        """Deterministic chat parity for the report and planning surfaces: a plain
        request like "coverage report as markdown" or "plan coverage" is answered
        directly from data (no model) and reports come back as cards carrying the
        same export actions the UI offers."""
        import re

        t = (text or "").lower()
        pid = session.project_id

        # --- Golden Path navigation ------------------------------------- #
        nav = re.search(r"\bwhat'?s next\b|\bnext step\b|\bwhere (are|was) we\b|\bresume\b|"
                        r"\bcontinue\b|\blooks good\b|\bproceed\b|\baccept\b|\bskip gated\b|"
                        r"\blog ?in\b|\badd (test )?credentials\b|\bforget (the )?(test )?credentials\b|"
                        r"\ballow writes\b|\bmake it read.?only\b|\bdata policy\b|\btest all pages\b|"
                        r"\bsave (the )?plan as tests\b|\bbuild the tests\b|\bbuild it\b|"
                        r"\bsmoke\b|\bapprove\b|\brun (it|all|everything|the (full )?suite|the tests)\b|"
                        r"\btriage\b|\b(rerun|quarantine|heal|mark expected|file (a )?bug)\b|"
                        r"\breadiness\b|\bgo.?no.?go\b|\bready to (ship|release)\b|\bsign.?off\b|"
                        r"\bpoke\b|\bexplore .{0,40}\b(as|for)\b|\bmake (this|it|a) (a )?test\b|"
                        r"\bkeep it green\b|\bkeep.green\b|\badd to ci\b|\bgithub action\b|\bschedule\b|"
                        r"\bshare( the| this)? report\b|\bshare it\b|\bpublish( the)? report\b|"
                        r"\b(enable|disable|turn on|turn off|add|drop|remove) \w", t)
        if nav:
            from ..services import journeys
            j = journeys.active_journey(self.db, pid)
            if j is not None:
                # handle() sets the reply's warnings; an empty list here is fine.
                reply = await self._journey_nav(j, t, session, ctx, [])
                if reply is not None:
                    return reply

        # --- planning tools (already deterministic) --------------------- #
        if re.search(r"\bplan coverage\b|\bcoverage plan\b|\bwhat (should i|to) (test|cover) next\b", t):
            return await self._planning_reply("plan_coverage", {}, ctx)
        if re.search(r"\bstatus brief\b|\bstand ?up\b|\bwhere (do|does) (we|things|testing) stand\b", t):
            return await self._planning_reply("test_status_brief", {}, ctx)
        if re.search(r"\bquality retro\w*\b|\bretrospective\b", t):
            days = re.search(r"last (\d+) days?", t)
            return await self._planning_reply(
                "quality_retrospective", {"days": int(days.group(1))} if days else {}, ctx)

        # --- report / export commands → a report card ------------------- #
        if not re.search(r"\breport\b|\bexport\b|\bcopy\b.{0,20}\bfor ai\b", t):
            return None
        if re.search(r"\bcoverage\b", t):
            rtype, base, ui = "coverage", f"/api/projects/{pid}/coverage", "/intelligence"
        elif re.search(r"\btraceability\b", t):
            rtype, base, ui = "traceability", f"/api/projects/{pid}/traceability", "/intelligence"
        elif re.search(r"\bflak", t):
            rtype, base, ui = "flaky", f"/api/projects/{pid}/flaky", "/intelligence"
        elif last_run is not None:
            rtype = "run"
            base, ui = f"/api/projects/{pid}/runs/{last_run.id}", f"/runs/{last_run.id}"
        else:
            return None

        summary, title = self._report_summary(rtype, last_run)
        formats = "JSON, Markdown or JUnit" if rtype == "run" else "JSON or Markdown"
        return ChatReply(
            text=f"Here's the {title.lower()}. Export it as {formats}, or copy it for an AI.",
            blocks=[{
                "type": "report_card", "report": rtype, "title": title,
                "api_base": base, "ui_href": ui, "junit": rtype == "run", "summary": summary,
            }],
        )

    async def _planning_reply(self, tool: str, args: dict, ctx) -> ChatReply:
        result = await registry.invoke(tool, args, ctx)
        if not result.get("ok", True):
            return ChatReply(text=f"That didn't work: {result.get('error', 'unknown error')}")
        ui = result.get("_ui") or {}
        blocks = []
        if ui.get("markdown"):
            blocks.append({"type": "doc", "title": ui.get("title") or tool, "markdown": ui["markdown"]})
        return ChatReply(text=result.get("guidance") or ui.get("title") or "Done.", blocks=blocks)

    def _journey_card(self, journey) -> ChatReply:
        from ..services import journeys

        info = journeys.describe(journey)
        nxt = info["next"]
        return ChatReply(
            text=f"You're on the **{info['stage']}** stage for {info['target']}. "
                 f"Next: {nxt['label'].lower()}.".replace("**", ""),
            blocks=[{"type": "journey_card", **info}],
            suggestions=[{"label": nxt["label"], "text": nxt["command"]}],
        )

    async def _journey_nav(self, journey, t, session, ctx, warnings) -> ChatReply | None:
        """Move an active journey through its stage-specific stops (access,
        guardrails) or edit its guardrails, all deterministic."""
        import re

        from ..models import JourneyStage
        from ..services import journeys
        from ..services import website_test as wt

        disc = journey.discovery or {}

        # Guardrails edits, available whenever the card is up.
        if re.search(r"\ballow writes\b", t):
            journeys.set_guardrails(self.db, journey, read_only=False, reason="writes allowed by you")
            return self._guardrails_reply(journey, disc, warnings)
        if re.search(r"\bmake it read.?only\b", t):
            journeys.set_guardrails(self.db, journey, read_only=True, reason="read-only by you")
            return self._guardrails_reply(journey, disc, warnings)
        m = re.search(r"\bdata policy (synthetic|seeded|none)\b", t)
        if m:
            journeys.set_guardrails(self.db, journey, data_policy=m.group(1))
            return self._guardrails_reply(journey, disc, warnings)

        # Forget stored credentials for the target, available at any stage.
        if re.search(r"\bforget (the )?(test )?credentials\b", t):
            from ..services import access
            access.forget(self.db, journey)
            return ChatReply(text=f"Forgotten. The stored credentials and session for "
                                  f"{journey.target} are wiped from the vault.",
                             path="onramp", warnings=warnings)

        # Access stage: pick how to handle the login wall.
        if journey.stage == JourneyStage.ACCESS:
            from ..services import access
            # Credentials were stored (via the secure form) → log in for real and
            # promote the pages that were behind the wall into the tested set.
            if access.has_credentials(journey) and re.search(r"\blog ?in\b|\bcontinue\b|\bproceed\b", t):
                return await self._login_with_credentials(journey, ctx, warnings)
            if re.search(r"\bskip gated\b|\bcontinue\b|\bproceed\b", t):
                journeys.set_guardrails(self.db, journey,
                                        **journeys.default_guardrails(journey.target, journey.environment))
                journeys.advance(self.db, journey, JourneyStage.GUARDRAILS)
                return self._guardrails_reply(journey, disc, warnings)
            if re.search(r"\blog ?in for me\b", t):
                return await self._login_handoff(journey, ctx, warnings)
            if re.search(r"\badd (test )?credentials\b", t):
                return ChatReply(
                    text="Add credentials in the Access panel. They go straight into the vault, "
                         "never through the chat. I'll then sign in and test the pages behind the "
                         "login. Or say 'log in for me' to sign in yourself in a real browser, or "
                         "'skip gated pages' to proceed without them.",
                    blocks=[{"type": "access_card", "target": journey.target,
                             "auth_count": (disc.get("skipped", {}) or {}).get("auth", 0),
                             "gated": [], "want_credentials": True, **journeys.describe(journey)}],
                    path="onramp", warnings=warnings,
                    suggestions=[{"label": "Log in for me", "text": "log in for me"},
                                 {"label": "Skip gated pages", "text": "skip gated pages"}],
                )

        # Guardrails accepted → reveal the plan.
        if journey.stage == JourneyStage.GUARDRAILS and re.search(r"\bcontinue\b|\blooks good\b|\bproceed\b|\baccept\b", t):
            plan = journey.plan or wt.pending_plan(session)
            if plan:
                wt.stash_plan(session, plan)  # make it the pending plan again for "approve"
                journeys.advance(self.db, journey, JourneyStage.PLAN)
                return self._website_plan_reply(plan, journey, warnings)

        # Toggle a test type in the plan → a new plan version.
        toggle = re.search(r"\b(enable|add|turn on|disable|drop|remove|turn off) ([a-z0-9/_ -]+)", t)
        if toggle and (journey.plan or {}).get("typed"):
            from ..services import test_plan as tp
            on = toggle.group(1) in ("enable", "add", "turn on")
            key = _match_type_key(toggle.group(2), journey.plan["typed"]["types"])
            if key:
                new_typed = tp.toggle(journey.plan["typed"], key, on)
                journey.plan = {**journey.plan, "typed": new_typed}
                journeys.advance(self.db, journey, journey.stage, ran=False,
                                 plan=journey.plan, plan_version=new_typed["version"])
                return self._website_plan_reply(journey.plan, journey, warnings)

        # Build the plan into durable, filed tests with provenance.
        if re.search(r"\bsave (the )?plan as tests\b|\bbuild the tests\b|\bbuild it\b", t) and (journey.plan or {}).get("typed"):
            from ..models import JourneyStage, Project
            from ..services.build import build_tests_from_plan
            project = self.db.get(Project, journey.project_id)
            result = build_tests_from_plan(self.db, project, journey)
            journeys.advance(self.db, journey, JourneyStage.BUILD, test_ids=result["run_keys"])
            suites = len(result["suites"])
            return ChatReply(
                text=(f"Built {result['filed']} test(s) across {suites} suite(s) from the plan for "
                      f"{journey.target}, one per page / form / endpoint, filed for review with full "
                      "provenance. Smoke-check them first, or open them in Tests."),
                blocks=[{"type": "build_result", "target": journey.target,
                         "filed": result["filed"], "tests": result["tests"],
                         "suites": result["suites"], "smoke_count": len(result["smoke_keys"]),
                         **journeys.describe(journey)}],
                path="onramp", warnings=warnings,
                suggestions=[
                    {"label": "Smoke-check first", "text": "smoke it"},
                    {"label": "Approve & run all", "text": "approve"},
                    {"label": "What's next", "text": "what's next"},
                ],
            )

        # Smoke: a ≤3-min front-door check that reuses the built smoke subset,
        # gating the full run. Available once tests are built.
        if re.search(r"\bsmoke\b", t) and (journey.test_ids or journey.stage == JourneyStage.BUILD):
            return await self._run_smoke(journey, ctx, warnings)

        # Run: approve the built Golden Path suite and run it in full. "approve",
        # "run it", "run the full suite" mean this; from a later stage it re-runs
        # (e.g. after adding a credential to cover the auth-gated units).
        if (journey.stage in (JourneyStage.BUILD, JourneyStage.SMOKE, JourneyStage.RUN,
                              JourneyStage.TRIAGE, JourneyStage.READINESS, JourneyStage.REPORT)
                and re.search(r"\bapprove\b|\b(re-?)?run (it|all|everything|the (full )?suite|the tests)\b", t)):
            return await self._run_golden_path(journey, ctx, warnings)

        # Keep-green: schedule the suite + a CI workflow + webhook routing.
        if re.search(r"\bkeep it green\b|\bkeep.green\b|\badd to ci\b|\bgithub action\b|\bschedule\b", t):
            return await self._keep_green(journey, t, ctx, warnings)

        # Exploratory: a guardrail-bounded, timeboxed poke at an area, as a role.
        if re.search(r"\bpoke\b|\bexplore .{0,40}\b(as|for)\b", t):
            return await self._explore(journey, t, ctx, warnings)
        # Promote an exploratory finding into a proposed test.
        if re.search(r"\bmake (this|it|a) (a )?test\b", t):
            return await self._make_test(journey, t, ctx, warnings)

        # Share: publish the release report (v2) as shareable artifacts.
        if re.search(r"\bshare( the| this)? report\b|\bshare it\b|\bpublish( the)? report\b", t):
            return await self._share(journey, t, ctx, warnings)

        # Readiness: the Go/No-Go gate. "sign off" is refused: a human must own it.
        if re.search(r"\breadiness\b|\bgo.?no.?go\b|\bready to (ship|release)\b|\bsign.?off\b", t):
            return await self._readiness(journey, t, ctx, warnings)

        # Triage: group the last run's failures and give each a disposition.
        if re.search(r"\btriage\b", t):
            return await self._triage(journey, t, ctx, warnings)
        # Rerun a slice as a child run → regression delta ("rerun failed", "rerun
        # the a11y suite", "rerun what touched /checkout"). Checked before the
        # disposition branch so these don't read as a triage rerun.
        if re.search(r"\brerun\b", t) and journey.run_id:
            spec = self._parse_rerun_spec(t)
            if spec is not None:
                return await self._rerun(journey, spec, ctx, warnings)
        # Disposition actions on a triaged failure group.
        if re.search(r"\b(rerun|quarantine|heal|mark expected|file (a )?bug)\b", t) and journey.run_id:
            return await self._disposition(journey, t, ctx, warnings)

        # Test the full discovered set, not just the default cap.
        if re.search(r"\btest all pages\b", t):
            return await self._expand_plan_to_all(journey, session, warnings)

        # A "what's next" / "where are we" query, or a transition that doesn't
        # apply at this stage: show the journey card.
        return self._journey_card(journey)

    async def _expand_plan_to_all(self, journey, session, warnings) -> ChatReply:
        """Re-discover with a higher cap and re-plan against every page found."""
        import asyncio

        from ..services import journeys
        from ..services import website_test as wt

        discovery = await asyncio.to_thread(wt.discover_pages, journey.target, wt.CRAWL_MAX_PAGES)
        if not discovery.get("ok"):
            return ChatReply(text="I couldn't re-crawl the site to expand the plan.", warnings=warnings)
        plan = wt.build_plan(discovery)
        plan = await wt.enrich_plan_with_model(plan, self.provider)
        wt.stash_plan(session, plan)
        journeys.advance(self.db, journey, journey.stage, ran=False,
                         discovery=discovery, plan=plan)
        return self._website_plan_reply(plan, journey, warnings)

    async def _run_smoke(self, journey, ctx, warnings) -> ChatReply:
        """Run the ≤3-min smoke subset and gate the full run on it."""
        from ..models import JourneyStage, Project
        from ..services import journeys
        from ..services.smoke import run_smoke

        project = self.db.get(Project, journey.project_id)
        result = await run_smoke(self.db, project=project, journey=journey,
                                 triggered_by=ctx.actor_id)
        if result.get("run_id"):
            journeys.advance(self.db, journey, JourneyStage.SMOKE, run_id=result["run_id"])
        clean = result.get("ok")
        suggestions = ([{"label": "Run the full suite", "text": "run the full suite"}]
                       if clean else
                       [{"label": "Log in for me", "text": "log in for me"},
                        {"label": "Run anyway", "text": "run the full suite"}])
        block = {"type": "smoke_result",
                 **{k: result.get(k) for k in ("ok", "status", "target", "total", "passed",
                                               "failed", "blockers", "run_id", "run_number",
                                               "timed_out", "message")},
                 **journeys.describe(journey)}
        return ChatReply(text=result["message"], blocks=[block], path="onramp",
                         warnings=warnings, suggestions=suggestions)

    async def _login_with_credentials(self, journey, ctx, warnings) -> ChatReply:
        """Sign in with the stored credentials, then promote the gated pages into
        the tested set and hand back to guardrails with the enlarged plan."""
        from ..models import JourneyStage, Project
        from ..services import access, journeys

        project = self.db.get(Project, journey.project_id)
        result = await access.perform_login(self.db, project=project, journey=journey,
                                             triggered_by=ctx.actor_id)
        disc = journey.discovery or {}
        if not result.get("ok"):
            return ChatReply(
                text=f"That sign-in didn't take ({result.get('error') or 'login failed'}). "
                     "Re-check the credentials in the Access panel, or say 'skip gated pages'.",
                blocks=[{"type": "access_card", "target": journey.target,
                         "auth_count": (disc.get("skipped", {}) or {}).get("auth", 0),
                         "gated": [], "want_credentials": True, **journeys.describe(journey)}],
                path="onramp", warnings=warnings,
                suggestions=[{"label": "Skip gated pages", "text": "skip gated pages"}])
        promoted = access.promote_gated(self.db, journey)
        journeys.set_guardrails(self.db, journey,
                                **journeys.default_guardrails(journey.target, journey.environment))
        journeys.advance(self.db, journey, JourneyStage.GUARDRAILS)
        note = (f" {len(promoted)} page(s) that were behind the login are now in the plan."
                if promoted else "")
        reply = self._guardrails_reply(journey, journey.discovery or {}, warnings)
        reply.text = f"Signed in ({result['kind']}) and the session is sealed.{note} " + reply.text
        return reply

    async def _login_handoff(self, journey, ctx, warnings) -> ChatReply:
        """Log-in-for-me: open a real browser parked at the login page for the user
        to sign into; the session is sealed on resume (headed pause-and-attach)."""
        from ..services import access

        disc = journey.discovery or {}
        login_url = next((f.get("url") for f in (disc.get("findings") or [])
                          if f.get("kind") == "auth_gated" and f.get("url")), journey.target)
        result = await access.begin_login_handoff(self.db, project_id=journey.project_id,
                                                  journey=journey, login_url=login_url,
                                                  triggered_by=ctx.actor_id)
        from ..services import journeys
        return ChatReply(
            text=f"A browser is opening at {login_url}. Sign in there, then click Resume and I'll "
                 "seal the session and test the pages behind the login. (Headed sign-in runs on a "
                 "machine with a display; on this headless server it parks for the resume signal.)",
            blocks=[{"type": "access_handoff", "target": journey.target, "login_url": login_url,
                     "run_id": result.get("run_id"), "run_number": result.get("run_number"),
                     "handoff_key": result.get("handoff_key"), **journeys.describe(journey)}],
            path="onramp", warnings=warnings,
            suggestions=[{"label": "Skip gated pages", "text": "skip gated pages"}])

    async def _run_golden_path(self, journey, ctx, warnings) -> ChatReply:
        """Approve the built suite and run it; return the Run board."""
        from ..models import JourneyStage, Project
        from ..services import journeys
        from ..services.golden_run import run_full

        project = self.db.get(Project, journey.project_id)
        board = await run_full(self.db, project=project, journey=journey,
                               triggered_by=ctx.actor_id)
        if not board.get("run_id"):
            return ChatReply(text=board.get("message", "Nothing to run. Build the tests first."),
                             path="onramp", warnings=warnings,
                             suggestions=[{"label": "Build the tests", "text": "build the tests"}])
        journeys.advance(self.db, journey, JourneyStage.RUN, run_id=board["run_id"])
        tot = board["totals"]
        if board["done"]:
            text = (f"Ran {tot['total']} test(s) on {journey.target}: {tot['passed']} passed, "
                    f"{tot['failed']} failed (no model, $0). "
                    + ("Clean. On to readiness." if not board["has_failures"] else "Triage the failures next."))
        else:
            text = (f"Running {tot['total']} test(s) on {journey.target}: {tot['passed']} passed, "
                    f"{tot['failed']} failed so far. Watch it live in Runs.")
        suggestions = ([{"label": "Triage failures", "text": "triage"}] if board["has_failures"]
                       else [{"label": "What's next", "text": "what's next"}])
        return ChatReply(text=text, blocks=[{**board, "type": "run_board",
                                             **journeys.describe(journey)}],
                         path="onramp", warnings=warnings, suggestions=suggestions)

    async def _triage(self, journey, t, ctx, warnings) -> ChatReply:
        """Group the current run's failures into a board of dispositions."""
        from ..models import JourneyStage, Run
        from ..services import journeys
        from ..services.triage import triage_board

        run = self.db.get(Run, journey.run_id) if journey.run_id else None
        if run is None:
            return ChatReply(text="No run to triage yet. Run the suite first.",
                             path="onramp", warnings=warnings,
                             suggestions=[{"label": "Run the full suite", "text": "run the full suite"}])
        board = triage_board(self.db, run, journey)
        journeys.advance(self.db, journey, JourneyStage.TRIAGE, run_id=run.id)
        if board["group_count"] == 0:
            text = f"Nothing to triage. Every test on {journey.target} passed."
            suggestions = [{"label": "What's next", "text": "what's next"}]
        else:
            text = (f"{board['group_count']} failure group(s) on {journey.target}; "
                    f"{board['open_count']} still need a disposition.")
            suggestions = [{"label": "What's next", "text": "what's next"}]
        return ChatReply(text=text, blocks=[{**board, "type": "triage_board",
                                            **journeys.describe(journey)}],
                         path="onramp", warnings=warnings, suggestions=suggestions)

    async def _share(self, journey, t, ctx, warnings) -> ChatReply:
        """Publish the release report (v2) in every format through the storage
        interface, and return the Share card. No format is a dead end; the team
        integrations are offered but gated behind an approval."""
        import re

        from ..models import JourneyStage, Project, Run
        from ..services import journeys, share

        run = self.db.get(Run, journey.run_id) if journey.run_id else None
        if run is None:
            return ChatReply(text="Nothing to share yet. Run the suite first.",
                             path="onramp", warnings=warnings,
                             suggestions=[{"label": "Run the full suite", "text": "run the full suite"}])
        project = self.db.get(Project, journey.project_id)
        stakeholder = bool(re.search(r"\bstakeholder\b|\bexec(utive)?\b", t))
        result = await share.publish(self.db, project, journey, run, stakeholder=stakeholder,
                                     actor=ctx.actor_id)
        journeys.advance(self.db, journey, JourneyStage.REPORT, run_id=run.id)
        return ChatReply(
            text=(f"Published the release report for {journey.target}"
                  + (" (stakeholder view: names, links and screenshots redacted)" if stakeholder else "")
                  + f". Public link expires in 30 days. Formats: {', '.join(result['formats'])}."),
            blocks=[{"type": "share_card", "target": journey.target,
                     "share_url": result["share_url"], "urls": result["urls"],
                     "formats": result["formats"], "public": result["public"],
                     "pdf_status": result.get("pdf_status"),
                     "stakeholder": stakeholder, "run_number": run.number,
                     **journeys.describe(journey)}],
            path="onramp", warnings=warnings,
            suggestions=[{"label": "Keep it green", "text": "keep it green"},
                         {"label": "What's next", "text": "what's next"}])

    async def _explore(self, journey, t, ctx, warnings) -> ChatReply:
        """Run a guardrail-bounded, timeboxed exploration and return an anomaly card."""
        import re

        from ..models import JourneyStage, Project
        from ..services import exploration, journeys

        area_m = re.search(r"\b(?:poke at|explore)\s+([\w/\-.]+)", t)
        role_m = re.search(r"\bas (?:an?\s+)?([\w \-]+?)(?:\s+for\b|\s+on\b|$)", t)
        min_m = re.search(r"\bfor\s+(\d+)\s*(?:min|minute)", t)
        area = (area_m.group(1) if area_m else "/").strip()
        role = (role_m.group(1).strip() if role_m else "a user")
        minutes = int(min_m.group(1)) if min_m else 10

        project = self.db.get(Project, journey.project_id)
        result = await exploration.explore_now(
            self.db, project=project, journey=journey, area=area, role=role,
            minutes=minutes, actor=ctx.actor_id)
        journeys.advance(self.db, journey, JourneyStage.EXPLORE, ran=False)
        session = result["session"]
        findings = result["findings"]
        n = len(findings)
        high = sum(1 for f in findings if f["severity"] == "high")
        text = (f"Explored {area} as {role} for up to {minutes} min: "
                + (f"{n} anomaly(ies)" + (f", {high} high" if high else "") + "."
                   if n else "nothing worth reporting.")
                + (" (still running, watch it live.)" if result["timed_out"] else ""))
        return ChatReply(
            text=text,
            blocks=[{"type": "anomaly_card", "session_id": session.id, "area": area,
                     "role": role, "minutes": minutes, "summary": session.summary,
                     "findings": findings, **journeys.describe(journey)}],
            path="onramp", warnings=warnings,
            suggestions=[{"label": "What's next", "text": "what's next"}])

    async def _keep_green(self, journey, t, ctx, warnings) -> ChatReply:
        """Turn the run into an ongoing guarantee: a schedule, a CI workflow, and
        webhook routing."""
        import re

        from ..models import JourneyStage, Project
        from ..services import journeys, keepgreen

        project = self.db.get(Project, journey.project_id)
        m = re.search(r"\bcron\s+([\d*/,\- ]+)$", t)
        cron = m.group(1).strip() if m else keepgreen.DEFAULT_CRON
        result = keepgreen.keep_green(self.db, project, journey, cron=cron, actor=ctx.actor_id)
        journeys.advance(self.db, journey, JourneyStage.KEEP_GREEN, run_id=journey.run_id)
        sched = result["schedule"]
        routing = (f"{result['webhooks_active']} webhook(s) active: run.finished / run.failed will fire"
                   if result["webhooks_active"] else
                   "No webhooks yet. Add one in Settings to route results to Slack.")
        return ChatReply(
            text=(f"Keeping {journey.target} green: scheduled **{sched['human_cron']}** "
                  f"({sched['tests']} test(s)), a GitHub Actions workflow is ready to copy, and "
                  f"{routing.lower()}").replace("**", ""),
            blocks=[{"type": "keep_green_card", "target": journey.target,
                     "schedule": sched, "github_action": result["github_action"],
                     "webhooks_active": result["webhooks_active"], "routing": routing,
                     **journeys.describe(journey)}],
            path="onramp", warnings=warnings,
            suggestions=[{"label": "Add a webhook", "text": "open settings"},
                         {"label": "What's next", "text": "what's next"}])

    async def _make_test(self, journey, t, ctx, warnings) -> ChatReply:
        """Promote an exploratory finding into a proposed test."""
        import re

        from ..services import exploration
        m = re.search(r"\bfor ([0-9a-f]{6,})\b", t)
        if not m:
            return ChatReply(text="Say 'make a test for <finding-id>' (the id is on the anomaly "
                                  "card).", path="onramp", warnings=warnings)
        result = exploration.promote_finding(self.db, project_id=journey.project_id,
                                             finding_id=m.group(1), journey=journey,
                                             actor=ctx.actor_id)
        if not result.get("ok"):
            return ChatReply(text=f"Couldn't promote that: {result.get('error')}",
                             path="onramp", warnings=warnings)
        if result.get("already"):
            return ChatReply(text="That finding is already a test.", path="onramp", warnings=warnings)
        return ChatReply(
            text=f"Filed **{result['key']}** from the {result['kind']} finding: proposed, "
                 "awaiting your review in Tests.".replace("**", ""),
            path="onramp", warnings=warnings,
            suggestions=[{"label": "Open in Tests", "text": "show the tests"}])

    async def _readiness(self, journey, t, ctx, warnings) -> ChatReply:
        """The Go/No-Go gate. Show the criteria; a request to *sign* is declined:
        an AI principal can never satisfy the gate, a human signs it."""
        import re

        from ..models import Run
        from ..services import journeys, readiness

        run = self.db.get(Run, journey.run_id) if journey.run_id else None
        if run is None:
            return ChatReply(text="No run to assess yet. Run the suite first.",
                             path="onramp", warnings=warnings,
                             suggestions=[{"label": "Run the full suite", "text": "run the full suite"}])
        result = readiness.evaluate(self.db, journey, run)
        block = {"type": "readiness_card", **result, **journeys.describe(journey)}
        wants_sign = bool(re.search(r"\bsign.?off\b", t))
        if wants_sign and not result["signoff"]:
            # The rule, embodied: the agent refuses to sign, and says who can.
            return ChatReply(
                text=(f"I can't sign this off. A release-readiness decision is a human's to own, "
                      f"never the agent's. {journey.target} is **{result['verdict'].replace('_', '-').upper()}**"
                      f"{' (all criteria pass)' if result['verdict'] == 'go' else ' (' + str(len(result['failed_criteria'])) + ' criterion(s) failing)'}. "
                      "Use **Sign off** on the card to record your decision.").replace("**", ""),
                blocks=[block], path="onramp", warnings=warnings,
                suggestions=[{"label": "Open the report", "text": "show the run report"}])
        verdict = result["verdict"].replace("_", "-").upper()
        signed = result["signoff"]
        text = (f"Readiness for {journey.target}: **{verdict}**. "
                + ("Signed off by " + signed["signer_name"] + "." if signed
                   else ("All criteria pass. Ready for a human sign-off."
                         if result["verdict"] == "go"
                         else f"{len(result['failed_criteria'])} criterion(s) block release."))).replace("**", "")
        return ChatReply(text=text, blocks=[block], path="onramp", warnings=warnings,
                         suggestions=[{"label": "Open the report", "text": "show the run report"},
                                      {"label": "Triage failures", "text": "triage"}])

    async def _disposition(self, journey, t, ctx, warnings) -> ChatReply:
        """Apply a disposition (rerun / quarantine / mark-expected / …) to a group."""
        import re

        from ..models import Run
        from ..services import journeys
        from ..services.triage import DISPOSITIONS, set_disposition, triage_board

        run = self.db.get(Run, journey.run_id) if journey.run_id else None
        if run is None:
            return ChatReply(text="No triaged run to act on.", path="onramp", warnings=warnings)
        board = triage_board(self.db, run, journey)
        # Map the verb to a disposition; target the group named after "for"/quotes,
        # else the first still-open failure group.
        verb = ("mark_expected" if re.search(r"\bmark expected\b", t)
                else "file_bug" if re.search(r"\bfile (a )?bug\b", t)
                else next((d for d in ("rerun", "quarantine", "heal") if d in t), None))
        if verb not in DISPOSITIONS:
            return ChatReply(text="I didn't catch which disposition. Try 'quarantine', 'rerun', "
                                  "'heal', 'mark expected', or 'file a bug'.",
                             path="onramp", warnings=warnings)
        m = re.search(r"\bfor (.+)$", t)
        want = (m.group(1).strip().strip("'\"") if m else "")
        groups = board["groups"]
        target = next((g for g in groups if want and want.lower() in g["signature"].lower()),
                      None) or next((g for g in groups if not g["disposition"]), None)
        if target is None:
            return ChatReply(text="No open failure group to disposition.", path="onramp",
                             warnings=warnings)

        # Rerun is special: it re-runs the group ×3 in isolation and lets the
        # results decide: a pass on retry flips the class to flaky automatically.
        if verb == "rerun":
            from ..models import Project
            from ..services import rerun as rerun_svc
            project = self.db.get(Project, journey.project_id)
            res = await rerun_svc.rerun_x3(self.db, project=project, journey=journey,
                                           signature=target["signature"], actor=ctx.actor_id)
            if not res.get("ok"):
                return ChatReply(text=res.get("message", "Couldn't rerun that group."),
                                 path="onramp", warnings=warnings)
            new_board = res["board"]
            runs = ", ".join(f"#{n}" for n in res["child_runs"])
            if res["flipped"]:
                text = (f"Reran ×3 ({runs}): {len(res['flipped'])} test(s) passed on retry → "
                        f"reclassified **flaky**, quarantine suggested. "
                        f"{new_board['open_count']} group(s) still open.").replace("**", "")
            else:
                text = (f"Reran ×3 ({runs}): failed every time, so confidence in the failure is high. "
                        f"{new_board['open_count']} group(s) still open.")
            return ChatReply(text=text, blocks=[{**new_board, "type": "triage_board",
                                                **journeys.describe(journey)}],
                             path="onramp", warnings=warnings,
                             suggestions=([{"label": "Quarantine it",
                                            "text": f"quarantine for {target['signature']}"}]
                                          if res["suggest_quarantine"] else
                                          [{"label": "What's next", "text": "what's next"}]))

        result = set_disposition(self.db, run, target["signature"], verb, actor=ctx.actor_id)
        new_board = result["board"]
        note = {"quarantine": "quarantined (7-day expiry)", "mark_expected": "marked expected",
                "heal": "sent to heal review", "file_bug": "bug filing filed for approval"}.get(verb, verb)
        text = (f"'{target['signature'][:60]}' → {note}. "
                f"{new_board['open_count']} group(s) still open.")
        return ChatReply(text=text, blocks=[{**new_board, "type": "triage_board",
                                            **journeys.describe(journey)}],
                         path="onramp", warnings=warnings,
                         suggestions=[{"label": "What's next", "text": "what's next"}])

    def _parse_rerun_spec(self, t: str) -> dict | None:
        """Map a rerun phrase to a selection spec, or None if it's a triage
        disposition ('rerun for <signature>') that belongs elsewhere."""
        import re
        if re.search(r"\brerun\b.*\bfor\b", t):
            return None  # 'rerun for <sig>' → a triage disposition
        if re.search(r"\brerun\b.*\bfail", t):
            return {"mode": "failed", "command": t}
        m = re.search(r"\brerun\b.*\b(?:suite\s+([a-z0-9_]+)|([a-z0-9_]+)\s+suite)\b", t)
        if m:
            return {"mode": "suite", "type": (m.group(1) or m.group(2)), "command": t}
        m = re.search(r"\brerun\b.*?(?:touch(?:ed|ing)?\s+)?(/[\w\-/]+)", t)
        if m:
            return {"mode": "path", "path": m.group(1), "command": t}
        if re.search(r"\brerun\b.*\b(all|everything|the (whole |full )?suite)\b", t):
            return {"mode": "all", "command": t}
        return None

    async def _rerun(self, journey, spec, ctx, warnings) -> ChatReply:
        """Run a slice as a child run and return the regression delta card."""
        from ..models import JourneyStage, Project
        from ..services import journeys, rerun

        project = self.db.get(Project, journey.project_id)
        result = await rerun.rerun(self.db, project=project, journey=journey, spec=spec,
                                   triggered_by=ctx.actor_id)
        if not result.get("ok"):
            return ChatReply(text=result.get("message", "Nothing to rerun."),
                             path="onramp", warnings=warnings,
                             suggestions=[{"label": "Triage failures", "text": "triage"}])
        journeys.advance(self.db, journey, JourneyStage.RUN, ran=False, run_id=result["run_id"])
        delta = result["delta"]
        return ChatReply(
            text=(f"Reran {result['count']} test(s) as run #{result['run_number']}. "
                  + delta["what_changed"]),
            blocks=[{"type": "delta_card", **delta, "count": result["count"],
                     **journeys.describe(journey)}],
            path="onramp", warnings=warnings,
            suggestions=[{"label": "Triage failures", "text": "triage"},
                         {"label": "Show the report", "text": "share the report"}])

    def _report_summary(self, rtype: str, last_run) -> tuple[str, str]:
        """A one-line summary for a report card, computed from the builder."""
        from ..models import Project

        project = self.db.get(Project, last_run.project_id) if last_run else \
            self.db.execute(select(Project).limit(1)).scalar_one_or_none()
        try:
            if rtype == "run" and last_run is not None:
                from ..reports.runs import build_run_report
                r = build_run_report(self.db, project, last_run)
                s = r["summary"]
                return (f"Run #{last_run.number}: {r['readiness']['verdict'].replace('_', ' ')}, "
                        f"{s['passed']}/{s['total']} passed, cost ${r['cost']['cost_estimate_usd']}.",
                        f"Run #{last_run.number} report")
            if rtype == "coverage":
                from ..reports.coverage import build_coverage_report
                r = build_coverage_report(self.db, project)
                return (f"{r['summary']['coverage_pct']}% covered, "
                        f"{r['summary']['automation_pct']}% automated.", "Coverage report")
            if rtype == "flaky":
                from ..reports.flaky import build_flaky_report
                r = build_flaky_report(self.db, project)
                return (f"{r['summary']['flaky_count']} flaky test(s).", "Flaky report")
            if rtype == "traceability":
                from ..reports.traceability import build_traceability_report
                r = build_traceability_report(self.db, project)
                return (f"{r['summary']['covered']}/{r['summary']['total']} requirements covered.",
                        "Traceability report")
        except Exception:  # noqa: BLE001 (a summary is a nicety, never a failure)
            pass
        return ("", f"{rtype.title()} report")

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
                    f"**{target.test_key}: {target.title}**\n\n"
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
                       "Even without a model I can scaffold cases directly from the requirement "
                       "structure. Open Requirements → Generate to review the deterministic "
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
                text=triage.get("headline") or f"Run #{run['number']}: {run['status']}.",
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
    "Deterministic locator healing, with a cached zero-token re-run",
    "Full reporting, dashboards and audit trail",
]


#: Plain-English names for the plan's test types, so "enable accessibility" or
#: "drop visual" find the right row.
_TYPE_ALIASES = {
    "accessibility": "a11y", "a11y": "a11y", "axe": "a11y",
    "performance": "perf", "perf": "perf", "cwv": "perf", "web vitals": "perf",
    "visual": "visual", "snapshot": "visual", "screenshot": "visual",
    "responsive": "responsive", "viewport": "responsive",
    "cross-browser": "cross_browser", "cross browser": "cross_browser", "browser": "cross_browser",
    "security": "security", "seo": "seo",
    "resilience": "resilience", "chaos": "resilience",
    "api": "api", "contract": "api",
    "forms": "forms", "form": "forms",
    "links": "links", "link": "links",
    "functional": "functional", "e2e": "functional",
    "data-driven": "data_driven", "data driven": "data_driven",
    "exploratory": "exploratory", "charter": "exploratory",
    "manual": "manual",
}


def _match_type_key(text: str, types: list[dict]) -> str | None:
    """Map free text ("accessibility", "visual snapshots") to a plan type key."""
    t = text.strip().lower()
    keys = {r["key"] for r in types}
    for phrase, key in _TYPE_ALIASES.items():
        if phrase in t and key in keys:
            return key
    return None


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
