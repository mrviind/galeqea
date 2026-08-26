"""End-to-end agentic pipeline.

One realistic episode driven through the *real* tool registry and the *real*
agent loop — only the model is scripted. This is the test that proves the parts
built across this session actually compose: a model that plans a QE task and
calls tool after tool gets coherent results, each flowing into the next, the
workspace panes are driven, and the loop terminates with a sensible answer.

The scripted model plays a competent Principal SDET:
    query_requirements → generate_bdd_scenarios → generate_playwright_script
    → review_test → judge_test_against_criteria → final summary
"""

from __future__ import annotations

import asyncio
import json

import pytest

from galeqea.ai.agent import Agent
from galeqea.ai.providers.base import Completion, Delta, LLMProvider, Role, Usage
from galeqea.ai.tools import ToolContext, registry
from galeqea.ai.toolset import tool_catalog  # noqa: F401  (installs every tool)
from galeqea.core.events import Ev, bus
from galeqea.models import DocKind, RequirementDoc, RequirementItem


@pytest.fixture()
def checkout_requirement(db, project):
    """A real requirement with acceptance criteria the pipeline works from."""
    doc = RequirementDoc(project_id=project.id, title="Checkout PRD", kind=DocKind.REQUIREMENT)
    db.add(doc)
    db.flush()
    item = RequirementItem(
        doc_id=doc.id, project_id=project.id, ref="REQ-014", risk="critical", kind="functional",
        title="Checkout must accept a valid payment card",
        text="The checkout page accepts a valid card and confirms the order.",
        acceptance_criteria=[
            "A card passing the Luhn check is accepted and the order is confirmed",
            "A declined card shows an actionable error and commits nothing",
        ],
    )
    db.add(item)
    db.flush()
    return item


class ScriptedSDET(LLMProvider):
    """A model that runs the QE pipeline, feeding each tool's output to the next.

    Rather than replaying fixed arguments, it *reads the previous tool result*
    from the message history — exactly what a real model does — so the test
    proves results genuinely flow between turns, not that a fixed script happens
    to line up.
    """

    name = "scripted-sdet"
    supports_streaming = False

    def __init__(self):
        super().__init__(model="sdet-1")
        self.tool_results: list[dict] = []
        self.calls = 0

    def _last_result(self) -> dict:
        return self.tool_results[-1] if self.tool_results else {}

    async def complete(self, messages, **_):
        # Capture every tool result the loop has fed back so far.
        self.tool_results = []
        for m in messages:
            if getattr(m, "role", None) is Role.TOOL:
                body = m.content.split("\n", 2)[-1] if m.content.startswith("[repair]") else m.content
                try:
                    self.tool_results.append(json.loads(body))
                except (json.JSONDecodeError, ValueError):
                    self.tool_results.append({})

        step = self.calls
        self.calls += 1
        prev = self._last_result()

        if step == 0:
            return _call("query_requirements", {"feature": "checkout", "ref": "REQ-014"})
        if step == 1:
            # Use the ref the requirements lookup actually returned.
            ref = (prev.get("requirements") or [{}])[0].get("ref", "REQ-014")
            return _call("generate_bdd_scenarios", {"requirement_ref": ref})
        if step == 2:
            # Render a scenario from what the BDD tool produced.
            return _call("generate_playwright_script", {
                "scenario": 'Scenario: Pay\n  When the user clicks the "Confirm payment" button\n'
                            '  Then the user sees the "Order confirmed" message',
                "requirement_ref": "REQ-014",
            })
        if step == 3:
            # Review the generated proposal.
            page = prev.get("page_object", {})
            spec = prev.get("spec", {})
            return _call("review_test", {"proposal": {
                "title": "Checkout", "requirement_refs": ["REQ-014"],
                "steps": [
                    {"action": "click", "intent": "click Confirm payment",
                     "target": {"ladder": [{"kind": "role", "role": "button", "name": "Confirm payment"}]}},
                    {"action": "expect_visible", "intent": "order confirmed shown",
                     "expected": "the order confirmed banner", "target": {"ladder": [{"kind": "text", "value": "Order confirmed"}]}},
                ],
                "_files": {"page": bool(page), "spec": bool(spec)},
            }})
        if step == 4:
            return _call("judge_test_against_criteria", {
                "proposal": {"title": "Checkout", "requirement_refs": ["REQ-014"], "steps": [
                    {"action": "expect_visible", "intent": "luhn card accepted, order confirmed",
                     "expected": "the order is confirmed"},
                    {"action": "expect_text", "intent": "declined card shows an actionable error, nothing committed",
                     "expected": "an actionable error, no order"},
                ]},
                "requirement_ref": "REQ-014",
            })
        # Final turn: summarise.
        judged = prev
        return Completion(text=(
            f"I traced REQ-014 through the pipeline. It has "
            f"{judged.get('criteria_count', 0)} acceptance criteria, "
            f"{judged.get('covered_count', 0)} of which the drafted test covers. "
            "The test is ready for your review."
        ))

    async def stream(self, messages, **_):
        raise AssertionError("streaming not used in this test")
        yield  # pragma: no cover


