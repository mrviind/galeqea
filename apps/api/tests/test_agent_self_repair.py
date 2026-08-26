"""Agent self-repair: a failed tool hands the model its recovery hint, and a
repeated identical failure earns an escalating instruction — so the loop adapts
instead of looping on a broken call.
"""

from __future__ import annotations

import asyncio

import pytest

from galeqea.ai.agent import _call_signature, _repair_note, _tool_result_for_model
from galeqea.ai.agent import Agent
from galeqea.ai.providers.base import Completion, LLMProvider, Role, Usage
from galeqea.ai.tools import ToolContext, ToolRegistry


# --------------------------------------------------------------------------- #
# The note itself
# --------------------------------------------------------------------------- #
def test_a_first_failure_carries_the_tools_own_guidance():
    note = _repair_note("query_requirements",
                        {"ok": False, "error": "no criteria", "guidance": "Ask the user for the criteria."}, 1)
    assert note.startswith("[repair]")
    assert "Ask the user for the criteria." in note
    assert "do not repeat" in note.lower()


def test_a_repeated_identical_failure_escalates():
    note = _repair_note("query_requirements", {"ok": False, "error": "no criteria"}, 3)
    assert "FAILED AGAIN" in note
    assert "3 times" in note
    assert "escalate_to_human" in note


def test_the_repair_note_is_read_before_the_raw_result():
    out = _tool_result_for_model(
        {"ok": False, "error": "boom"}, repair="[repair] fix it")
    assert out.startswith("[repair] fix it")
    assert '"ok": false' in out  # the JSON is still there, just after the note


def test_a_success_carries_no_repair_prefix():
    assert not _tool_result_for_model({"ok": True, "x": 1}).startswith("[repair]")


def test_call_signatures_ignore_argument_order():
    a = _call_signature({"name": "t", "arguments": {"a": 1, "b": 2}})
    b = _call_signature({"name": "t", "arguments": {"b": 2, "a": 1}})
    assert a == b


# --------------------------------------------------------------------------- #
# Through the loop: the model actually sees the guidance and recovers
# --------------------------------------------------------------------------- #
class Scripted(LLMProvider):
    name = "scripted"
    supports_streaming = False

    def __init__(self, turns):
        super().__init__(model="s")
        self._turns = turns
        self.calls = 0
        self.tool_messages_seen: list[str] = []

    async def complete(self, messages, **_):
        for m in messages:
            if getattr(m, "role", None) is Role.TOOL:
                self.tool_messages_seen.append(m.content)
        turn = self._turns[min(self.calls, len(self._turns) - 1)]
        self.calls += 1
        return turn

    async def stream(self, messages, **_):
        raise AssertionError
        yield  # pragma: no cover


def _tc(name, args=None):
    return Completion(tool_calls=[{"id": f"c{name}{id(args)}", "name": name, "arguments": args or {}}])


def _run(provider, reg, db, project):
    agent = Agent(provider=provider, registry=reg, role="orchestrator", system_prompt="t")
    ctx = ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")
    return asyncio.run(agent.run("go", ctx, history=[]))


def test_the_model_sees_the_guidance_and_recovers(db, project):
    """First call fails with a hint; the model reads it, calls with fixed args,
    succeeds, and finishes."""
    reg = ToolRegistry()

    @reg.register("lookup", description="Looks up by ref. Fails without one. Present to test recovery.",
                  parameters={"properties": {"ref": {"type": "string", "description": "the ref"}}})
    def lookup(args, ctx):
        if not args.get("ref"):
            return {"ok": False, "error": "no ref given",
                    "guidance": "Pass a ref like REQ-014 and try again."}
        return {"ok": True, "found": args["ref"]}

    provider = Scripted([
        _tc("lookup", {}),                 # fails
        _tc("lookup", {"ref": "REQ-014"}),  # the model adapted
        Completion(text="Found REQ-014."),
    ])
    result = _run(provider, reg, db, project)

    # The model's second turn saw the repair note carrying the tool's guidance.
    assert any("Pass a ref like REQ-014" in m for m in provider.tool_messages_seen)
    assert any(m.startswith("[repair]") for m in provider.tool_messages_seen)
    assert result.text == "Found REQ-014."
    assert [s["tool"] for s in result.steps] == ["lookup", "lookup"]
    assert result.steps[0]["result"]["ok"] is False
    assert result.steps[1]["result"]["ok"] is True


def test_a_repeated_identical_failure_gets_the_escalation_note(db, project):
    reg = ToolRegistry()

    @reg.register("always_fails", description="Always fails. Present to test the repeat-failure escalation path.",
                  parameters={"properties": {}})
    def always_fails(args, ctx):
        return {"ok": False, "error": "broken"}

    # The model stubbornly repeats the same call, then gives up with text.
    provider = Scripted([
        _tc("always_fails", {}),
        _tc("always_fails", {}),
        Completion(text="I could not do it."),
    ])
    _run(provider, reg, db, project)

    # The second tool message the model saw should be the escalated note.
    escalated = [m for m in provider.tool_messages_seen if "FAILED AGAIN" in m]
    assert escalated, "a repeated identical failure did not produce an escalating note"
    assert "escalate_to_human" in escalated[0]


def test_a_successful_tool_never_gets_a_repair_note_in_the_loop(db, project):
    reg = ToolRegistry()

    @reg.register("ok_tool", description="Always succeeds. Present to prove success carries no repair note.",
                  parameters={"properties": {}})
    def ok_tool(args, ctx):
        return {"ok": True, "value": 1}

    provider = Scripted([_tc("ok_tool", {}), Completion(text="done")])
    _run(provider, reg, db, project)
    assert not any(m.startswith("[repair]") for m in provider.tool_messages_seen)
