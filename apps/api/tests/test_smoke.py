"""Smoke: a ≤3-min front-door subset, reusing the built tests, gating the run."""

from __future__ import annotations

from galeqea.models import Run, RunTest
from galeqea.services import journeys, smoke
from galeqea.services import test_plan as tp
from galeqea.services.build import build_tests_from_plan

_DISC = {"base": "https://x.com", "pages": ["https://x.com", "https://x.com/a"],
         "forms": 1, "apis": []}


def _build(db, project):
    typed = tp.build_typed_plan(_DISC)
    journey = journeys.start(db, project.id, "https://x.com")
    journey.plan = {"pages": _DISC["pages"], "typed": typed}
    journey.discovery = _DISC
    result = build_tests_from_plan(db, project, journey)
    journey.test_ids = result["run_keys"]
    return journey


def test_smoke_keys_are_the_built_smoke_tagged_subset(db, project):
    journey = _build(db, project)
    keys = smoke.smoke_keys(db, project.id, journey)
    assert keys, "there is a smoke subset"
    assert set(keys) <= set(journey.test_ids)  # reuse, not a parallel set
    # Every smoke key is an entry-page fast check (functional / seo), not e.g. a11y.
    assert all("-GP-FUNCTIONAL-" in k or "-GP-SEO-" in k for k in keys)


def test_smoke_report_surfaces_blockers(db, project):
    journey = _build(db, project)
    run = Run(project_id=project.id, number=1, title="Smoke", status="failed",
              environment="local", base_url="https://x.com",
              totals={"total": 2, "passed": 1, "failed": 1})
    db.add(run)
    db.flush()
    db.add_all([
        RunTest(run_id=run.id, test_case_id="", test_key="DEMO-GP-FUNCTIONAL-01", title="front door",
                status="passed", duration_ms=300),
        RunTest(run_id=run.id, test_case_id="", test_key="DEMO-GP-SEO-01", title="console clean",
                status="failed", duration_ms=250,
                error_message="12 console errors on load"),
    ])
    db.commit()

    report = smoke._report(db, run, journey, timed_out=False)
    assert report["ok"] is False
    assert len(report["blockers"]) == 1
    b = report["blockers"][0]
    assert b["check"] == "console & page hygiene"
    assert "console errors" in b["detail"]


def test_smoke_report_clean_clears_the_full_run(db, project):
    journey = _build(db, project)
    run = Run(project_id=project.id, number=2, title="Smoke", status="passed",
              environment="local", base_url="https://x.com",
              totals={"total": 2, "passed": 2, "failed": 0})
    db.add(run)
    db.flush()
    db.add_all([
        RunTest(run_id=run.id, test_case_id="", test_key="DEMO-GP-FUNCTIONAL-01", title="front door",
                status="passed", duration_ms=300),
        RunTest(run_id=run.id, test_case_id="", test_key="DEMO-GP-SEO-01", title="console clean",
                status="passed", duration_ms=250),
    ])
    db.commit()

    report = smoke._report(db, run, journey, timed_out=False)
    assert report["ok"] is True
    assert report["blockers"] == []
    assert "clear to run" in report["message"]
