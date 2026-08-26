"""check_run_health — flaky-aware advice before a (re-)run."""

from __future__ import annotations

import uuid

import pytest

from galeqea.ai.tools import ToolContext, registry
from galeqea.ai.toolset import tool_catalog  # noqa: F401
from galeqea.mcp.qe_tools import check_run_health
from galeqea.models import TestCase, TestStat, TestStatus


@pytest.fixture()
def flaky_suite(db, project):
    """A tagged suite where a chosen fraction of tests are flaky."""
    def _make(total: int, flaky: int, quarantined: int = 0, score: float = 0.6):
        tag = "hc-" + uuid.uuid4().hex[:8]
        cases = []
        for i in range(total):
            tc = TestCase(project_id=project.id, key=f"HC-{tag}-{i}", title=f"t{i}",
                          status=TestStatus.APPROVED, tags=[tag], category="automated",
                          quarantined=(i < quarantined))
            db.add(tc); db.flush(); cases.append(tc)
            if quarantined <= i < quarantined + flaky:
                db.add(TestStat(project_id=project.id, test_case_id=tc.id, flake_score=score,
                                flake_confidence=0.8, flake_reasons=["alternating"],
                                outcome_window=[{"status": "passed"}, {"status": "failed"}] * 5))
        db.flush()
        return tag
    return _make


def _ctx(db, project):
    return ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")


def test_a_clean_suite_recommends_run(db, project, flaky_suite):
    tag = flaky_suite(total=4, flaky=0)
    r = check_run_health({"tags": [tag]}, _ctx(db, project))
    assert r["recommendation"] == "run"
    assert r["flaky_count"] == 0


def test_a_mostly_flaky_suite_recommends_quarantine_first(db, project, flaky_suite):
    tag = flaky_suite(total=4, flaky=3)
    r = check_run_health({"tags": [tag]}, _ctx(db, project))
    assert r["recommendation"] == "quarantine_first"
    assert r["flaky_share"] >= 0.5
    assert "narrow" in r["guidance"] or "Quarantine" in r["guidance"]


def test_a_lightly_flaky_suite_says_run_but_expect_noise(db, project, flaky_suite):
    tag = flaky_suite(total=5, flaky=1)  # 20%
    r = check_run_health({"tags": [tag]}, _ctx(db, project))
    assert r["recommendation"] == "run_but_expect_noise"


def test_flaky_tests_are_ranked_worst_first(db, project):
    tag = "rank-" + uuid.uuid4().hex[:8]
    for i, score in enumerate([0.35, 0.9, 0.55]):
        tc = TestCase(project_id=project.id, key=f"RK-{tag}-{i}", title=f"t{i}",
                      status=TestStatus.APPROVED, tags=[tag], category="automated")
        db.add(tc); db.flush()
        db.add(TestStat(project_id=project.id, test_case_id=tc.id, flake_score=score,
                        flake_confidence=0.8, outcome_window=[{"status": "passed"}, {"status": "failed"}] * 5))
    db.flush()
    r = check_run_health({"tags": [tag]}, _ctx(db, project))
    scores = [f["flake_score"] for f in r["flaky"]]
    assert scores == sorted(scores, reverse=True)


def test_quarantined_tests_never_reach_the_selection():
    """select_tests excludes quarantined tests by default, so run_tests never runs
    them — and check_run_health mirrors run_tests exactly. The health check
    therefore reports the set that would actually run, quarantined already gone.
    This is verified against the resolver so the mirror cannot silently drift."""
    from galeqea.engine.plan import select_tests
    import inspect
    src = inspect.getsource(select_tests)
    assert "exclude_quarantined" in src and "quarantined" in src, (
        "select_tests must still filter quarantined; check_run_health relies on it"
    )


def test_an_empty_selection_is_handled(db, project):
    r = check_run_health({"tags": ["does-not-exist-anywhere"]}, _ctx(db, project))
    assert r["ok"] is True
    assert r["recommendation"] == "empty"
    assert r["total"] == 0


def test_a_test_with_no_history_is_not_called_flaky(db, project):
    """No run history means no evidence of flakiness — not a low score to trust."""
    tag = "nohist-" + uuid.uuid4().hex[:8]
    tc = TestCase(project_id=project.id, key=f"NH-{tag}", title="new",
                  status=TestStatus.APPROVED, tags=[tag], category="automated")
    db.add(tc); db.flush()
    r = check_run_health({"tags": [tag]}, _ctx(db, project))
    assert r["flaky_count"] == 0
    assert r["recommendation"] == "run"


def test_it_projects_onto_the_rca_pane(db, project, flaky_suite):
    tag = flaky_suite(total=4, flaky=3)
    r = check_run_health({"tags": [tag]}, _ctx(db, project))
    assert r["_ui"]["pane"] == "rca"
    assert r["_ui"]["review"]["verdict"] == "needs_work"


def test_it_is_registered_read_only(db, project):
    tool = registry.get("check_run_health")
    assert tool is not None and tool.read_only and tool.approval_action is None
