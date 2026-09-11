"""Rerun: re-run a slice of the suite as a child run, and show what changed.

The slice can be the previous run's failures, a type suite, the tests touching a
path, or an explicit set of keys. The child run links to its parent, and the
result comes back as a regression delta against that parent: what newly broke,
what got fixed, what's still failing. Deterministic; no model.
"""

from __future__ import annotations

from sqlalchemy import select

from ..models import Run, RunTest, TestCase, TestCategory


def _parent_run(db, journey) -> Run | None:
    return db.get(Run, journey.run_id) if journey.run_id else None


def select_keys(db, project_id: str, journey, spec: dict) -> list[str]:
    """Resolve a rerun spec into concrete test keys.

    spec.mode: 'failed' (parent's failures) | 'suite' (a type) | 'path' (unit path
    prefix) | 'keys' (explicit) | 'all' (the whole built suite)."""
    mode = spec.get("mode", "failed")
    built = [k for k in (journey.test_ids or [])]
    if mode == "keys":
        return [k for k in spec.get("keys", []) if k in built]
    if mode == "failed":
        parent = _parent_run(db, journey)
        if parent is None:
            return []
        return [r.test_key for r in db.execute(
            select(RunTest).where(RunTest.run_id == parent.id)).scalars()
            if r.status in ("failed", "error")]
    if mode == "suite":
        want = spec.get("type", "")
        return [k for k in built if f"-GP-{want.upper().replace('_', '')}-" in k]
    if mode == "path":
        from urllib.parse import urlparse
        prefix = spec.get("path", "")
        rows = db.execute(select(TestCase).where(
            TestCase.project_id == project_id, TestCase.key.in_(built))).scalars()
        out = []
        for tc in rows:
            prov = tc.provenance or {}
            if prov.get("unit", "").startswith(prefix) or urlparse(prov.get("page", "")).path.startswith(prefix):
                out.append(tc.key)
        return out
    if mode == "all":
        rows = db.execute(select(TestCase).where(
            TestCase.project_id == project_id, TestCase.key.in_(built))).scalars()
        return [tc.key for tc in rows if tc.category == TestCategory.AUTOMATED]
    return []


async def rerun_x3(db, *, project, journey, signature: str, actor: str | None = None,
                   attempts: int = 3, timeout: float = 180.0) -> dict:
    """Confirm-or-clear a triage group by re-running its tests in isolation N times.

    A test that passes in *any* isolated attempt was flaky, not genuinely broken:
    its result on the original run is flipped to ``flaky`` (so triage stops calling
    it an open failure and the readiness flaky rate reflects it), and quarantine is
    suggested. A test that fails every attempt hardens confidence in its class."""
    from ..models.base import utcnow
    from .golden_run import run_full  # noqa: F401 - not used, kept for symmetry
    from .runs import start_run, wait_for_run
    from .triage import _signature as sig_of
    from .triage import triage_board

    parent = _parent_run(db, journey)
    if parent is None:
        return {"ok": False, "message": "no run to rerun"}
    affected = [r for r in db.execute(
        select(RunTest).where(RunTest.run_id == parent.id)).scalars()
        if sig_of(r) == signature]
    keys = sorted({r.test_key for r in affected})
    if not keys:
        return {"ok": False, "message": "no tests in that group"}

    # A human label for the group, never the signature hash in a run title.
    group = next((g for g in triage_board(db, parent, journey)["groups"]
                  if g["signature"] == signature), None)
    label = group["label"] if group else "failure group"

    passed_any: set[str] = set()
    child_numbers: list[int] = []
    from .access import run_auth
    for i in range(attempts):
        run = await start_run(
            db, project_id=project.id, selection={"keys": keys},
            environment=journey.environment, trigger="rerun", triggered_by=actor,
            title=f"Rerun {i + 1}/{attempts}: {label} ({len(keys)} test(s))",
            command="rerun ×3", parent_run_id=parent.id, auth=run_auth(journey),
            base_url=journey.target, rerun_kind="x3", attempt=i + 1,
            rate_limit_rps=(journey.guardrails or {}).get("rate_limit_rps"))
        child_numbers.append(run.number)
        await wait_for_run(run.id, timeout=timeout)
        db.expire_all()
        for r in db.execute(select(RunTest).where(RunTest.run_id == run.id)).scalars():
            if r.status in ("passed", "flaky"):
                passed_any.add(r.test_key)

    # Flip the flaky ones on the ORIGINAL run so triage and readiness update.
    flipped = sorted(passed_any & set(keys))
    for r in affected:
        if r.test_key in flipped:
            r.status = "flaky"
            r.classification = "flaky"
    for key in flipped:
        tc = db.execute(select(TestCase).where(
            TestCase.project_id == project.id, TestCase.key == key)).scalar_one_or_none()
        if tc is not None:
            tc.flake_score = min(1.0, (tc.flake_score or 0.0) + 0.34)
    triage = dict(parent.triage or {})
    reruns = dict(triage.get("reruns", {}))
    reruns[signature] = {
        "attempts": attempts, "child_runs": child_numbers, "flipped": flipped,
        "verdict": "flaky" if flipped else "confirmed",
        "confidence": "high" if len(keys) == len(flipped) or not flipped else "medium",
        "at": utcnow().isoformat(), "by": actor,
    }
    triage["reruns"] = reruns
    parent.triage = triage
    db.commit()
    return {"ok": True, "flipped": flipped, "confirmed": not flipped,
            "child_runs": child_numbers,
            "verdict": "flaky" if flipped else "confirmed",
            "suggest_quarantine": bool(flipped),
            "board": triage_board(db, parent, journey)}


async def rerun(db, *, project, journey, spec: dict, triggered_by: str | None = None,
                timeout: float = 240.0) -> dict:
    """Run the selected slice as a child of the current run; return the delta."""
    from .access import run_auth
    from .golden_run import run_board
    from .regression import compute_delta
    from .runs import start_run, wait_for_run

    parent = _parent_run(db, journey)
    keys = select_keys(db, project.id, journey, spec)
    if not keys:
        return {"ok": False, "reason": "empty_selection",
                "message": "Nothing matched that rerun: no failures, or no tests on that path."}

    run = await start_run(
        db, project_id=project.id, selection={"keys": keys},
        environment=journey.environment, trigger="golden_path", triggered_by=triggered_by,
        title=f"Rerun ({spec.get('mode')}): {journey.target}",
        command=spec.get("command", "rerun"), parent_run_id=parent.id if parent else None,
        auth=run_auth(journey), base_url=journey.target,
        rerun_kind=spec.get("mode", "failed"),
        rate_limit_rps=(journey.guardrails or {}).get("rate_limit_rps"),
    )
    await wait_for_run(run.id, timeout=timeout)
    db.expire_all()
    run = db.get(Run, run.id)
    delta = compute_delta(db, run, parent)
    return {"ok": True, "run_id": run.id, "run_number": run.number,
            "count": len(keys), "delta": delta, "board": run_board(db, run, journey)}
