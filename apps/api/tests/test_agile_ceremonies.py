"""Agile ceremonies for QE: estimation, sprint planning, standup, retrospective.

All deterministic — the same project state must yield the same ceremony output,
because a retrospective (or estimate) that changed each time you asked would be
useless. These lock that, plus the judgement rules (blocked items stay out of a
sprint, value = risk × gap, retro cites evidence).
"""

from __future__ import annotations

import uuid

import pytest

from galeqea.ai.tools import ToolContext, registry
from galeqea.ai.toolset import tool_catalog  # noqa: F401
from galeqea.mcp.agile import (
    estimate_points, estimate_test_effort, plan_test_sprint,
    test_retrospective as run_retrospective,
    test_standup as run_standup,
)
from galeqea.models import DocKind, RequirementDoc, RequirementItem


def _ctx(db, project):
    return ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")


@pytest.fixture()
def backlog(db, project):
    """Seed requirements with a spread of risk and some open questions."""
    doc = RequirementDoc(project_id=project.id, title="PRD", kind=DocKind.REQUIREMENT)
    db.add(doc)
    db.flush()
    tag = uuid.uuid4().hex[:6].upper()
    rows = [
        (f"SP{tag}-1", "critical", ["a", "b"], []),
        (f"SP{tag}-2", "high", ["c", "d"], ["unresolved?"]),
        (f"SP{tag}-3", "medium", ["e"], []),
        (f"SP{tag}-4", "low", ["f"], []),
    ]
    for ref, risk, crit, q in rows:
        db.add(RequirementItem(doc_id=doc.id, project_id=project.id, ref=ref, risk=risk,
                               title=f"req {ref}", acceptance_criteria=crit, open_questions=q))
    db.flush()
    return tag


# --------------------------------------------------------------------------- #
# Estimation rule
# --------------------------------------------------------------------------- #
def test_estimate_is_deterministic():
    a = estimate_points(criteria=3, risk="high", existing_tests=0, open_questions=0)
    b = estimate_points(criteria=3, risk="high", existing_tests=0, open_questions=0)
    assert a == b


def test_higher_risk_costs_more_all_else_equal():
    low = estimate_points(criteria=2, risk="low", existing_tests=0, open_questions=0)["points"]
    crit = estimate_points(criteria=2, risk="critical", existing_tests=0, open_questions=0)["points"]
    assert crit >= low


def test_open_questions_block_and_lower_confidence():
    e = estimate_points(criteria=2, risk="high", existing_tests=0, open_questions=2)
    assert e["blocked"] is True
    assert e["confidence"] < 0.9


def test_existing_tests_reduce_the_estimate():
    without = estimate_points(criteria=4, risk="medium", existing_tests=0, open_questions=0)["points"]
    with_tests = estimate_points(criteria=4, risk="medium", existing_tests=4, open_questions=0)["points"]
    assert with_tests <= without


def test_points_land_on_the_fibonacci_scale():
    for c in range(1, 8):
        assert estimate_points(criteria=c, risk="critical", existing_tests=0, open_questions=0)["points"] in {1, 2, 3, 5, 8, 13}


def test_estimate_reads_an_ingested_requirement(db, project, backlog):
    r = estimate_test_effort({"requirement_ref": f"SP{backlog}-1"}, _ctx(db, project))
    assert r["ok"] and r["points"] in {1, 2, 3, 5, 8, 13}
    assert r["requirement"]["risk"] == "critical"


def test_estimate_can_size_a_shape_directly():
    r = estimate_test_effort({"criteria": 3, "risk": "high"}, None)
    assert r["ok"] and r["points"] >= 1


# --------------------------------------------------------------------------- #
# Sprint planning
# --------------------------------------------------------------------------- #
def test_the_sprint_fills_highest_value_first(db, project, backlog):
    plan = plan_test_sprint({"capacity_points": 6}, _ctx(db, project))
    refs = [c["ref"] for c in plan["committed"]]
    assert f"SP{backlog}-1" in refs, "the critical requirement must be committed first"
    # The committed points never exceed capacity.
    assert plan["committed_points"] <= 6


def test_blocked_requirements_never_enter_the_sprint(db, project, backlog):
    plan = plan_test_sprint({"capacity_points": 50}, _ctx(db, project))
    committed = {c["ref"] for c in plan["committed"]}
    blocked = {b["ref"] for b in plan["blocked"]}
    assert f"SP{backlog}-2" in blocked, "the requirement with an open question must be blocked"
    assert f"SP{backlog}-2" not in committed


def test_a_risk_floor_excludes_low_risk_work(db, project, backlog):
    plan = plan_test_sprint({"capacity_points": 50, "risk_floor": "high"}, _ctx(db, project))
    considered = {c["ref"] for c in plan["committed"] + plan["backlog"] + plan["blocked"]}
    assert f"SP{backlog}-4" not in considered  # low risk, filtered out
    assert f"SP{backlog}-3" not in considered  # medium, below the high floor


def test_planning_projects_a_markdown_sprint_board(db, project, backlog):
    plan = plan_test_sprint({"capacity_points": 8}, _ctx(db, project))
    assert plan["_ui"]["pane"] == "requirements"
    assert "# Sprint plan" in plan["_ui"]["markdown"]


def test_planning_with_no_requirements_is_graceful(db, project):
    plan = plan_test_sprint({"capacity_points": 20}, _ctx(db, project))
    assert plan["ok"] is True
    assert plan["committed"] == []


# --------------------------------------------------------------------------- #
# Standup & retrospective
# --------------------------------------------------------------------------- #
def test_standup_reports_the_three_scrum_questions(db, project, backlog):
    s = run_standup({}, _ctx(db, project))
    assert set(s.keys()) >= {"done", "in_progress", "blocked"}
    assert s["blocked"]["requirements_uncovered"] >= 4  # the seeded ones


def test_standup_is_deterministic(db, project, backlog):
    ctx = _ctx(db, project)
    assert run_standup({}, ctx)["blocked"] == run_standup({}, ctx)["blocked"]


def test_retrospective_cites_evidence_not_vibes(db, project, backlog):
    r = run_retrospective({}, _ctx(db, project))
    assert r["went_well"] and r["went_wrong"] and r["action_items"]
    # A critical uncovered requirement must drive a concrete action.
    assert any("critical" in w.lower() for w in r["went_wrong"])
    assert any("SP" in a or "cover" in a.lower() for a in r["action_items"])


def test_retrospective_is_deterministic(db, project, backlog):
    ctx = _ctx(db, project)
    a, b = run_retrospective({}, ctx), run_retrospective({}, ctx)
    assert a["metrics"] == b["metrics"] and a["action_items"] == b["action_items"]


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["estimate_test_effort", "plan_test_sprint", "test_standup", "test_retrospective"])
def test_ceremonies_are_registered_read_only(name):
    tool = registry.get(name)
    assert tool is not None and tool.read_only and tool.approval_action is None
    assert tool.title
