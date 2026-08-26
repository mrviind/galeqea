"""Confirm-then-execute for agent plans.

``propose_plan`` (the tool) lays out a sequence of tool calls and returns it for
the user to see. This module is the other half: it remembers that plan on the
conversation, recognises the user's reply to it — proceed, stop, or amend — and,
on confirmation, executes the plan's steps one at a time.

Why a deterministic gate rather than leaving it to the model:

* **The plan the user approved is the plan that runs.** If confirmation just told
  the model "go ahead", the model might re-plan, reorder, or add a step nobody
  agreed to. Executing the *stored* plan makes "yes" mean exactly what was shown.
* **It works with no model.** Recognising "proceed" and dispatching stored tool
  calls needs no LLM, so a confirmed plan runs in No-AI mode.
* **The gate is auditable.** The plan is a row on the session; approving it, and
  each step's result, are observable facts rather than something buried in a
  model's reasoning.

Every write step still hits the approval gate individually when it runs.
Confirming the plan is consent to *attempt* the sequence, never a bypass of the
per-action review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Where the pending plan lives on ``ChatSession.context``.
PLAN_KEY = "pending_plan"

_PROCEED = re.compile(
    r"^\s*(proceed|go ahead|go for it|do it|yes(?:\s+please)?|execute|run it|"
    r"confirm(?:ed)?|approved?|continue|carry on|let'?s go|ship it|sounds good)\b",
    re.IGNORECASE,
)
_STOP = re.compile(
    r"^\s*(stop|cancel|abort|no(?:pe)?|don'?t|hold on|wait|never ?mind|scrap|discard|forget it)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class GateOutcome:
    """What the gate decided for one incoming message."""

    kind: str                       # proceed | stop | amend | none
    plan: dict | None = None
    message: str = ""
    executed: list[dict] = field(default_factory=list)


def stash_plan(session, plan: dict) -> None:
    """Remember a freshly proposed plan on the conversation.

    Stores only what confirmation needs — the goal and the ordered steps — not
    the whole tool result. A plan the user never answers is simply overwritten
    by the next one, so a stale plan cannot be confirmed by accident later.
    """
    context = dict(session.context or {})
    context[PLAN_KEY] = {
        "goal": plan.get("goal", ""),
        "steps": [
            {"tool": s.get("tool", ""), "arguments": s.get("arguments") or {},
             "why": s.get("why", ""), "effect": s.get("effect", "")}
            for s in plan.get("steps", [])
        ],
        "writes_state": bool(plan.get("writes_state")),
    }
    session.context = context


def pending_plan(session) -> dict | None:
    return (session.context or {}).get(PLAN_KEY)


def clear_plan(session) -> None:
    if session.context and PLAN_KEY in session.context:
        context = dict(session.context)
        context.pop(PLAN_KEY, None)
        session.context = context


def classify_reply(text: str, has_pending: bool) -> str:
    """proceed | stop | amend | none.

    ``amend`` is the important middle case: while a plan is pending, a message
    that is neither a clear yes nor a clear no is a *revision* — the user wants
    something different — so the pending plan is dropped and the message handled
    fresh, rather than being mistaken for confirmation.
    """
    if not has_pending:
        return "none"
    if _PROCEED.match(text):
        return "proceed"
    if _STOP.match(text):
        return "stop"
    return "amend"


async def execute_plan(plan: dict, registry, ctx, *, emit=None) -> list[dict]:
    """Run the stored plan's steps in order.

    Stops early on the first hard failure or the first step that files an
    approval: a plan whose step 2 is now waiting on a human must not barrel on to
    step 3 as though 2 had succeeded. Each step's result is returned so the caller
    can report the whole attempt.
    """
    executed: list[dict] = []
    for index, step in enumerate(plan.get("steps", []), 1):
        tool_name = step.get("tool", "")
        if emit is not None:
            await emit(index, tool_name)
        result = await registry.invoke(tool_name, step.get("arguments") or {}, ctx)
        record = {"index": index, "tool": tool_name, "result": result,
                  "ok": bool(result.get("ok", True))}
        executed.append(record)

        if result.get("status") == "awaiting_approval":
            record["halted"] = "awaiting_approval"
            break
        if not record["ok"]:
            record["halted"] = "error"
            break
    return executed


def summarise_execution(plan: dict, executed: list[dict]) -> str:
    """A human-readable account of what the confirmed plan actually did."""
    total = len(plan.get("steps", []))
    done = len(executed)
    last = executed[-1] if executed else None

    if last and last.get("halted") == "awaiting_approval":
        return (
            f"Ran {done} of {total} planned steps. Step {done} ({last['tool']}) needs your "
            f"approval before it takes effect — it is queued for review. The remaining "
            f"{total - done} step(s) are paused until you decide."
        )
    if last and last.get("halted") == "error":
        err = last["result"].get("error", "an error")
        return (
            f"Ran {done} of {total} planned steps. Step {done} ({last['tool']}) failed: {err}. "
            f"I stopped rather than continue on a failed step — tell me how to proceed."
        )
    ok = sum(1 for e in executed if e["ok"])
    return f"Completed the plan: {ok} of {total} steps ran successfully."
