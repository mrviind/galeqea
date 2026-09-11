"""Regression delta: what changed since last run.

Given a run and a baseline (the previous run on the same target+env by default, or
one named explicitly), every test is classified against the baseline: a **new
failure**, a **fix**, **still failing**, **new flaky**, or **unchanged**. That is
the read a release manager wants: not "43 passed" but "2 things broke that used to
work". Deterministic; keyed by test key so it survives re-ordering.
"""

from __future__ import annotations

from sqlalchemy import and_, desc, select

from ..models import Run, RunTest
from .test_plan import type_from_key

_PASS = {"passed"}
_FAIL = {"failed", "error"}
_SKIP = {"skipped", "blocked", "cancelled"}

#: Delta classes, in the order a report lists them (worst first).
DELTA_ORDER = ["new_fail", "still_failing", "new_flaky", "fixed", "unchanged"]


def _state(rt: RunTest | None) -> str:
    if rt is None:
        return "absent"
    if rt.status == "flaky":
        return "flaky"
    if rt.status in _PASS:
        return "pass"
    if rt.status in _FAIL:
        return "fail"
    if rt.status in _SKIP:
        return "skip"
    return "other"


def _classify(cur: str, base: str) -> str:
    if cur == "flaky" and base != "flaky":
        return "new_flaky"
    if cur == "fail" and base in ("pass", "absent", "skip"):
        return "new_fail"
    if cur == "pass" and base == "fail":
        return "fixed"
    if cur == "fail" and base in ("fail", "flaky"):
        return "still_failing"
    return "unchanged"


def find_baseline(db, run: Run) -> Run | None:
    """The default comparison run: the previous run of the same trigger on the same
    target and environment."""
    return db.execute(
        select(Run).where(and_(
            Run.project_id == run.project_id, Run.trigger == run.trigger,
            Run.base_url == run.base_url, Run.environment == run.environment,
            Run.number < run.number,
        )).order_by(desc(Run.number)).limit(1)
    ).scalars().first()


def compute_delta(db, run: Run, baseline: Run | None = None) -> dict:
    """Classify every test in ``run`` against ``baseline`` (default: the previous
    run on the same target+env). Returns rows, per-class totals and a one-liner."""
    baseline = baseline or find_baseline(db, run)
    cur = {r.test_key: r for r in db.execute(
        select(RunTest).where(RunTest.run_id == run.id)).scalars()}
    base = {}
    if baseline is not None:
        base = {r.test_key: r for r in db.execute(
            select(RunTest).where(RunTest.run_id == baseline.id)).scalars()}

    rows = []
    # Iterate the *current* run's tests: a partial rerun only makes a claim about
    # what it actually re-ran, so a baseline-only key isn't a "disappeared" delta.
    for key in sorted(cur):
        c, b = cur.get(key), base.get(key)
        delta = _classify(_state(c), _state(b))
        rows.append({
            "key": key, "type": type_from_key(key) or "other",
            "unit": (getattr(c, "title", "") or getattr(b, "title", "")).split(": ")[-1],
            "current": (c.status if c else "n/a"), "baseline": (b.status if b else "n/a"),
            "delta": delta,
        })
    totals = {d: sum(1 for r in rows if r["delta"] == d) for d in DELTA_ORDER}
    return {
        "run_id": run.id, "run_number": run.number,
        "baseline_run_id": baseline.id if baseline else None,
        "baseline_run_number": baseline.number if baseline else None,
        "has_baseline": baseline is not None,
        "totals": totals,
        "what_changed": _one_liner(totals, baseline),
        # The rows worth showing: everything that isn't "unchanged".
        "rows": [r for r in rows if r["delta"] != "unchanged"],
        "unchanged": totals["unchanged"],
    }


def _one_liner(totals: dict, baseline: Run | None) -> str:
    if baseline is None:
        return "No prior run on this target/env, so there is nothing to compare against yet."
    bits = []
    if totals["new_fail"]:
        bits.append(f"{totals['new_fail']} newly failing")
    if totals["fixed"]:
        bits.append(f"{totals['fixed']} fixed")
    if totals["still_failing"]:
        bits.append(f"{totals['still_failing']} still failing")
    if totals["new_flaky"]:
        bits.append(f"{totals['new_flaky']} newly flaky")
    tail = f" vs run #{baseline.number}"
    return (", ".join(bits) + tail) if bits else f"No change vs run #{baseline.number}."
