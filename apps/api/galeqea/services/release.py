"""Release management operations: the verbs behind the test-manager cards.

Deterministic and gate-respecting: creating objects is a plain write; a milestone
sign-off is a human-only, audited, immutable act (an AI principal can never sign).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import audit
from ..core.approvals import SelfApprovalError
from ..models import (
    Cycle,
    CycleStatus,
    Environment,
    Milestone,
    MilestoneStatus,
    Project,
    Role,
    TestCase,
    TestPlan,
    TestStatus,
    User,
)
from ..models.base import utcnow
from . import release_metrics

_OPS = {
    ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b, "<": lambda a, b: a < b,
    "==": lambda a, b: a == b, "=": lambda a, b: a == b,
}


# --------------------------------------------------------------------------- #
# Milestones & environments
# --------------------------------------------------------------------------- #
def create_milestone(db: Session, project: Project, *, name: str, version: str,
                     target_date: datetime | None = None) -> Milestone:
    # A one-letter name is almost always a mis-parse; default to "Release <version>".
    if not name or len(name.strip()) < 2:
        name = f"Release {version}"
    m = Milestone(project_id=project.id, name=name.strip(), version=version,
                  target_date=target_date, status=MilestoneStatus.ACTIVE)
    db.add(m)
    db.flush()
    return m


def milestone_for(db: Session, project_id: str, version: str) -> Milestone | None:
    """The current (non-archived) milestone for a version, newest first."""
    return db.execute(
        select(Milestone).where(Milestone.project_id == project_id,
                                Milestone.version == version,
                                Milestone.status != MilestoneStatus.ARCHIVED)
        .order_by(Milestone.created_at.desc())
    ).scalars().first()


def archive_milestone(db: Session, milestone: Milestone) -> Milestone:
    milestone.status = MilestoneStatus.ARCHIVED
    db.flush()
    return milestone


def delete_milestone(db: Session, milestone: Milestone) -> None:
    db.delete(milestone)
    db.flush()


def set_exit_criteria(db: Session, milestone: Milestone, rules: list[dict]) -> Milestone:
    milestone.exit_criteria = rules
    db.flush()
    return milestone


def add_environment(db: Session, project: Project, *, name: str, base_url: str,
                    build_label: str = "", credentials_ref: str | None = None,
                    tags: list | None = None) -> Environment:
    env = Environment(project_id=project.id, name=name, base_url=base_url,
                      build_label=build_label, credentials_ref=credentials_ref,
                      tags=tags or [])
    db.add(env)
    db.flush()
    return env


# --------------------------------------------------------------------------- #
# Plans & cycles
# --------------------------------------------------------------------------- #
def resolve_selection(db: Session, project_id: str, selection: dict) -> list[TestCase]:
    """The approved cases a selection resolves to: static ids or a saved query."""
    q = select(TestCase).where(TestCase.project_id == project_id,
                               TestCase.status == TestStatus.APPROVED)
    ids = selection.get("ids")
    if ids:
        return list(db.execute(q.where(TestCase.key.in_(ids) | TestCase.id.in_(ids))).scalars())
    query = selection.get("query") or {}
    cases = list(db.execute(q).scalars())
    if query.get("type"):
        cases = [c for c in cases if (c.provenance or {}).get("type") == query["type"]]
    if query.get("risk"):
        cases = [c for c in cases if c.risk == query["risk"]]
    tags = query.get("tags")
    if tags:
        cases = [c for c in cases if set(tags) & set(c.tags or [])]
    return cases


def create_plan(db: Session, project: Project, *, milestone: Milestone | None, name: str,
                selection: dict, configurations: list[dict]) -> tuple[TestPlan, int]:
    plan = TestPlan(project_id=project.id, milestone_id=milestone.id if milestone else None,
                    name=name, selection=selection, configurations=configurations)
    db.add(plan)
    db.flush()
    return plan, len(resolve_selection(db, project.id, selection))


def start_cycles(db: Session, plan: TestPlan) -> list[Cycle]:
    """Materialise one Cycle per configuration, pinning each case's current version."""
    cases = resolve_selection(db, plan.project_id, plan.selection)
    pinned = [{"test_case_id": c.id, "key": c.key, "version": c.version} for c in cases]
    cycles: list[Cycle] = []
    for cfg in (plan.configurations or [{"browser": "chromium", "viewport": "desktop"}]):
        env_id = cfg.get("env")
        env = db.get(Environment, env_id) if env_id else None
        label = f"{plan.name} · {cfg.get('browser', 'chromium')}/{cfg.get('viewport', 'desktop')}"
        cycle = Cycle(project_id=plan.project_id, plan_id=plan.id,
                      milestone_id=plan.milestone_id,
                      environment_id=env.id if env else None,
                      name=label, configuration=cfg, status=CycleStatus.ACTIVE,
                      pinned_cases=pinned,
                      counters={"planned": len(pinned), "executed": 0, "passed": 0, "failed": 0})
        db.add(cycle)
        cycles.append(cycle)
    db.flush()
    return cycles