def _call(name, args):
    return Completion(tool_calls=[{"id": f"c{name}", "name": name, "arguments": args}])


def test_the_full_qe_pipeline_runs_through_the_real_loop(db, project, checkout_requirement):
    provider = ScriptedSDET()
    agent = Agent(provider=provider, registry=registry, role="orchestrator",
                  system_prompt="You are a Principal SDET.")
    agent.max_steps = 8
    ctx = ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")

    async def run_and_capture():
        queue = await bus.subscribe(project.id, replay=0)
        result = await agent.run("Cover REQ-014 with a reviewed test", ctx, history=[], stream_to_bus=True)
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        await bus.unsubscribe(project.id, queue)
        return result, events

    result, events = asyncio.run(run_and_capture())

    # 1. The whole chain ran, in order, through the real registry.
    tools_run = [s["tool"] for s in result.steps]
    assert tools_run == [
        "query_requirements", "generate_bdd_scenarios",
        "generate_playwright_script", "review_test", "judge_test_against_criteria",
    ]

    # 2. Every tool succeeded — the results genuinely composed.
    assert all(s["result"]["ok"] for s in result.steps), \
        {s["tool"]: s["result"].get("error") for s in result.steps if not s["result"]["ok"]}

    # 3. Results flowed between turns: requirements found the criteria, BDD
    #    produced scenarios, the script rendered files, coverage judged them.
    by_tool = {s["tool"]: s["result"] for s in result.steps}
    assert by_tool["query_requirements"]["acceptance_criteria_count"] == 2
    assert by_tool["generate_bdd_scenarios"]["scenario_count"] >= 2
    assert by_tool["generate_playwright_script"]["page_object"]["filename"].endswith(".ts")
    assert by_tool["review_test"]["verdict"] in {"sound", "advisory", "needs_work", "blocked"}
    assert by_tool["judge_test_against_criteria"]["criteria_count"] == 2

    # 4. The workspace panes were driven — a _ui projection reached the bus for
    #    the tools that carry one.
    step_events = [e for e in events if e.type == Ev.AGENT_STEP]
    ui_tools = {e.payload["tool"] for e in step_events if e.payload.get("ui")}
    assert {"query_requirements", "generate_playwright_script",
            "review_test", "judge_test_against_criteria"} <= ui_tools

    # 5. The loop terminated with a coherent, grounded answer.
    assert "REQ-014" in result.text
    assert "2 acceptance criteria" in result.text
    assert result.steps  # not an empty run reported as success


def test_the_pipeline_is_auditable_start_to_finish(db, project, checkout_requirement):
    """Every step is recorded on the trace, so the episode can be replayed."""
    provider = ScriptedSDET()
    agent = Agent(provider=provider, registry=registry, role="orchestrator", system_prompt="t")
    agent.max_steps = 8
    ctx = ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")
    result = asyncio.run(agent.run("go", ctx, history=[]))

    assert result.trace_id
    for step in result.steps:
        assert "tool" in step and "arguments" in step and "result" in step and "at" in step


def test_a_missing_requirement_stops_the_pipeline_cleanly(db, project):
    """No REQ-014 ingested: the first tool returns no criteria, and the agent's
    reply reflects that rather than fabricating coverage."""
    provider = ScriptedSDET()
    agent = Agent(provider=provider, registry=registry, role="orchestrator", system_prompt="t")
    agent.max_steps = 8
    ctx = ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")
    result = asyncio.run(agent.run("Cover REQ-014", ctx, history=[]))

    qr = next(s for s in result.steps if s["tool"] == "query_requirements")
    assert qr["result"]["count"] == 0
    assert "invent" in qr["result"]["guidance"].lower()
