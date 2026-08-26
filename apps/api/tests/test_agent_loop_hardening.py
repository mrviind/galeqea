"""Adversarial tests of the agentic loop itself.

Not "does a tool work" — those are elsewhere. These drive the *loop* through the
states that break agents in production: a tool that errors mid-plan, a tool that
files an approval, a model that loops without finishing, a tool that raises, a
model that hallucinates a tool name. Each asserts the loop degrades into
something a user can act on rather than a crash or a lie.
"""

from __future__ import annotations

import asyncio

from galeqea.ai.agent import Agent
from galeqea.ai.providers.base import Completion, LLMProvider
from galeqea.ai.providers.base import Role as _PRole
from galeqea.ai.tools import RiskTier, ToolContext, ToolRegistry
from galeqea.core.events import Ev, bus

_TOOL_ROLE = _PRole.TOOL


class Scripted(LLMProvider):
    """A provider that replays a fixed list of turns and records what it saw.

    Each scripted turn is either text (ends the loop) or a tool call (continues).
    `seen_tool_results` captures the tool_result content the loop fed back, so a
    test can assert the model was actually told a tool failed.
    """

    name = "scripted"
    supports_streaming = False

    def __init__(self, turns: list[Completion]):
        super().__init__(model="scripted-1")
        self._turns = turns
        self.calls = 0
        self.seen_tool_results: list[dict] = []

    async def complete(self, messages, **_):
        # Record any tool results the loop has appended before this turn.
        for m in messages:
            if getattr(m, "role", None) is _TOOL_ROLE:
                self.seen_tool_results.append({"content": m.content, "is_error": m.is_error})
        turn = self._turns[min(self.calls, len(self._turns) - 1)]
        self.calls += 1
        return turn

    async def stream(self, messages, **_):
        raise AssertionError("not used")
        yield  # pragma: no cover


def _tool_call(name, args=None):
    return Completion(tool_calls=[{"id": f"c{name}", "name": name, "arguments": args or {}}])


def _text(t):
    return Completion(text=t)


def _run(agent: Agent, db, project) -> object:
    ctx = ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")
    return asyncio.run(agent.run("go", ctx, history=[]))


# --------------------------------------------------------------------------- #
# A tool that fails mid-plan is reported to the model as an error, and the loop
# gives the model a chance to adapt rather than crashing.
# --------------------------------------------------------------------------- #
def test_a_tool_error_is_fed_back_flagged_and_the_model_can_recover(db, project):
    reg = ToolRegistry()

    @reg.register("flaky", description="A tool that fails once. Exists only to test recovery in the loop.",
                  parameters={"properties": {}})
    def flaky(args, ctx):
        return {"ok": False, "error": "upstream 503"}

    provider = Scripted([_tool_call("flaky"), _text("The lookup failed, so I stopped and am telling you.")])
    agent = Agent(provider=provider, registry=reg, role="orchestrator", system_prompt="t")
    result = _run(agent, db, project)

    # The model's second turn saw the failure, flagged.
    assert provider.seen_tool_results[-1]["is_error"] is True
    assert "503" in provider.seen_tool_results[-1]["content"]
    assert "failed" in result.text.lower()
    assert result.steps[0]["result"]["ok"] is False


# --------------------------------------------------------------------------- #
# A tool that raises an exception must not take the loop down with it.
# --------------------------------------------------------------------------- #
def test_a_raising_tool_becomes_an_error_result_not_a_crash(db, project):
    reg = ToolRegistry()

    @reg.register("boom", description="Raises. Exists only to prove the loop survives an exception in a tool.",
                  parameters={"properties": {}})
    def boom(args, ctx):
        raise RuntimeError("kaboom")

    provider = Scripted([_tool_call("boom"), _text("done")])
    agent = Agent(provider=provider, registry=reg, role="orchestrator", system_prompt="t")
    result = _run(agent, db, project)

    step = result.steps[0]["result"]
    assert step["ok"] is False
    assert "kaboom" in step["error"]
    assert result.text == "done"  # loop continued and finished


