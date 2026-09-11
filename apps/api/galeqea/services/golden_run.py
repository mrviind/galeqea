"""Run: approve the built Golden Path suite, run it, and show the Run board.

The Run board is the live face of a Golden Path run: how many tests are running,
passed, or failed, an ETA, the running cost (0, since executing a built test needs
no model), and per-type progress so "Accessibility 3/8" reads at a glance. It is a
snapshot the chat renders and the Runs view streams; the numbers come straight
from the run's own results, so it is deterministic and model-free.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from ..models import Run, RunTest, TestCase, TestCategory, TestStatus
from ..models.base import utcnow
from ..models.testing import TERMINAL_RUN_STATES, RunStatus

#: Bounded wait for the chat turn. A larger suite returns a "running" board that
#: the Runs view keeps streaming, so the chat never hangs on a long run.
RUN_TIMEOUT = 240.0

def _type_token(test_key: str) -> str:
    """The golden-path type of a run test, via the one shared helper, so the board
    bars, the report table, the JUnit suites and readiness never disagree."""
    from .test_plan import type_from_key
    return type_from_key(test_key) or "other"


def runnable_keys(db, project_id: str, journey) -> list[str]:
    """The built Golden Path tests that a full run should execute: the automated
    ones (exploratory / manual charters are for the readiness stage, not a run)."""
    built = list(journey.test_ids or [])
    if not built:
        return []
    rows = db.execute(
        select(TestCase).where(TestCase.project_id == project_id, TestCase.key.in_(built))
    ).scalars()
    return [tc.key for tc in rows if tc.category == TestCategory.AUTOMATED]


def _approve(db, project_id: str, keys: list[str], actor: str | None) -> None:
    for tc in db.execute(
        select(TestCase).where(TestCase.project_id == project_id, TestCase.key.in_(keys))
    ).scalars():
        if tc.status != TestStatus.APPROVED:
            tc.status = TestStatus.APPROVED
            tc.approved_by = actor
            tc.approved_at = utcnow()
    db.commit()


async def run_full(db, *, project, journey, triggered_by: str | None = None,
                   timeout: float = RUN_TIMEOUT) -> dict:
    """Approve the built suite and run it; return the Run board (a snapshot)."""
    from .runs import run_task, start_run

    keys = runnable_keys(db, project.id, journey)
    if not keys:
        return {"ok": False, "reason": "nothing_to_run", "target": journey.target,
                "message": "No runnable tests built yet. Build the tests first."}

    from .access import run_auth
    _approve(db, project.id, keys, triggered_by)
    run = await start_run(
        db, project_id=project.id, selection={"keys": keys},
        environment=journey.environment, trigger="golden_path", triggered_by=triggered_by,
        title=f"Golden Path: {journey.target}", command="run the full suite",
        auth=run_auth(journey), base_url=journey.target,
        rate_limit_rps=(journey.guardrails or {}).get("rate_limit_rps"),
    )
    task = run_task(run.id)
    if task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except TimeoutError:
            pass  # a running board is a perfectly good answer

    db.expire_all()
    run = db.get(Run, run.id)
    # Manual/exploratory checks ride the same run as a human checklist.
    from .manual import seed_manual_rows
    seed_manual_rows(db, project.id, journey, run)
    return run_board(db, run, journey)


def _not_run(db, run: Run, journey, ran_keys: set[str]) -> list[dict]:
    """Built tests that were meant to run but produced no result, so a drop is
    visible on the board with a reason, never silently missing."""
    built = [k for k in (journey.test_ids or []) if k not in ran_keys]
    if not built:
        return []
    cases = {c.key: c for c in db.execute(
        select(TestCase).where(TestCase.project_id == run.project_id,
                               TestCase.key.in_(built))).scalars()}
    out = []
    for key in built:
        tc = cases.get(key)
        if tc is None or tc.category != TestCategory.AUTOMATED:
            continue  # exploratory/manual charters aren't expected in a run
        if tc.quarantined:
            reason = "quarantined"
        elif tc.status != TestStatus.APPROVED:
            reason = "not approved"
        else:
            reason = "not selected"
        out.append({"key": key, "label": tc.title, "reason": reason})
    return out


def run_board(db, run: Run, journey) -> dict:
    """Everything the Run board needs, computed from the run's own results. Manual
    checklist rows are kept out of the automated totals; they have their own
    section, and mixing a human's pending checkbox into the pass rate is misleading."""
    results = [r for r in db.execute(select(RunTest).where(RunTest.run_id == run.id)).scalars()
               if not (r.metrics or {}).get("manual")]
    total = len(results)

    def _count(*statuses):
        return sum(1 for r in results if r.status in statuses)

    passed = _count(RunStatus.PASSED, RunStatus.FLAKY)
    failed = _count(RunStatus.FAILED, RunStatus.ERROR)
    running = _count(RunStatus.RUNNING)
    queued = _count(RunStatus.QUEUED)
    blocked = _count(RunStatus.BLOCKED)
    skipped = _count(RunStatus.SKIPPED, RunStatus.CANCELLED)
    done = run.status in TERMINAL_RUN_STATES

    # Per-type progress, ordered by how much is still outstanding then by name.
    from .test_plan import type_label
    groups: dict[str, dict] = {}
    for r in results:
        token = _type_token(r.test_key)
        g = groups.setdefault(token, {"type": token, "label": type_label(token),
                                      "total": 0, "passed": 0, "failed": 0, "running": 0})
        g["total"] += 1
        if r.status in (RunStatus.PASSED, RunStatus.FLAKY):
            g["passed"] += 1
        elif r.status in (RunStatus.FAILED, RunStatus.ERROR):
            g["failed"] += 1
        elif r.status == RunStatus.RUNNING:
            g["running"] += 1
    by_type = sorted(groups.values(),
                     key=lambda g: (g["passed"] + g["failed"] == g["total"], g["label"]))

    remaining = total - passed - failed - skipped
    avg_ms = _avg_duration(results)
    parallelism = max(1, len(run.browsers or ["chromium"]) * 2)
    eta_seconds = int((remaining * avg_ms) / 1000 / parallelism) if remaining and avg_ms else 0
    elapsed = int((utcnow() - run.started_at).total_seconds()) if run.started_at else 0
    not_run = _not_run(db, run, journey, {r.test_key for r in results}) if done else []

    return {
        "ok": failed == 0 and done and not not_run,
        "run_id": run.id, "run_number": run.number, "status": run.status,
        "target": journey.target, "environment": run.environment, "done": done,
        "totals": {"total": total, "passed": passed, "failed": failed,
                   "running": running, "queued": queued, "blocked": blocked,
                   "skipped": skipped + len(not_run), "remaining": remaining},
        "not_run": not_run,
        # Execution of a built test is deterministic: no model is called, so a
        # run costs nothing. This is the "pay to build, re-run free" promise, live.
        "cost": {"calls": 0, "tokens": 0, "usd": 0.0},
        "eta_seconds": eta_seconds, "elapsed_seconds": elapsed,
        "by_type": by_type,
        # The human checklist that rides this run (manual + exploratory charters).
        "manual": _manual_rows(db, run),
        "has_failures": failed > 0 or blocked > 0,
    }


def _manual_rows(db, run: Run) -> list[dict]:
    from .manual import manual_rows
    return manual_rows(db, run)


def _avg_duration(results: list[RunTest]) -> int:
    done = [r.duration_ms for r in results if r.duration_ms]
    return int(sum(done) / len(done)) if done else 4000  # 4s default per test
