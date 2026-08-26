"""Confirm-then-execute plan gate: the plan the user approved is the plan that runs."""

from __future__ import annotations

import asyncio

import pytest

from galeqea.ai.plan_gate import (
    PLAN_KEY, classify_reply, clear_plan, execute_plan, pending_plan,
    stash_plan, summarise_execution,
)
from galeqea.ai.tools import RiskTier, ToolContext, ToolRegistry


class _Session:
    """A stand-in for ChatSession — only .context is touched by the gate."""
    def __init__(self):
        self.context = {}
        self.project_id = "p"
        self.id = "s"


# --------------------------------------------------------------------------- #
# Reply classification
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", ["proceed", "go ahead", "yes please", "do it", "run it", "confirmed"])
def test_confirmations_are_recognised(text):
    assert classify_reply(text, has_pending=True) == "proceed"


@pytest.mark.parametrize("text", ["stop", "cancel", "no", "never mind", "hold on"])
def test_rejections_are_recognised(text):
    assert classify_reply(text, has_pending=True) == "stop"


def test_an_unrelated_message_while_a_plan_pends_is_an_amendment_not_a_yes():
    """The dangerous case: a vague message must not be read as confirmation."""
    assert classify_reply("actually, do the login flow instead", has_pending=True) == "amend"


def test_nothing_is_intercepted_when_no_plan_is_pending():
    assert classify_reply("proceed", has_pending=False) == "none"


# --------------------------------------------------------------------------- #
# Stash / clear
# --------------------------------------------------------------------------- #
def test_a_plan_is_stashed_with_only_what_confirmation_needs():
    session = _Session()
    stash_plan(session, {"goal": "g", "writes_state": True, "steps": [
        {"tool": "query_requirements", "arguments": {"feature": "checkout"}, "why": "read", "effect": "read-only", "extra": "dropped"},
    ]})
    plan = pending_plan(session)
    assert plan["goal"] == "g"
    assert plan["steps"][0]["tool"] == "query_requirements"
    assert plan["steps"][0]["arguments"] == {"feature": "checkout"}
    assert "extra" not in plan["steps"][0]


def test_a_new_plan_overwrites_an_unanswered_one():
    session = _Session()
    stash_plan(session, {"goal": "first", "steps": []})
    stash_plan(session, {"goal": "second", "steps": []})
    assert pending_plan(session)["goal"] == "second"


def test_clearing_removes_the_plan():
    session = _Session()
    stash_plan(session, {"goal": "g", "steps": []})
    clear_plan(session)
    assert pending_plan(session) is None
    assert PLAN_KEY not in session.context


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
def _registry():
    reg = ToolRegistry()

    @reg.register("read_a", description="Reads. Present so a plan has a read step to run in tests.",
                  parameters={"properties": {}})
    def read_a(args, ctx):
        return {"ok": True, "value": "a"}

    @reg.register("read_b", description="Reads. Present so a plan has a second read step to run.",
                  parameters={"properties": {}})
    def read_b(args, ctx):
        return {"ok": True, "value": "b"}

    @reg.register("fails", description="Fails. Present to prove the plan halts on a failed step.",
                  parameters={"properties": {}})
    def fails(args, ctx):
        return {"ok": False, "error": "nope"}

    @reg.register("gated", description="Gated. Present to prove the plan halts on an approval.",
                  parameters={"properties": {}}, read_only=False,
                  approval_action="test.create", risk=RiskTier.HIGH)
    def gated(args, ctx):
        raise AssertionError("must not execute")

    return reg


def _ctx(db, project):
    return ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")


def test_the_stored_plan_runs_in_order(db, project):
    plan = {"goal": "g", "steps": [{"tool": "read_a", "arguments": {}}, {"tool": "read_b", "arguments": {}}]}
    executed = asyncio.run(execute_plan(plan, _registry(), _ctx(db, project)))
    assert [e["tool"] for e in executed] == ["read_a", "read_b"]
    assert all(e["ok"] for e in executed)


def test_execution_halts_on_the_first_failed_step(db, project):
    plan = {"goal": "g", "steps": [
        {"tool": "read_a", "arguments": {}}, {"tool": "fails", "arguments": {}}, {"tool": "read_b", "arguments": {}}]}
    executed = asyncio.run(execute_plan(plan, _registry(), _ctx(db, project)))
    assert [e["tool"] for e in executed] == ["read_a", "fails"], "did not run read_b after the failure"
    assert executed[-1]["halted"] == "error"


def test_execution_halts_when_a_step_files_an_approval(db, project):
    """A plan whose step now waits on a human must not run the next step."""
    plan = {"goal": "g", "steps": [
        {"tool": "read_a", "arguments": {}}, {"tool": "gated", "arguments": {}}, {"tool": "read_b", "arguments": {}}]}
    executed = asyncio.run(execute_plan(plan, _registry(), _ctx(db, project)))
    assert [e["tool"] for e in executed] == ["read_a", "gated"]
    assert executed[-1]["halted"] == "awaiting_approval"
    assert executed[-1]["result"]["status"] == "awaiting_approval"


# --------------------------------------------------------------------------- #
# Summaries
# --------------------------------------------------------------------------- #
def test_a_completed_plan_summary_counts_the_steps():
    plan = {"goal": "g", "steps": [{"tool": "a"}, {"tool": "b"}]}
    executed = [{"tool": "a", "ok": True, "result": {"ok": True}}, {"tool": "b", "ok": True, "result": {"ok": True}}]
    assert "2 of 2" in summarise_execution(plan, executed)


def test_an_approval_halt_summary_says_what_is_queued():
    plan = {"goal": "g", "steps": [{"tool": "a"}, {"tool": "gated"}, {"tool": "c"}]}
    executed = [{"tool": "a", "ok": True, "result": {"ok": True}},
                {"tool": "gated", "ok": True, "result": {"ok": True, "status": "awaiting_approval"}, "halted": "awaiting_approval"}]
    summary = summarise_execution(plan, executed)
    assert "needs your approval" in summary
    assert "2 of 3" in summary


# --------------------------------------------------------------------------- #
# End to end through the orchestrator
# --------------------------------------------------------------------------- #
def test_the_orchestrator_round_trip_propose_then_confirm(db, project, humans):
    """A propose_plan result is stashed; a following 'proceed' executes it."""
    from galeqea.ai.orchestrator import Orchestrator
    from galeqea.models import ChatSession

    session = ChatSession(project_id=project.id, title="t", context={})
    db.add(session)
    db.flush()

    # Simulate the agent having proposed a plan last turn.
    stash_plan(session, {"goal": "list then read", "writes_state": False, "steps": [
        {"tool": "query_requirements", "arguments": {"feature": "checkout"}, "effect": "read-only"},
    ]})
    assert pending_plan(session) is not None

    orch = Orchestrator(db, project_id=project.id)
    reply = asyncio.run(orch.handle(session=session, user=humans["author"], text="proceed"))
    assert pending_plan(session) is None, "the plan was consumed"
    assert "plan" in reply.text.lower() or "step" in reply.text.lower()


def test_the_orchestrator_cancels_on_stop(db, project, humans):
    from galeqea.ai.orchestrator import Orchestrator
    from galeqea.models import ChatSession

    session = ChatSession(project_id=project.id, title="t", context={})
    db.add(session)
    db.flush()
    stash_plan(session, {"goal": "g", "steps": [{"tool": "query_requirements", "arguments": {}}]})

    orch = Orchestrator(db, project_id=project.id)
    reply = asyncio.run(orch.handle(session=session, user=humans["author"], text="stop"))
    assert pending_plan(session) is None
    assert "cancel" in reply.text.lower()
