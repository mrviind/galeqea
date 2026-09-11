"""Quality-planning tools: coverage cost, coverage plan, status brief, retro.

All deterministic: the same project state must yield the same output, because a
retrospective (or a cost estimate) that changed each time you asked would be
useless. These lock that, plus the judgement rules (blocked items stay out of the
cover-next list, priority = risk × gap × recent-failure history, re-runs cost 0
tokens, the retro cites evidence). No PM vocabulary: the unit is a requirement
covered by tests, never a person's time.
"""

from __future__ import annotations

import uuid

import pytest

from galeqea.ai.tools import ToolContext, registry
from galeqea.ai.toolset import tool_catalog  # noqa: F401
from galeqea.mcp.agile import (
    estimate_coverage,
    estimate_coverage_cost,
    plan_coverage,
    quality_retrospective,
)
from galeqea.mcp.agile import (
    test_status_brief as run_status_brief,
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
# Coverage-cost rule
# --------------------------------------------------------------------------- #
def test_estimate_is_deterministic():
    a = estimate_coverage(criteria=3, risk="high", existing_tests=0, open_questions=0)
    b = estimate_coverage(criteria=3, risk="high", existing_tests=0, open_questions=0)
    assert a == b


def test_higher_risk_needs_more_tests_all_else_equal():
    low = estimate_coverage(criteria=2, risk="low", existing_tests=0, open_questions=0)["tests_to_author"]
    crit = estimate_coverage(criteria=2, risk="critical", existing_tests=0, open_questions=0)["tests_to_author"]
    assert crit >= low


def test_open_questions_block_and_lower_confidence():
    e = estimate_coverage(criteria=2, risk="high", existing_tests=0, open_questions=2)
    assert e["blocked"] is True
    assert e["confidence"] < 0.9


def test_existing_tests_reduce_the_authoring_work():
    without = estimate_coverage(criteria=4, risk="medium", existing_tests=0, open_questions=0)["tests_to_author"]
    with_tests = estimate_coverage(criteria=4, risk="medium", existing_tests=4, open_questions=0)["tests_to_author"]
    assert with_tests < without


def test_reruns_always_cost_zero_tokens():
    for risk in ("low", "medium", "high", "critical"):
        assert estimate_coverage(criteria=3, risk=risk, existing_tests=0, open_questions=0)["rerun_tokens"] == 0


def test_cost_reads_an_ingested_requirement(db, project, backlog):
    r = estimate_coverage_cost({"requirement_ref": f"SP{backlog}-1"}, _ctx(db, project))
    assert r["ok"] and r["tests_to_author"] >= 1 and r["rerun_tokens"] == 0
    assert r["requirement"]["risk"] == "critical"
    assert r["build_tokens"] > 0  # authoring costs tokens, once


def test_cost_can_size_a_shape_directly():
    r = estimate_coverage_cost({"criteria": 3, "risk": "high"}, None)
    assert r["ok"] and r["tests_to_author"] >= 1 and r["run_minutes"] > 0


# --------------------------------------------------------------------------- #
# Coverage planning
# --------------------------------------------------------------------------- #
def test_plan_ranks_highest_value_first(db, project, backlog):
    plan = plan_coverage({}, _ctx(db, project))
    refs = [c["ref"] for c in plan["cover_next"]]
    assert refs and refs[0] == f"SP{backlog}-1", "the critical, uncovered requirement ranks first"
    # priorities are non-increasing down the list
    priorities = [c["priority"] for c in plan["cover_next"]]
    assert priorities == sorted(priorities, reverse=True)


def test_blocked_requirements_stay_out_of_cover_next(db, project, backlog):
    plan = plan_coverage({"top": 50}, _ctx(db, project))
    cover = {c["ref"] for c in plan["cover_next"]}
    blocked = {b["ref"] for b in plan["blocked"]}
    assert f"SP{backlog}-2" in blocked, "the requirement with an open question must be blocked"
    assert f"SP{backlog}-2" not in cover


def test_a_risk_floor_excludes_lower_risk_work(db, project, backlog):
    plan = plan_coverage({"top": 50, "risk_floor": "high"}, _ctx(db, project))
    considered = {c["ref"] for c in plan["cover_next"] + plan["deferred"] + plan["blocked"]}
    assert f"SP{backlog}-4" not in considered  # low risk, filtered out
    assert f"SP{backlog}-3" not in considered  # medium, below the high floor


def test_plan_projects_a_markdown_board(db, project, backlog):
    plan = plan_coverage({}, _ctx(db, project))
    assert plan["_ui"]["pane"] == "requirements"
    assert "# Coverage plan" in plan["_ui"]["markdown"]


def test_plan_with_no_requirements_is_graceful(db, project):
    plan = plan_coverage({}, _ctx(db, project))
    assert plan["ok"] is True and plan["cover_next"] == []


# --------------------------------------------------------------------------- #
# Status brief & retrospective
# --------------------------------------------------------------------------- #
def test_status_brief_reports_recent_blocked_and_next(db, project, backlog):
    s = run_status_brief({}, _ctx(db, project))
    assert set(s.keys()) >= {"recent", "blocked", "new_failures", "run_today"}
    assert s["blocked"]["requirements_uncovered"] >= 4  # the seeded ones
    assert s["run_today"]


def test_status_brief_is_deterministic(db, project, backlog):
    ctx = _ctx(db, project)
    assert run_status_brief({}, ctx)["blocked"] == run_status_brief({}, ctx)["blocked"]


def test_retrospective_cites_evidence_not_vibes(db, project, backlog):
    r = quality_retrospective({}, _ctx(db, project))
    assert r["improved"] and r["regressed"] and r["action_items"]
    # A critical uncovered requirement must drive a concrete action.
    assert any("critical" in w.lower() for w in r["regressed"])
    assert any("SP" in a or "cover" in a.lower() for a in r["action_items"])
    assert "heals_applied" in r["metrics"] and "recurring_failures" in r["metrics"]


def test_retrospective_is_deterministic(db, project, backlog):
    ctx = _ctx(db, project)
    a, b = quality_retrospective({}, ctx), quality_retrospective({}, ctx)
    assert a["metrics"] == b["metrics"] and a["action_items"] == b["action_items"]


# --------------------------------------------------------------------------- #
# Registration: no PM-shaped tool names remain
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["estimate_coverage_cost", "plan_coverage",
                                  "test_status_brief", "quality_retrospective"])
def test_planning_tools_are_registered_read_only(name):
    tool = registry.get(name)
    assert tool is not None and tool.read_only and tool.approval_action is None
    assert tool.title


@pytest.mark.parametrize("gone", ["estimate_test_effort", "plan_test_sprint",
                                  "test_standup", "test_retrospective"])
def test_old_pm_shaped_names_are_removed(gone):
    assert registry.get(gone) is None
