"""Triage: failures grouped by signature; every one gets a disposition."""

from __future__ import annotations

from sqlalchemy import select

from galeqea.models import Run, RunTest, TestCase, TestCategory, TestStatus
from galeqea.services import journeys
from galeqea.services.triage import DISPOSITIONS, set_disposition, triage_board


def _run_with_failures(db, project):
    run = Run(project_id=project.id, number=1, title="Golden Path", status="failed",
              environment="local", base_url="https://x.com",
              totals={"total": 4, "passed": 1, "failed": 3})
    db.add(run)
    db.flush()
    # Two failures share a signature; one is a locator (test-bug); one is flaky.
    db.add_all([
        RunTest(run_id=run.id, test_case_id="", test_key="DEMO-GP-A11Y-01", title="a11y /", status="passed"),
        RunTest(run_id=run.id, test_case_id="", test_key="DEMO-GP-PERF-01", title="perf /", status="failed",
                error_type="BudgetError", error_message="LCP 4200ms > 2500ms",
                failure_signature="perf-lcp-budget", classification="new"),
        RunTest(run_id=run.id, test_case_id="", test_key="DEMO-GP-PERF-02", title="perf /a", status="failed",
                error_type="BudgetError", error_message="LCP 4400ms > 2500ms",
                failure_signature="perf-lcp-budget", classification="new"),
        RunTest(run_id=run.id, test_case_id="", test_key="DEMO-GP-FUNCTIONAL-01", title="home", status="failed",
                error_type="TimeoutError", error_message="Timeout waiting for #hero",
                failure_signature="func-hero-missing", classification="new"),
    ])
    # matching TestCases so quarantine can act on them
    for key in ("DEMO-GP-PERF-01", "DEMO-GP-PERF-02", "DEMO-GP-FUNCTIONAL-01"):
        db.add(TestCase(project_id=project.id, key=key, title=key,
                        status=TestStatus.APPROVED, category=TestCategory.AUTOMATED))
    db.commit()
    return run


def test_board_groups_failures_by_signature(db, project):
    run = _run_with_failures(db, project)
    journey = journeys.start(db, project.id, "https://x.com")
    board = triage_board(db, run, journey)

    assert board["failure_count"] == 3
    assert board["group_count"] == 2          # two share perf-lcp-budget
    assert board["open_count"] == 2           # nothing dispositioned yet
    assert board["resolved"] is False
    perf = next(g for g in board["groups"] if g["signature"] == "perf-lcp-budget")
    assert perf["count"] == 2
    assert perf["rca"] == "app-bug"           # a budget failure, not a locator
    assert perf["suggested"] == "file_bug"
    func = next(g for g in board["groups"] if g["signature"] == "func-hero-missing")
    assert func["rca"] == "test-bug"          # "Timeout waiting for #hero" → healable
    assert func["suggested"] == "heal"


def test_quarantine_disposition_resolves_and_quarantines_tests(db, project):
    run = _run_with_failures(db, project)

    set_disposition(db, run, "perf-lcp-budget", "quarantine", actor="u1")
    result = set_disposition(db, run, "func-hero-missing", "mark_expected", actor="u1")

    board = result["board"]
    assert board["resolved"] is True          # every open failure now has a call
    assert board["open_count"] == 0
    perf_cases = db.execute(select(TestCase).where(
        TestCase.project_id == project.id,
        TestCase.key.in_(["DEMO-GP-PERF-01", "DEMO-GP-PERF-02"]))).scalars().all()
    assert len(perf_cases) == 2
    assert all(c.quarantined for c in perf_cases)
    assert all(c.test_data["quarantine"]["until"] for c in perf_cases)  # has an expiry
    func = db.execute(select(TestCase).where(
        TestCase.project_id == project.id,
        TestCase.key == "DEMO-GP-FUNCTIONAL-01")).scalar_one()
    assert func.test_data["expected_failure"]["reason"] == "func-hero-missing"


def test_groups_carry_a_human_label_not_just_a_hash(db, project):
    run = Run(project_id=project.id, number=5, title="GP", status="failed",
              environment="production", base_url="https://x.com", totals={})
    db.add(run)
    db.flush()
    db.add_all([
        RunTest(run_id=run.id, test_case_id="", test_key="DEMO-GP-A11Y-01",
                title="Accessibility (WCAG 2.2 AA): /checkboxes", status="failed",
                error_type="accessibility",
                error_message="3 accessibility violation(s) (image-alt, form-label), allowed 0",
                failure_signature="sig-a11y", classification="new"),
        RunTest(run_id=run.id, test_case_id="", test_key="DEMO-GP-FUNCTIONAL-01",
                title="Functional: /login", status="failed", error_type="TimeoutError",
                error_message="Timeout waiting for #hero", failure_signature="sig-loc",
                classification="new"),
    ])
    db.commit()

    board = triage_board(db, run, journeys.start(db, project.id, "https://x.com"))
    a11y = next(g for g in board["groups"] if g["signature"] == "sig-a11y")
    # Human root cause + the page, hash kept only as data.
    assert a11y["label"] == "accessibility: image-alt, form-label · /checkboxes"
    assert a11y["units"] == ["/checkboxes"]
    assert a11y["keys"] == ["DEMO-GP-A11Y-01"]
    assert a11y["confidence"] in {"high", "medium", "low"}
    # Every disposition is present; the sensible ones enabled, the rest greyed.
    reasons = a11y["disposition_reasons"]
    assert set(reasons) == set(DISPOSITIONS)
    assert reasons["rerun"]["enabled"] is True         # always the tester's call
    assert reasons["file_bug"]["enabled"] is True      # app-bug
    assert reasons["heal"]["enabled"] is False and reasons["heal"]["reason"]
    loc = next(g for g in board["groups"] if g["signature"] == "sig-loc")
    assert loc["rca"] == "test-bug"                     # a locator timeout
    assert loc["disposition_reasons"]["heal"]["enabled"] is True


import pytest

from galeqea.services.triage import _rca


@pytest.mark.parametrize("etype,emsg,cls,expected", [
    ("Error", "net::ERR_CONNECTION_REFUSED at goto", "", "env"),
    ("TimeoutError", "page.goto: Timeout 30000ms exceeded", "", "env"),
    ("HTTPError", "429 Too Many Requests", "", "env"),
    ("HTTPError", "503 Service Unavailable", "", "env"),
    ("HTTPError", "navigation returned HTTP 404 for /pricing", "", "app-bug"),  # broken internal link
    ("AssertionError", "expected text 'Welcome' not found", "", "app-bug"),     # content assertion
    ("accessibility", "1 accessibility violation(s): 1 critical (label)", "", "app-bug"),
    ("TimeoutError", "Timeout waiting for locator #hero", "", "test-bug"),      # stale locator
    ("Error", "anything at all", "flaky", "flaky"),                             # intelligence wins
    ("Error", "connection blip", "env", "env"),
])
def test_rca_classifier_rules(etype, emsg, cls, expected):
    rt = RunTest(run_id="r", test_case_id="", test_key="P-GP-FUNCTIONAL-01",
                 title="t", status="failed", error_type=etype, error_message=emsg,
                 classification=cls)
    assert _rca(rt) == expected


def test_unknown_disposition_is_rejected(db, project):
    run = _run_with_failures(db, project)
    result = set_disposition(db, run, "perf-lcp-budget", "ignore", actor="u1")
    assert result["ok"] is False
