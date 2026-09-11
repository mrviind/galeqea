"""Chat parity: the report and planning surfaces reachable in plain English.

Every one is deterministic (no model): a report request comes back as a card
carrying export actions; a planning request runs the tool and renders its output.
The chat is the primary interface, so these must not depend on a model being
configured.
"""

from __future__ import annotations

import uuid

import pytest

from galeqea.ai.orchestrator import Orchestrator
from galeqea.models import (
    ChatSession,
    DocKind,
    RequirementDoc,
    RequirementItem,
    Role,
    Run,
    User,
)


@pytest.fixture()
def chat(db, project):
    user = User(email=f"u-{uuid.uuid4().hex[:8]}@x.io", name="U", role=Role.OWNER.value)
    db.add(user)
    session = ChatSession(project_id=project.id, title="t")
    db.add(session)
    run = Run(project_id=project.id, number=3, title="Smoke", status="passed",
              environment="local", totals={"total": 2, "passed": 2, "failed": 0})
    db.add(run)
    doc = RequirementDoc(project_id=project.id, title="PRD", kind=DocKind.REQUIREMENT)
    db.add(doc)
    db.flush()
    db.add(RequirementItem(doc_id=doc.id, project_id=project.id, ref="REQ-1", risk="critical",
                           title="login", acceptance_criteria=["a"]))
    db.commit()
    orch = Orchestrator(db, provider=None, project_id=project.id)

    async def send(text: str):
        return await orch.handle(session=session, user=user, text=text)

    return send


def _blocks(reply):
    return [b.get("type") for b in (reply.blocks or [])]


async def test_report_requests_render_as_cards_with_export(chat):
    for msg, rtype in [
        ("coverage report as markdown", "coverage"),
        ("flaky report", "flaky"),
        ("traceability report", "traceability"),
        ("export last run as junit", "run"),
        ("copy the run report for AI", "run"),
    ]:
        reply = await chat(msg)
        assert _blocks(reply) == ["report_card"], msg
        card = reply.blocks[0]
        assert card["report"] == rtype
        assert card["api_base"] and card["junit"] == (rtype == "run")


async def test_run_report_card_is_junit_capable(chat):
    reply = await chat("give me the run report")
    card = reply.blocks[0]
    assert card["report"] == "run" and card["junit"] is True
    assert "runs/" in card["api_base"]


async def test_plan_coverage_runs_deterministically(chat):
    reply = await chat("plan coverage")
    # A doc card (the plan markdown) since requirements exist.
    assert _blocks(reply) == ["doc"]
    assert "cover" in reply.blocks[0]["markdown"].lower()


async def test_quality_retro_accepts_a_day_window(chat):
    reply = await chat("quality retro last 30 days")
    assert _blocks(reply) == ["doc"]
    assert "retrospective" in reply.blocks[0]["markdown"].lower()


async def test_status_brief_answers_from_data(chat):
    reply = await chat("status brief")
    # Text answer (recent/blocked/today), no model.
    assert "recent" in reply.text.lower() or "today" in reply.text.lower()


async def test_these_work_with_no_model(chat):
    # The orchestrator was built with provider=None; a report card must still come back.
    reply = await chat("coverage report")
    assert _blocks(reply) == ["report_card"]
