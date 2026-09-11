"""Manual checklist rows: human-executed checks on the same run."""

from __future__ import annotations

from sqlalchemy import select

from galeqea.models import Run, RunTest, TestCase, TestCategory, TestStatus
from galeqea.services import journeys, manual, readiness


def _journey_run(db, project):
    j = journeys.start(db, project.id, "https://x.com")
    j.plan = {"typed": {"totals": {"test_count": 2}}}
    j.test_ids = ["P-GP-A11Y-01", "P-GP-MANUAL-01"]
    db.add_all([
        TestCase(project_id=project.id, key="P-GP-A11Y-01", title="a11y: /",
                 status=TestStatus.APPROVED, category=TestCategory.AUTOMATED,
                 provenance={"type": "a11y"}),
        TestCase(project_id=project.id, key="P-GP-MANUAL-01", title="Manual: receipt email",
                 status=TestStatus.APPROVED, category=TestCategory.MANUAL,
                 charter="Confirm the receipt email arrives", provenance={"type": "manual"}),
    ])
    run = Run(project_id=project.id, number=1, trigger="golden_path", base_url="https://x.com",
              environment="production", status="passed", totals={})
    db.add(run)
    db.flush()
    db.add(RunTest(run_id=run.id, test_case_id="", test_key="P-GP-A11Y-01",
                   title="a11y: /", status="passed"))
    db.commit()
    return j, run


def test_seed_creates_a_pending_manual_row(db, project):
    j, run = _journey_run(db, project)
    added = manual.seed_manual_rows(db, project.id, j, run)
    assert added == 1
    rows = manual.manual_rows(db, run)
    assert len(rows) == 1 and rows[0]["pending"] is True and rows[0]["key"] == "P-GP-MANUAL-01"
    # Seeding is idempotent; a second call adds nothing.
    assert manual.seed_manual_rows(db, project.id, j, run) == 0


def test_pending_manual_blocks_readiness_until_executed(db, project, humans):
    j, run = _journey_run(db, project)
    manual.seed_manual_rows(db, project.id, j, run)
    gate = readiness.evaluate(db, j, run)
    m = next(c for c in gate["criteria"] if c["key"] == "manual")
    assert m["pass"] is False and gate["verdict"] == "no_go"  # an unfilled checklist blocks

    # A human marks it passed → the criterion clears.
    row = manual.manual_rows(db, run)[0]
    result = manual.mark(db, run, row["id"], status="passed", note="email arrived",
                         user=humans["approver"])
    assert result["ok"] and result["pending_manual"] == 0
    rt = db.execute(select(RunTest).where(RunTest.id == row["id"])).scalar_one()
    assert rt.status == "passed"
    assert rt.metrics["executed_by_name"] == "Approver" and rt.metrics["note"] == "email arrived"
    assert readiness.evaluate(db, j, run)["criteria"][1]["pass"] is True


def test_manual_rows_stay_out_of_the_automated_pass_rate(db, project):
    from galeqea.services.golden_run import run_board
    j, run = _journey_run(db, project)
    manual.seed_manual_rows(db, project.id, j, run)
    board = run_board(db, run, j)
    assert board["totals"]["total"] == 1  # only the automated a11y row
    assert len(board["manual"]) == 1 and board["manual"][0]["pending"] is True


def test_marking_a_non_manual_row_is_rejected(db, project):
    j, run = _journey_run(db, project)
    auto = db.execute(select(RunTest).where(
        RunTest.run_id == run.id, RunTest.test_key == "P-GP-A11Y-01")).scalar_one()
    assert manual.mark(db, run, auto.id, status="passed")["ok"] is False
