"""Rerun: slice selection and the Rerun×3 env→flaky auto-flip."""

from __future__ import annotations

from galeqea.models import Run, RunTest, TestCase, TestCategory, TestStatus
from galeqea.services import journeys, rerun


def _built(db, project):
    j = journeys.start(db, project.id, "https://x.com")
    specs = [("P-GP-FUNCTIONAL-01", "functional", "https://x.com/"),
             ("P-GP-FUNCTIONAL-02", "functional", "https://x.com/checkout"),
             ("P-GP-A11Y-01", "a11y", "https://x.com/checkout")]
    for key, typ, page in specs:
        db.add(TestCase(project_id=project.id, key=key, title=f"{typ}: {page.split('.com')[-1]}",
                        status=TestStatus.APPROVED, category=TestCategory.AUTOMATED,
                        provenance={"type": typ, "unit": page.split(".com")[-1], "page": page}))
    j.test_ids = [s[0] for s in specs]
    db.flush()
    return j


def test_select_keys_by_failed_suite_and_path(db, project):
    j = _built(db, project)
    parent = Run(project_id=project.id, number=1, trigger="golden_path",
                 base_url="https://x.com", environment="production", totals={})
    db.add(parent)
    db.flush()
    db.add_all([
        RunTest(run_id=parent.id, test_case_id="", test_key="P-GP-FUNCTIONAL-01", status="passed"),
        RunTest(run_id=parent.id, test_case_id="", test_key="P-GP-FUNCTIONAL-02", status="failed"),
        RunTest(run_id=parent.id, test_case_id="", test_key="P-GP-A11Y-01", status="failed"),
    ])
    j.run_id = parent.id
    db.commit()

    assert set(rerun.select_keys(db, project.id, j, {"mode": "failed"})) == {
        "P-GP-FUNCTIONAL-02", "P-GP-A11Y-01"}
    assert rerun.select_keys(db, project.id, j, {"mode": "suite", "type": "a11y"}) == ["P-GP-A11Y-01"]
    assert set(rerun.select_keys(db, project.id, j, {"mode": "path", "path": "/checkout"})) == {
        "P-GP-FUNCTIONAL-02", "P-GP-A11Y-01"}  # both units are on /checkout


async def test_rerun_x3_flips_a_passing_group_to_flaky(db, project, monkeypatch):
    j = _built(db, project)
    parent = Run(project_id=project.id, number=1, trigger="golden_path", base_url="https://x.com",
                 environment="production", totals={}, triage={})
    db.add(parent)
    db.flush()
    db.add(RunTest(run_id=parent.id, test_case_id="", test_key="P-GP-FUNCTIONAL-02",
                   title="functional: /checkout", status="failed",
                   error_type="TimeoutError", error_message="net::ERR_TIMED_OUT",
                   failure_signature="net-timeout"))
    j.run_id = parent.id
    db.commit()

    # Stub the runner: each rerun of the group passes (it was a flake).
    counter = {"n": 1}

    async def fake_start_run(db, *, project_id, selection, **kw):
        counter["n"] += 1
        run = Run(project_id=project_id, number=100 + counter["n"], trigger="rerun",
                  base_url="https://x.com", environment="production", totals={})
        db.add(run)
        db.flush()
        for key in selection["keys"]:
            db.add(RunTest(run_id=run.id, test_case_id="", test_key=key, status="passed"))
        db.commit()
        return run
    monkeypatch.setattr("galeqea.services.runs.start_run", fake_start_run)

    res = await rerun.rerun_x3(db, project=project, journey=j, signature="net-timeout", actor="u1")
    assert res["ok"] and res["verdict"] == "flaky"
    assert res["flipped"] == ["P-GP-FUNCTIONAL-02"] and res["suggest_quarantine"] is True
    assert len(res["child_runs"]) == 3
    # The original result flipped to flaky, so triage no longer counts it as an open failure.
    orig = db.execute(
        select_rt := __import__("sqlalchemy").select(RunTest).where(
            RunTest.run_id == parent.id, RunTest.test_key == "P-GP-FUNCTIONAL-02")
    ).scalar_one()
    assert orig.status == "flaky" and orig.classification == "flaky"
    assert res["board"]["open_count"] == 0
