"""Regression delta: what changed vs the baseline run."""

from __future__ import annotations

from galeqea.models import Run, RunTest
from galeqea.services import regression


def _run(db, project, number, results, base_url="https://x.com"):
    run = Run(project_id=project.id, number=number, title=f"GP {number}", status="failed",
              trigger="golden_path", environment="production", base_url=base_url,
              totals={})
    db.add(run)
    db.flush()
    for key, status in results.items():
        db.add(RunTest(run_id=run.id, test_case_id="", test_key=key,
                       title=f"functional: /{key[-1]}", status=status))
    db.commit()
    return run


def test_delta_classifies_every_change(db, project):
    base = _run(db, project, 1, {
        "P-GP-FUNCTIONAL-01": "passed",   # will break → new_fail
        "P-GP-FUNCTIONAL-02": "failed",   # will pass → fixed
        "P-GP-FUNCTIONAL-03": "failed",   # stays failed → still_failing
        "P-GP-FUNCTIONAL-04": "passed",   # stays passed → unchanged
        "P-GP-A11Y-01": "passed",         # will go flaky → new_flaky
    })
    cur = _run(db, project, 2, {
        "P-GP-FUNCTIONAL-01": "failed",
        "P-GP-FUNCTIONAL-02": "passed",
        "P-GP-FUNCTIONAL-03": "failed",
        "P-GP-FUNCTIONAL-04": "passed",
        "P-GP-A11Y-01": "flaky",
    })
    d = regression.compute_delta(db, cur)
    assert d["baseline_run_number"] == base.number
    assert d["totals"] == {"new_fail": 1, "still_failing": 1, "new_flaky": 1,
                           "fixed": 1, "unchanged": 1}
    assert "1 newly failing" in d["what_changed"] and "vs run #1" in d["what_changed"]
    # Only the changed rows are surfaced (unchanged is a count, not noise).
    assert {r["key"] for r in d["rows"]} == {
        "P-GP-FUNCTIONAL-01", "P-GP-FUNCTIONAL-02", "P-GP-FUNCTIONAL-03", "P-GP-A11Y-01"}
    nf = next(r for r in d["rows"] if r["key"] == "P-GP-FUNCTIONAL-01")
    assert nf["delta"] == "new_fail" and nf["current"] == "failed" and nf["baseline"] == "passed"


def test_delta_without_a_baseline(db, project):
    only = _run(db, project, 1, {"P-GP-FUNCTIONAL-01": "failed"})
    d = regression.compute_delta(db, only)
    assert d["has_baseline"] is False
    assert "nothing to compare" in d["what_changed"].lower()


def test_baseline_is_scoped_to_target_and_env(db, project):
    _run(db, project, 1, {"P-GP-FUNCTIONAL-01": "passed"}, base_url="https://other.com")
    cur = _run(db, project, 2, {"P-GP-FUNCTIONAL-01": "failed"}, base_url="https://x.com")
    d = regression.compute_delta(db, cur)
    assert d["has_baseline"] is False  # the run #1 was a different target
