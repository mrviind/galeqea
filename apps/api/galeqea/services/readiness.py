"""Readiness: the Go / No-Go gate that stands between a run and a release.

Each criterion (no open blockers, plan coverage, flaky rate, Core Web Vitals,
critical/serious accessibility, critical security) is evaluated from the run's own
report (deterministically, no model) and carries its actual value, its threshold,
and an evidence link. The verdict is Go only when every hard criterion passes.

Sign-off is a human act: an AI principal can never satisfy the gate
(``SelfApprovalError``), a No-Go can be signed only with an override note, and the
decision is appended to the hash-chained audit ledger, immutable by construction.
"""

from __future__ import annotations

from ..core import audit
from ..core.approvals import SelfApprovalError
from ..models import JourneyStage, Project, Role
from ..models.base import utcnow

#: Hard thresholds. A project may tighten these; the defaults are release-sane.
_DEFAULT_THRESHOLDS = {"coverage_pct": 80, "flaky_pct": 5}


def _crit(key, label, ok, actual, threshold, *, evidence="", hard=True):
    return {"key": key, "label": label, "pass": bool(ok), "actual": actual,
            "threshold": threshold, "evidence": evidence, "hard": hard}


def evaluate(db, journey, run, *, thresholds: dict | None = None) -> dict:
    """Evaluate every readiness criterion for a run against the plan and the report."""
    from ..reports.runs import build_run_report
    from .triage import triage_board

    project = db.get(Project, run.project_id)
    report = build_run_report(db, project, run)
    by_type = report["by_test_type"]
    summary = report["summary"]
    th = {**_DEFAULT_THRESHOLDS, **(thresholds or {})}
    evidence = f"/runs/{run.id}"

    board = triage_board(db, run, journey)
    open_blockers = board["open_count"]

    plan = (journey.plan if journey else None) or {}
    planned = (plan.get("typed") or {}).get("totals", {}).get("test_count", 0)
    built = len((journey.test_ids if journey else None) or [])
    coverage = round(built / planned * 100) if planned else 100

    total = summary["total"] or 1
    flaky = sum(1 for it in report["results"] if it["status"] == "flaky")
    flaky_pct = round(flaky / total * 100, 1)

    perf_fail = by_type.get("perf", {}).get("failed", 0)
    a11y_fail = by_type.get("a11y", {}).get("failed", 0)  # fail_on = serious+critical
    sec_fail = by_type.get("security", {}).get("failed", 0)

    from .manual import pending_manual
    manual_open = pending_manual(db, run)

    criteria = [
        _crit("blockers", "No open blockers", open_blockers == 0,
              f"{open_blockers} undispositioned", "0", evidence=evidence),
        _crit("manual", "Manual checks executed", manual_open == 0,
              f"{manual_open} unexecuted", "0", evidence=evidence),
        _crit("coverage", f"Plan coverage ≥ {th['coverage_pct']}%", coverage >= th["coverage_pct"],
              f"{coverage}%", f"≥ {th['coverage_pct']}%", evidence=evidence),
        _crit("flaky", f"Flaky ≤ {th['flaky_pct']}%", flaky_pct <= th["flaky_pct"],
              f"{flaky_pct}%", f"≤ {th['flaky_pct']}%", evidence=evidence),
        _crit("perf", "Core Web Vitals within budget", perf_fail == 0,
              f"{perf_fail} over budget", "0", evidence=evidence),
        _crit("a11y", "No critical/serious accessibility", a11y_fail == 0,
              f"{a11y_fail} page(s) with violations", "0", evidence=evidence),
        _crit("security", "No critical security findings", sec_fail == 0,
              f"{sec_fail} finding(s)", "0", evidence=evidence),
    ]
    failed = [c for c in criteria if c["hard"] and not c["pass"]]
    verdict = "go" if not failed else "no_go"
    existing = (run.ci_metadata or {}).get("readiness_signoff")
    return {
        "run_id": run.id, "run_number": run.number,
        "target": journey.target if journey else run.base_url,
        "environment": run.environment, "verdict": verdict, "criteria": criteria,
        "failed_criteria": [c["key"] for c in failed],
        "auth_hint": _auth_hint(db, run),
        "signoff": existing,
    }


def _auth_hint(db, run) -> str | None:
    """A hint (not a criterion) when pages went untested for want of a credential,
    so a missing basic/digest login reads as "add credentials", not a failure."""
    from sqlalchemy import select

    from ..models import RunTest
    kinds: set[str] = set()
    count = 0
    for rt in db.execute(select(RunTest).where(RunTest.run_id == run.id)).scalars():
        if rt.error_type == "auth_gated":
            count += 1
            if "(" in rt.error_message and ")" in rt.error_message:
                kinds.add(rt.error_message[rt.error_message.find("(") + 1:rt.error_message.find(")")])
    if not count:
        return None
    which = " / ".join(sorted(kinds)) if kinds else "the right"
    return f"{count} unit(s) skipped. Add {which} credentials to cover them."


def sign_off(db, journey, run, *, decider, override_note: str | None = None) -> dict:
    """Record a human sign-off. An AI principal can never sign; a No-Go needs an
    override note. The decision lands in the immutable audit ledger."""
    if getattr(decider, "is_machine", False) or decider.role == Role.AGENT:
        raise SelfApprovalError(
            "an AI principal can never sign off a release-readiness gate. "
            "A human must own this decision"
        )
    result = evaluate(db, journey, run)
    is_override = result["verdict"] != "go"
    if is_override and not (override_note and override_note.strip()):
        return {"ok": False, "reason": "override_required",
                "error": "this run is No-Go; signing off anyway requires an override note",
                "readiness": result}

    signoff = {
        "verdict": result["verdict"],
        "signed_by": decider.id,
        "signer_name": decider.name or decider.email,
        "at": utcnow().isoformat(),
        "override": is_override,
        "override_note": (override_note or "").strip() or None,
        "criteria": result["criteria"],
    }
    run.ci_metadata = {**(run.ci_metadata or {}), "readiness_signoff": signoff}
    audit.record(
        db, action="readiness.signed_off", actor_id=decider.id, actor_kind="human",
        actor_label=decider.name or decider.email, project_id=run.project_id,
        resource_type="run", resource_id=run.id,
        detail={"verdict": result["verdict"], "override": is_override,
                "note": signoff["override_note"], "run_number": run.number},
    )
    from .journeys import advance
    advance(db, journey, JourneyStage.READINESS, run_id=run.id)
    db.commit()
    return {"ok": True, "signoff": signoff, "readiness": {**result, "signoff": signoff}}
