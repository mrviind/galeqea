"""The ceremonies, driven agentically — an agent runs sprint planning end to end
through the real registry and loop, the way the chat would."""

from __future__ import annotations

import asyncio
import json
import uuid

from galeqea.ai.agent import Agent
from galeqea.ai.providers.base import Completion, LLMProvider, Role
from galeqea.ai.tools import ToolContext, registry
from galeqea.ai.toolset import tool_catalog  # noqa: F401
from galeqea.models import DocKind, RequirementDoc, RequirementItem


class ScriptedPlanner(LLMProvider):
    name = "planner"
    supports_streaming = False

    def __init__(self):
        super().__init__(model="p")
        self.calls = 0
        self.saw = []

    async def complete(self, messages, **_):
        for m in messages:
            if getattr(m, "role", None) is Role.TOOL:
                body = m.content.split("\n", 2)[-1] if m.content.startswith("[repair]") else m.content
                try:
                    self.saw.append(json.loads(body))
                except (json.JSONDecodeError, ValueError):
                    self.saw.append({})
        step = self.calls
        self.calls += 1
        if step == 0:
            return Completion(tool_calls=[{"id": "c1", "name": "plan_test_sprint",
                                           "arguments": {"capacity_points": 8}}])
        plan = self.saw[-1] if self.saw else {}
        return Completion(text=(
            f"Here's a sprint plan: {len(plan.get('committed', []))} requirement(s) "
            f"for {plan.get('committed_points', 0)} of {plan.get('capacity_points', 0)} points. "
            "Refine it with the team before we commit."
        ))

    async def stream(self, messages, **_):
        raise AssertionError
        yield  # pragma: no cover


def test_an_agent_runs_sprint_planning_from_a_prompt(db, project):
    doc = RequirementDoc(project_id=project.id, title="PRD", kind=DocKind.REQUIREMENT)
    db.add(doc)
    db.flush()
    tag = uuid.uuid4().hex[:6].upper()
    for i, risk in enumerate(["critical", "high", "medium"]):
        db.add(RequirementItem(doc_id=doc.id, project_id=project.id, ref=f"AG{tag}-{i}",
                               risk=risk, title=f"r{i}", acceptance_criteria=["a", "b"]))
    db.flush()

    provider = ScriptedPlanner()
    agent = Agent(provider=provider, registry=registry, role="orchestrator",
                  system_prompt="You are a Principal SDET.")
    agent.max_steps = 4
    ctx = ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")
    result = asyncio.run(agent.run("Plan the next testing sprint, capacity 8 points", ctx, history=[]))

    # The ceremony tool ran through the real loop and produced a committed plan.
    assert [s["tool"] for s in result.steps] == ["plan_test_sprint"]
    plan = result.steps[0]["result"]
    assert plan["ok"] and plan["committed_points"] <= 8
    assert any(c["risk"] == "critical" for c in plan["committed"]), "critical work committed first"
    # The model saw the plan and answered coherently.
    assert "sprint plan" in result.text.lower()
    assert "points" in result.text.lower()
