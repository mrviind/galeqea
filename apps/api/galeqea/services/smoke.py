"""Smoke: a ≤3-min front-door check before the full Golden Path run.

The smoke subset reuses the Build stage's own tests, the ones tagged ``smoke``
(the entry page's functional journey and its console/hygiene check), plus a login
verification when access is configured. It is not a parallel set of tests; it is
a fast slice of the same built suite. If any smoke check blocks (the entry page
won't load, the console is full of errors, the login fails), the full run is not
worth starting: the blockers come back as a card to resolve first. If smoke is
clean, the suite is clear to run in full.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from ..models import Run, RunTest, TestCase, TestStatus
from ..models.base import utcnow
from ..models.testing import RunStatus

#: Hard cap: smoke is a gate, not the run. Set well above a healthy smoke's real
#: cost so that a genuinely hung front door still returns a verdict rather than
#: hanging the chat turn.
SMOKE_TIMEOUT = 180.0

#: How each smoke test's type reads as a human "check" in the blockers card.
_CHECK = {
    "functional": "front-door journey",
    "seo": "console & page hygiene",
    "a11y": "accessibility",
    "perf": "performance",
}


def smoke_keys(db, project_id: str, journey) -> list[str]:
    """The built Golden Path tests tagged ``smoke`` for this journey."""
    built = list(journey.test_ids or [])
    if not built:
        return []
    rows = db.execute(
        select(TestCase).where(TestCase.project_id == project_id, TestCase.key.in_(built))
    ).scalars()
    return [tc.key for tc in rows if "smoke" in (tc.tags or [])]


def _approve(db, project_id: str, keys: list[str], actor: str | None) -> None:
    """Approve just the smoke tests, so the keyed selection resolves and runs.

    Only the smoke subset is approved here; the rest of the suite still waits at
    the human gate until the full run is approved."""
    for tc in db.execute(
        select(TestCase).where(TestCase.project_id == project_id, TestCase.key.in_(keys))
    ).scalars():
        if tc.status != TestStatus.APPROVED:
            tc.status = TestStatus.APPROVED
            tc.approved_by = actor
            tc.approved_at = utcnow()
    db.commit()


async def run_smoke(db, *, project, journey, triggered_by: str | None = None,
                    timeout: float = SMOKE_TIMEOUT) -> dict:
    """Run the smoke subset and return a pass/blockers verdict."""
    from .runs import run_task, start_run

    keys = smoke_keys(db, project.id, journey)
    if not keys:
        return {"ok": False, "reason": "no_subset", "target": journey.target,
                "blockers": [], "total": 0, "passed": 0, "failed": 0,
                "message": "No smoke subset yet. Build the tests first, then smoke-check."}

    from .access import run_auth
    _approve(db, project.id, keys, triggered_by)
    run = await start_run(
        db, project_id=project.id, selection={"keys": keys},
        environment=journey.environment, trigger="smoke", triggered_by=triggered_by,
        title=f"Smoke: {journey.target}", command="smoke it", auth=run_auth(journey),
        base_url=journey.target,
        rate_limit_rps=(journey.guardrails or {}).get("rate_limit_rps"),
    )
    task = run_task(run.id)
    timed_out = False
    if task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except TimeoutError:
            timed_out = True

    db.expire_all()
    run = db.get(Run, run.id)
    return _report(db, run, journey, timed_out)


def _check_kind(test_key: str) -> str:
    """Human check name from a GP key, via the shared type helper."""
    from .test_plan import type_from_key
    token = type_from_key(test_key) or "check"
    return _CHECK.get(token, token)


def _report(db, run: Run, journey, timed_out: bool) -> dict:
    results = list(db.execute(select(RunTest).where(RunTest.run_id == run.id)).scalars())
    totals = run.totals or {}
    passed = totals.get("passed", 0)
    failed = totals.get("failed", 0) + totals.get("error", 0)
    blockers = [
        {"title": r.title, "check": _check_kind(r.test_key),
         "detail": (r.error_message or "").strip()[:200] or "failed"}
        for r in results if r.status in (RunStatus.FAILED, RunStatus.ERROR, RunStatus.BLOCKED)
    ]

    base = {"run_id": run.id, "run_number": run.number, "target": journey.target,
            "status": run.status, "total": len(results), "passed": passed,
            "failed": failed, "blockers": blockers}

    if timed_out:
        return {**base, "ok": False, "timed_out": True,
                "message": (f"Smoke is still running past {int(SMOKE_TIMEOUT)}s. The front "
                            "door is slow. Watch it in Runs before starting the full suite.")}

    clear = run.status in (RunStatus.PASSED, RunStatus.FLAKY) and not blockers
    return {
        **base, "ok": clear, "timed_out": False,
        "message": (f"Smoke passed: {passed}/{len(results)} green. The full suite is clear to run."
                    if clear else
                    f"Smoke found {len(blockers)} blocker(s). Resolve these before the full run."),
    }
