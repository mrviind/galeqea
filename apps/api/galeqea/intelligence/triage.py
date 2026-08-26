"""Regression triage: separate new breakage from known noise.

A 40-failure run is unreadable. The same run split into "3 new, 31 known, 6
flaky" is actionable in seconds. That split is the difference between a report
someone reads and a report someone mutes.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import FailureSignature, Run, RunStatus, RunTest, TestStat
from ..models.base import utcnow
from . import flaky
from .signatures import classify_error


def triage_run(db: Session, run: Run) -> dict:
    """Classify every failure in a finished run and fold results into statistics."""
    results = list(
        db.execute(select(RunTest).where(RunTest.run_id == run.id)).scalars()
    )

    buckets: dict[str, list[dict]] = {
        "new": [], "known": [], "flaky": [], "environment": [],
        "test_defect": [], "needs_review": [],
    }
    totals = {"total": len(results), "passed": 0, "failed": 0,
              "needs_review": 0, "blocked": 0, "error": 0}

    for result in results:
        totals[_total_key(result.status)] = totals.get(_total_key(result.status), 0) + 1

        stat = flaky.record_outcome(
            db,
            project_id=run.project_id,
            test_case_id=result.test_case_id,
            status=result.status,
            duration_ms=result.duration_ms,
            git_sha=run.git_sha,
            healed=result.healed,
            attempt=result.attempt,
        )

        if result.status == RunStatus.NEEDS_REVIEW:
            buckets["needs_review"].append(_entry(result, "awaiting a human verdict"))
            result.classification = "needs_review"
            continue
        if result.status not in {RunStatus.FAILED, RunStatus.ERROR}:
            continue

        classification, reason = _classify(db, run, result, stat)
        result.classification = classification
        buckets.setdefault(classification, []).append(_entry(result, reason))

    signature_summary = _update_signatures(db, run, results)
    db.flush()

    ordered_actionable = buckets["new"] + buckets["test_defect"]
    return {
        "totals": totals,
        "new": buckets["new"],
        "known": buckets["known"],
        "flaky": buckets["flaky"],
        "environment": buckets["environment"],
        "test_defect": buckets["test_defect"],
        "needs_review": buckets["needs_review"],
        "blocked": totals.get("blocked", 0),
        "signatures": signature_summary,
        # What a human should look at first, in order.
        "headline": _headline(buckets, totals),
        "action_queue": [e["key"] for e in ordered_actionable][:10],
    }


def _total_key(status: str) -> str:
    return {
        RunStatus.PASSED: "passed",
        RunStatus.FAILED: "failed",
        RunStatus.ERROR: "error",
        RunStatus.NEEDS_REVIEW: "needs_review",
        RunStatus.BLOCKED: "blocked",
    }.get(status, "failed")


def _entry(result: RunTest, reason: str) -> dict:
    return {
        "run_test_id": result.id,
        "test_case_id": result.test_case_id,
        "key": result.test_key,
        "title": result.title,
        "signature": result.failure_signature,
        "error": (result.error_message or "")[:300],
        "reason": reason,
    }


def _classify(db: Session, run: Run, result: RunTest, stat: TestStat) -> tuple[str, str]:
    """Order matters: cheapest and most certain checks first."""
    coarse = classify_error(result.error_type, result.error_message)
    if coarse == "environment":
        return "environment", "error signature indicates infrastructure, not the product"
    if coarse == "test_defect":
        return "test_defect", "the test could not locate what it needed - likely a test-side defect"

    verdict = flaky.assess(stat)
    if verdict.score >= 0.5 and verdict.confidence >= 0.5:
        return "flaky", f"historically unstable: {verdict.reasons[0]}"

    if result.failure_signature:
        known = db.execute(
            select(FailureSignature).where(
                FailureSignature.project_id == run.project_id,
                FailureSignature.signature == result.failure_signature,
            )
        ).scalar_one_or_none()
        if known and known.occurrences > 0:
            if known.muted_until and known.muted_until > utcnow():
                return "known", f"muted known issue{f' ({known.ticket_ref})' if known.ticket_ref else ''}"
            if known.known_issue:
                return "known", f"tracked known issue {known.ticket_ref or ''}".strip()
            return "known", f"same failure seen {known.occurrences}x before (first {known.first_seen_at:%Y-%m-%d})"

    return "new", "failure signature has not been seen in this project before"


def _update_signatures(db: Session, run: Run, results: list[RunTest]) -> list[dict]:
    summary: list[dict] = []
    for result in results:
        if not result.failure_signature:
            continue
        record = db.execute(
            select(FailureSignature).where(
                FailureSignature.project_id == run.project_id,
                FailureSignature.signature == result.failure_signature,
            )
        ).scalar_one_or_none()
        if record is None:
            record = FailureSignature(
                project_id=run.project_id,
                signature=result.failure_signature,
                label=(result.error_message or "")[:200],
                normalized_message=(result.error_message or "")[:600],
                error_type=result.error_type,
                first_seen_at=utcnow(),
                occurrences=0,
                affected_tests=[],
            )
            db.add(record)
        record.occurrences += 1
        record.last_seen_at = utcnow()
        if result.test_key and result.test_key not in (record.affected_tests or []):
            record.affected_tests = [*(record.affected_tests or []), result.test_key]
        summary.append({
            "signature": record.signature,
            "label": record.label,
            "occurrences": record.occurrences,
            "affected": len(record.affected_tests or []),
            "known_issue": record.known_issue,
        })
    return summary


def _headline(buckets: dict[str, list], totals: dict) -> str:
    if totals.get("failed", 0) == 0 and totals.get("error", 0) == 0:
        if totals.get("needs_review"):
            return f"All executed tests passed; {totals['needs_review']} need a human verdict."
        return "All tests passed."
    parts: list[str] = []
    if buckets["new"]:
        parts.append(f"{len(buckets['new'])} new")
    if buckets["known"]:
        parts.append(f"{len(buckets['known'])} known")
    if buckets["flaky"]:
        parts.append(f"{len(buckets['flaky'])} flaky")
    if buckets["environment"]:
        parts.append(f"{len(buckets['environment'])} environmental")
    if buckets["test_defect"]:
        parts.append(f"{len(buckets['test_defect'])} test-side")
    lead = ", ".join(parts)
    focus = (
        f" Start with {buckets['new'][0]['key']}." if buckets["new"] else
        " Nothing new broke - the failures are already accounted for."
    )
    return f"{lead}.{focus}"


def mark_known_issue(
    db: Session, *, project_id: str, signature: str, ticket_ref: str = "", muted_days: int = 0
) -> FailureSignature | None:
    from datetime import timedelta

    record = db.execute(
        select(FailureSignature).where(
            FailureSignature.project_id == project_id,
            FailureSignature.signature == signature,
        )
    ).scalar_one_or_none()
    if record is None:
        return None
    record.known_issue = True
    record.ticket_ref = ticket_ref
    if muted_days:
        record.muted_until = utcnow() + timedelta(days=muted_days)
    db.flush()
    return record