# --------------------------------------------------------------------------- #
# A hallucinated tool name is answered, not crashed on, and the model is told
# what actually exists.
# --------------------------------------------------------------------------- #
def test_an_unknown_tool_returns_the_available_set(db, project):
    reg = ToolRegistry()

    @reg.register("real", description="A real tool. Present so the unknown-tool error can list something.",
                  parameters={"properties": {}})
    def real(args, ctx):
        return {"ok": True}

    provider = Scripted([_tool_call("imaginary"), _text("ok")])
    agent = Agent(provider=provider, registry=reg, role="orchestrator", system_prompt="t")
    result = _run(agent, db, project)

    err = result.steps[0]["result"]
    assert err["ok"] is False
    assert "real" in err["available"]
    assert provider.seen_tool_results[-1]["is_error"] is True


# --------------------------------------------------------------------------- #
# A state-changing tool files an approval and the loop surfaces it rather than
# executing — the gate holds even under agent control.
# --------------------------------------------------------------------------- #
def test_a_gated_tool_files_an_approval_and_does_not_execute(db, project):
    reg = ToolRegistry()
    executed = {"did": False}

    @reg.register("danger", description="A gated tool. Must never run without approval, even from the agent.",
                  parameters={"properties": {}}, read_only=False,
                  approval_action="test.create", risk=RiskTier.HIGH)
    def danger(args, ctx):
        executed["did"] = True  # must not happen
        return {"ok": True}

    provider = Scripted([_tool_call("danger"), _text("I have queued it for your approval.")])
    agent = Agent(provider=provider, registry=reg, role="orchestrator", system_prompt="t")
    result = _run(agent, db, project)

    assert executed["did"] is False, "a gated tool executed under the agent — the gate failed"
    assert result.pending_approvals, "the approval id was not surfaced"
    assert result.steps[0]["result"]["status"] == "awaiting_approval"


# --------------------------------------------------------------------------- #
# A model that never stops calling tools is bounded by max_steps and says so.
# --------------------------------------------------------------------------- #
def test_an_endless_model_is_bounded_by_the_step_limit(db, project):
    reg = ToolRegistry()

    @reg.register("again", description="Always available. Used to make the model loop forever in this test.",
                  parameters={"properties": {}})
    def again(args, ctx):
        return {"ok": True, "keep_going": True}

    # Every turn asks for the tool again — the model never emits final text.
    provider = Scripted([_tool_call("again")])
    agent = Agent(provider=provider, registry=reg, role="orchestrator", system_prompt="t")
    agent.max_steps = 4
    result = _run(agent, db, project)

    assert len(result.steps) == 4, "the loop ran exactly max_steps times"
    assert "stopped after 4 steps" in result.text


# --------------------------------------------------------------------------- #
# The whole run is recorded to the trace, so an agentic session is auditable.
# --------------------------------------------------------------------------- #
def test_the_run_emits_started_and_finished_events(db, project):
    reg = ToolRegistry()

    @reg.register("noop", description="Does nothing. Present so there is one step to observe on the bus.",
                  parameters={"properties": {}})
    def noop(args, ctx):
        return {"ok": True}

    provider = Scripted([_tool_call("noop"), _text("done")])
    agent = Agent(provider=provider, registry=reg, role="orchestrator", system_prompt="t")

    async def run_and_collect():
        queue = await bus.subscribe(project.id, replay=0)
        ctx = ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")
        await agent.run("go", ctx, history=[], stream_to_bus=True)
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        await bus.unsubscribe(project.id, queue)
        return events

    events = asyncio.run(run_and_collect())
    types = {e.type for e in events}
    assert Ev.AGENT_STARTED in types
    assert Ev.AGENT_TOOL_CALL in types
    assert Ev.AGENT_STEP in types
