"""Run: the built suite is approved and run; the Run board reads from results."""

from __future__ import annotations

from sqlalchemy import select

from galeqea.models import Run, RunTest, TestCase
from galeqea.services import journeys
from galeqea.services import test_plan as tp
from galeqea.services.build import build_tests_from_plan
from galeqea.services.golden_run import run_board, runnable_keys

_DISC = {"base": "https://x.com", "pages": ["https://x.com", "https://x.com/a"],
         "forms": 1, "apis": []}


def _build(db, project):
    typed = tp.toggle(tp.build_typed_plan(_DISC), "exploratory", True)  # add a manual-ish type
    journey = journeys.start(db, project.id, "https://x.com")
    journey.plan = {"pages": _DISC["pages"], "typed": typed}
    journey.discovery = _DISC
    result = build_tests_from_plan(db, project, journey)
    journey.test_ids = result["run_keys"]
    return journey


def test_runnable_keys_excludes_exploratory_and_manual(db, project):
    journey = _build(db, project)
    keys = runnable_keys(db, project.id, journey)
    assert keys, "there are automated tests to run"
    assert not any("-EXPLORATORY-" in k or "-MANUAL-" in k for k in keys)
    assert all(k in (journey.test_ids or []) for k in keys)


def test_run_board_groups_by_type_and_costs_zero(db, project):
    run = Run(project_id=project.id, number=7, title="Golden Path", status="running",
              environment="local", base_url="https://x.com",
              totals={"total": 4, "passed": 2, "failed": 1})
    db.add(run)
    db.flush()
    db.add_all([
        RunTest(run_id=run.id, test_case_id="", test_key="X-GP-A11Y-01", title="a11y /", status="passed",
                duration_ms=500),
        RunTest(run_id=run.id, test_case_id="", test_key="X-GP-A11Y-02", title="a11y /a", status="failed",
                duration_ms=700, error_message="2 serious violations"),
        RunTest(run_id=run.id, test_case_id="", test_key="X-GP-PERF-01", title="perf /", status="passed",
                duration_ms=900),
        RunTest(run_id=run.id, test_case_id="", test_key="X-GP-PERF-02", title="perf /a", status="running"),
    ])
    db.commit()

    journey = journeys.start(db, project.id, "https://x.com")
    board = run_board(db, run, journey)

    assert board["totals"] == {"total": 4, "passed": 2, "failed": 1, "running": 1,
                               "queued": 0, "blocked": 0, "skipped": 0, "remaining": 1}
    assert board["cost"] == {"calls": 0, "tokens": 0, "usd": 0.0}  # re-run is free
    by_type = {g["type"]: g for g in board["by_type"]}
    assert by_type["a11y"]["label"] == "Accessibility"
    assert by_type["a11y"]["passed"] == 1 and by_type["a11y"]["failed"] == 1
    assert by_type["perf"]["running"] == 1
    assert board["has_failures"] is True


def test_a_built_but_unrun_test_shows_as_skipped_not_missing(db, project):
    from galeqea.models import RunStatus
    journey = _build(db, project)
    # Quarantine one built test, then run only the rest; the drop must be visible.
    keys = runnable_keys(db, project.id, journey)
    dropped = keys[0]
    tc = next(c for c in db.execute(select(TestCase).where(
        TestCase.project_id == project.id, TestCase.key == dropped)).scalars())
    tc.quarantined = True
    run = Run(project_id=project.id, number=9, title="GP", status=RunStatus.PASSED,
              environment="local", base_url="https://x.com",
              totals={"total": 1, "passed": 1, "failed": 0})
    db.add(run)
    db.flush()
    db.add(RunTest(run_id=run.id, test_case_id="", test_key=keys[1], title="ran",
                   status=RunStatus.PASSED, duration_ms=100))
    db.commit()

    board = run_board(db, run, journey)
    dropped_entry = next((n for n in board["not_run"] if n["key"] == dropped), None)
    assert dropped_entry is not None            # never silently missing
    assert dropped_entry["reason"] == "quarantined"
    assert board["ok"] is False                 # an unrun built test isn't "all clear"


def test_rebuild_clears_a_prior_runs_quarantine(db, project):
    """A test quarantined in one journey's triage must not stay excluded when the
    plan is rebuilt; otherwise it silently drops from the next run."""
    from galeqea.models import TestStatus
    journey = _build(db, project)
    key = runnable_keys(db, project.id, journey)[0]
    tc = next(c for c in db.execute(select(TestCase).where(
        TestCase.project_id == project.id, TestCase.key == key)).scalars())
    tc.quarantined = True
    tc.test_data = {"quarantine": {"until": "2099-01-01", "reason": "old"}}
    db.commit()

    build_tests_from_plan(db, project, journey)  # rebuild
    tc = next(c for c in db.execute(select(TestCase).where(
        TestCase.project_id == project.id, TestCase.key == key)).scalars())
    assert tc.quarantined is False
    assert "quarantine" not in (tc.test_data or {})
    assert tc.status == TestStatus.PROPOSED
