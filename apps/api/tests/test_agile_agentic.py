"""Coverage planning, driven agentically: an agent runs plan_coverage end to end
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
            return Completion(tool_calls=[{"id": "c1", "name": "plan_coverage",
                                           "arguments": {"top": 5}}])
        plan = self.saw[-1] if self.saw else {}
        return Completion(text=(
            f"Here's a coverage plan: {len(plan.get('cover_next', []))} requirement(s) "
            "to cover next, highest value first. Refine it with the team."
        ))

    async def stream(self, messages, **_):
        raise AssertionError
        yield  # pragma: no cover


def test_an_agent_runs_coverage_planning_from_a_prompt(db, project):
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
    result = asyncio.run(agent.run("Plan what to cover next, top 5", ctx, history=[]))

    # The planning tool ran through the real loop and produced a ranked list.
    assert [s["tool"] for s in result.steps] == ["plan_coverage"]
    plan = result.steps[0]["result"]
    assert plan["ok"] and plan["cover_next"]
    assert plan["cover_next"][0]["risk"] == "critical", "critical work ranks first"
    # The model saw the plan and answered coherently.
    assert "coverage plan" in result.text.lower()