def roll_up_cycle(db: Session, cycle: Cycle) -> bool:
    """Recompute a cycle's counters from its runs' latest per-case results, and mark
    it COMPLETE when every pinned case has a terminal result. Returns True only on the
    transition into COMPLETE, so the caller fires ``cycle.finished`` exactly once."""
    from ..models import Run, RunTest
    from ..models.testing import TERMINAL_RUN_STATES

    pinned_ids = {c.get("test_case_id") for c in (cycle.pinned_cases or []) if c.get("test_case_id")}
    planned = len(pinned_ids) or len(cycle.pinned_cases or [])

    # Latest terminal result per test case across every run in this cycle.
    rows = db.execute(
        select(RunTest).join(Run, RunTest.run_id == Run.id)
        .where(Run.cycle_id == cycle.id)
        .order_by(RunTest.finished_at.is_(None), RunTest.finished_at.desc())
    ).scalars()
    latest: dict[str, str] = {}
    for rt in rows:
        if rt.status not in TERMINAL_RUN_STATES:
            continue
        if pinned_ids and rt.test_case_id not in pinned_ids:
            continue
        latest.setdefault(rt.test_case_id, rt.status)

    counters = {"planned": planned, "executed": len(latest),
                "passed": 0, "failed": 0, "blocked": 0, "skipped": 0}
    for status in latest.values():
        if status in ("passed", "flaky"):
            counters["passed"] += 1
        elif status in ("failed", "error", "needs_review"):
            counters["failed"] += 1
        elif status == "blocked":
            counters["blocked"] += 1
        elif status in ("skipped", "cancelled"):
            counters["skipped"] += 1
    cycle.counters = counters

    just_finished = (cycle.status == CycleStatus.ACTIVE and planned > 0
                     and counters["executed"] >= planned)
    if just_finished:
        cycle.status = CycleStatus.COMPLETE
    db.flush()
    return just_finished


# --------------------------------------------------------------------------- #
# Readiness & sign-off
# --------------------------------------------------------------------------- #
def evaluate_readiness(db: Session, milestone: Milestone) -> dict:
    """Evaluate each exit-criterion against live metrics → Go/No-Go with evidence."""
    metrics = release_metrics.compute(db, milestone)
    criteria = []
    all_met = True
    for rule in (milestone.exit_criteria or []):
        metric = rule.get("metric")
        op = rule.get("op", ">=")
        target = rule.get("value")
        actual = metrics.get(metric)
        met = actual is not None and op in _OPS and _OPS[op](actual, target)
        all_met = all_met and met
        criteria.append({"metric": metric, "op": op, "target": target,
                         "actual": actual, "met": bool(met)})
    verdict = "go" if (all_met and (milestone.exit_criteria or [])) else "no_go"
    return {"version": milestone.version, "verdict": verdict, "criteria": criteria,
            "metrics": metrics, "signed_off": bool(milestone.signoff)}


def sign_off(db: Session, milestone: Milestone, *, decider: User, decision: str,
             note: str = "") -> Milestone:
    """Immutable, human-only, audited milestone sign-off. An AI principal can never
    sign; an approver-or-above human must own the decision."""
    if getattr(decider, "is_machine", False) or decider.role == Role.AGENT:
        raise SelfApprovalError("an AI principal can never sign off a release. "
                                "A human must own this decision")
    if not decider.at_least(Role.APPROVER):
        raise SelfApprovalError("signing off a release requires the approver role or above")
    if milestone.signoff:
        raise ValueError(f"{milestone.version} is already signed off. The decision is immutable")
    if decision not in ("go", "no_go"):
        raise ValueError("decision must be 'go' or 'no_go'")
    # Signing GO while readiness is NO-GO is an override. It must carry a note.
    override = decision == "go" and evaluate_readiness(db, milestone)["verdict"] == "no_go"
    if override and not note.strip():
        raise ValueError("readiness is NO-GO, and a GO sign-off is an override that requires a note")

    milestone.signoff = {"by": decider.email, "by_id": decider.id,
                         "at": utcnow().isoformat(), "decision": decision,
                         "note": note, "override": override}
    if decision == "go":
        milestone.status = MilestoneStatus.RELEASED
    db.flush()
    audit.record(db, action="milestone.signed_off", actor_id=decider.id, actor_kind="human",
                 actor_label=decider.email, project_id=milestone.project_id,
                 resource_type="milestone", resource_id=milestone.id,
                 detail={"version": milestone.version, "decision": decision, "note": note})
    return milestone


# --------------------------------------------------------------------------- #
# Card serialisers (the deterministic chat blocks)
# --------------------------------------------------------------------------- #
def milestone_card(m: Milestone) -> dict:
    return {"type": "milestone_card", "id": m.id, "name": m.name, "version": m.version,
            "status": m.status, "target_date": m.target_date.isoformat() if m.target_date else None,
            "exit_criteria": m.exit_criteria, "signoff": m.signoff or None}


def environment_card(e: Environment) -> dict:
    return {"type": "environment_card", "id": e.id, "name": e.name, "base_url": e.base_url,
            "build_label": e.build_label, "tags": e.tags}


def plan_card(plan: TestPlan, case_count: int) -> dict:
    return {"type": "plan_card", "id": plan.id, "name": plan.name,
            "milestone_id": plan.milestone_id, "case_count": case_count,
            "configurations": plan.configurations,
            "matrix_size": case_count * max(len(plan.configurations or [1]), 1)}


def cycle_card(c: Cycle) -> dict:
    return {"type": "cycle_card", "id": c.id, "name": c.name, "plan_id": c.plan_id,
            "configuration": c.configuration, "status": c.status,
            "counters": c.counters, "case_count": len(c.pinned_cases or [])}


def readiness_card(evaluation: dict) -> dict:
    return {"type": "release_readiness_card", **evaluation}
