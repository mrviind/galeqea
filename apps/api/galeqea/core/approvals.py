"""The human approval gate.

Nothing an agent proposes reaches durable state directly. A proposal becomes an
``ApprovalRequest`` carrying a reviewable diff plus its evidence; a human with a
sufficient role decides; only then does the registered applier run.

Two rules are enforced here and cannot be configured away:

* ``AI_CANNOT_SELF_APPROVE`` - the decider must be a human principal, and must
  not be the principal that requested the change.
* ``APPLY_REQUIRES_APPROVED`` - the applier is unreachable from any other state.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ApprovalMode, settings
from ..models import (
    ApprovalBatch,
    ApprovalRequest,
    ApprovalStatus,
    RiskTier,
    Role,
    User,
)
from ..models.base import new_id, utcnow
from . import audit

# --------------------------------------------------------------------------- #
# Applier registry
# --------------------------------------------------------------------------- #
Applier = Callable[[Session, ApprovalRequest], dict]
_APPLIERS: dict[str, Applier] = {}

#: Minimum role that may decide an action at each risk tier.
RISK_MIN_ROLE: dict[str, Role] = {
    RiskTier.LOW: Role.AUTHOR,
    RiskTier.MEDIUM: Role.APPROVER,
    RiskTier.HIGH: Role.APPROVER,
    RiskTier.CRITICAL: Role.ADMIN,
}

#: Default risk classification per action. Anything unlisted is treated as HIGH,
#: so a newly added action fails closed rather than open.
ACTION_RISK: dict[str, RiskTier] = {
    "test.create": RiskTier.MEDIUM,
    "test.update": RiskTier.MEDIUM,
    "test.approve": RiskTier.MEDIUM,
    "test.delete": RiskTier.HIGH,
    "test.generate_script": RiskTier.MEDIUM,
    "suite.update": RiskTier.LOW,
    "heal.apply": RiskTier.MEDIUM,
    "baseline.update": RiskTier.MEDIUM,
    "memory.write": RiskTier.LOW,
    "schedule.create": RiskTier.MEDIUM,
    "run.start": RiskTier.LOW,
    "jira.create_issue": RiskTier.HIGH,
    "xray.push_results": RiskTier.HIGH,
    "git.commit": RiskTier.HIGH,
    "git.open_pr": RiskTier.HIGH,
    "ci.trigger": RiskTier.HIGH,
    "integration.connect": RiskTier.HIGH,
    "secret.write": RiskTier.CRITICAL,
    "plugin.install": RiskTier.CRITICAL,
    "plugin.enable": RiskTier.CRITICAL,
    "user.role_change": RiskTier.CRITICAL,
    "data.delete": RiskTier.CRITICAL,
}


_appliers_loaded = False


def _ensure_appliers() -> None:
    """Guarantee the applier registry is populated before it is consulted.

    Appliers live next to the tools that file them, in ``ai.toolset``. Relying on
    that module happening to be imported first would make the gate's behaviour
    depend on import order - the failure mode being an approved change that
    cannot be applied. The import is deferred to call time because ``toolset``
    imports this module for the decorator.
    """
    global _appliers_loaded
    if _appliers_loaded:
        return
    _appliers_loaded = True
    from ..ai import toolset  # noqa: F401


def applier(action: str) -> Callable[[Applier], Applier]:
    """Register the function that performs ``action`` once it is approved."""

    def decorator(fn: Applier) -> Applier:
        _APPLIERS[action] = fn
        return fn

    return decorator


def registered_actions() -> list[str]:
    _ensure_appliers()
    return sorted(_APPLIERS)


class ApprovalError(RuntimeError):
    pass


class SelfApprovalError(ApprovalError):
    """Raised when a non-human, or the requester, tries to satisfy the gate."""


@dataclass(slots=True)
class Decision:
    request: ApprovalRequest
    applied: bool
    result: dict


# --------------------------------------------------------------------------- #
# Requesting
# --------------------------------------------------------------------------- #
def request(
    db: Session,
    *,
    action: str,
    title: str,
    project_id: str | None = None,
    resource_type: str = "",
    resource_id: str | None = None,
    summary: str = "",
    payload: dict | None = None,
    diff: dict | None = None,
    evidence: dict | None = None,
    requested_by: str | None = None,
    requested_by_kind: str = "agent",
    agent_role: str = "",
    trace_id: str | None = None,
    batch_id: str | None = None,
    risk: RiskTier | None = None,
    ttl_hours: int = 72,
) -> ApprovalRequest:
    """Queue a write for human review. Returns the pending request."""
    _ensure_appliers()
    if action not in _APPLIERS:
        raise ApprovalError(
            f"no applier registered for action {action!r}; "
            f"known actions: {', '.join(registered_actions()) or '(none)'}"
        )

    tier = risk or ACTION_RISK.get(action, RiskTier.HIGH)
    req = ApprovalRequest(
        project_id=project_id,
        batch_id=batch_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        title=title,
        summary=summary,
        payload=payload or {},
        diff=diff or {},
        evidence=evidence or {},
        risk=tier,
        required_role=RISK_MIN_ROLE.get(tier, Role.APPROVER).value,
        requested_by=requested_by,
        requested_by_kind=requested_by_kind,
        agent_role=agent_role,
        trace_id=trace_id,
        status=ApprovalStatus.PENDING,
        expires_at=utcnow() + timedelta(hours=ttl_hours),
    )
    db.add(req)
    db.flush()

    audit.record(
        db,
        action="approval.requested",
        actor_id=requested_by,
        actor_kind=requested_by_kind,
        actor_label=agent_role or requested_by_kind,
        project_id=project_id,
        resource_type="approval_request",
        resource_id=req.id,
        detail={"action": action, "risk": tier, "title": title},
        approval_id=req.id,
    )
    return req


def open_batch(
    db: Session, *, title: str, project_id: str | None = None, summary: str = ""
) -> ApprovalBatch:
    batch = ApprovalBatch(
        id=new_id("bat"), project_id=project_id, title=title, summary=summary
    )
    db.add(batch)
    db.flush()
    return batch


# --------------------------------------------------------------------------- #
# Deciding
# --------------------------------------------------------------------------- #
def _assert_can_decide(db: Session, req: ApprovalRequest, decider: User) -> None:
    """The two invariants. Deliberately not configurable."""
    if decider.is_machine or decider.role == Role.AGENT:
        raise SelfApprovalError(
            "an AI principal can never satisfy an approval gate - "
            "a human reviewer must decide this request"
        )
    if req.requested_by and req.requested_by == decider.id:
        raise SelfApprovalError(
            "the principal that proposed this change cannot also approve it"
        )
    if settings.allow_ai_self_approval:  # config may only ever make this stricter
        raise ApprovalError(
            "GALEQEA_ALLOW_AI_SELF_APPROVAL is set but self-approval is "
            "structurally prohibited; unset it"
        )
    required = Role(req.required_role)
    if not decider.at_least(required):
        actual = decider.role.value if hasattr(decider.role, "value") else decider.role
        raise ApprovalError(
            f"{req.risk} risk action requires role '{required.value}' or above; "
            f"'{actual}' is insufficient"
        )
    if req.status != ApprovalStatus.PENDING:
        raise ApprovalError(f"request is already {req.status}")
    if req.expires_at and req.expires_at < utcnow():
        req.status = ApprovalStatus.EXPIRED
        db.flush()
        raise ApprovalError("request has expired; ask the agent to re-propose it")


def approve(
    db: Session, request_id: str, decider: User, *, comment: str = "", apply_now: bool = True
) -> Decision:
    req = db.get(ApprovalRequest, request_id)
    if not req:
        raise ApprovalError(f"unknown approval request {request_id}")
    _assert_can_decide(db, req, decider)

    req.status = ApprovalStatus.APPROVED
    req.decided_by = decider.id
    req.decided_at = utcnow()
    req.decision_comment = comment
    db.flush()

    audit.record(
        db,
        action="approval.approved",
        actor_id=decider.id,
        actor_kind="human",
        actor_label=decider.email,
        project_id=req.project_id,
        resource_type="approval_request",
        resource_id=req.id,
        detail={"action": req.action, "risk": req.risk, "comment": comment},
        approval_id=req.id,
    )

    if not apply_now:
        return Decision(request=req, applied=False, result={})

    result = _apply(db, req, decider)
    return Decision(request=req, applied=req.status == ApprovalStatus.APPLIED, result=result)


def reject(db: Session, request_id: str, decider: User, *, comment: str = "") -> ApprovalRequest:
    req = db.get(ApprovalRequest, request_id)
    if not req:
        raise ApprovalError(f"unknown approval request {request_id}")
    _assert_can_decide(db, req, decider)

    req.status = ApprovalStatus.REJECTED
    req.decided_by = decider.id
    req.decided_at = utcnow()
    req.decision_comment = comment
    db.flush()

    audit.record(
        db,
        action="approval.rejected",
        actor_id=decider.id,
        actor_kind="human",
        actor_label=decider.email,
        project_id=req.project_id,
        resource_type="approval_request",
        resource_id=req.id,
        detail={"action": req.action, "comment": comment},
        approval_id=req.id,
    )
    return req


def decide_batch(
    db: Session, batch_id: str, decider: User, *, approved: bool, comment: str = ""
) -> list[Decision]:
    """Gated-workflow mode: one human decision covers every item in the batch."""
    batch = db.get(ApprovalBatch, batch_id)
    if not batch:
        raise ApprovalError(f"unknown batch {batch_id}")
    reqs = list(
        db.execute(
            select(ApprovalRequest).where(
                ApprovalRequest.batch_id == batch_id,
                ApprovalRequest.status == ApprovalStatus.PENDING,
            )
        ).scalars()
    )
    decisions: list[Decision] = []
    for req in reqs:
        if approved:
            decisions.append(approve(db, req.id, decider, comment=comment))
        else:
            reject(db, req.id, decider, comment=comment)
            decisions.append(Decision(request=req, applied=False, result={}))

    batch.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
    batch.decided_by = decider.id
    batch.decided_at = utcnow()
    batch.item_count = len(reqs)
    db.flush()
    return decisions


def _apply(db: Session, req: ApprovalRequest, decider: User) -> dict:
    if req.status != ApprovalStatus.APPROVED:
        raise ApprovalError("refusing to apply a request that is not approved")
    _ensure_appliers()
    fn = _APPLIERS.get(req.action)
    if fn is None:
        raise ApprovalError(f"no applier registered for {req.action!r}")
    try:
        result = fn(db, req) or {}
    except Exception as exc:  # noqa: BLE001
        req.apply_error = f"{type(exc).__name__}: {exc}"
        db.flush()
        audit.record(
            db,
            action="approval.apply_failed",
            actor_id=decider.id,
            actor_kind="human",
            project_id=req.project_id,
            resource_type="approval_request",
            resource_id=req.id,
            outcome="failure",
            detail={"action": req.action, "error": req.apply_error},
            approval_id=req.id,
        )
        raise

    req.status = ApprovalStatus.APPLIED
    req.applied_at = utcnow()
    db.flush()
    audit.record(
        db,
        action="approval.applied",
        actor_id=decider.id,
        actor_kind="human",
        actor_label=decider.email,
        project_id=req.project_id,
        resource_type=req.resource_type or "approval_request",
        resource_id=req.resource_id or req.id,
        detail={"action": req.action, "result": result},
        approval_id=req.id,
    )
    return result


# --------------------------------------------------------------------------- #
# Mode helpers
# --------------------------------------------------------------------------- #
def gate_required(action: str, *, actor_kind: str) -> bool:
    """Whether ``action`` must pass the gate under the current approval mode.

    A human acting on their own low-risk resources is not gated; an agent always
    is, at every tier. ``AUTO_LOW_RISK`` relaxes only the LOW tier and only for
    reversible, local actions - it never covers anything that leaves the machine.
    """
    tier = ACTION_RISK.get(action, RiskTier.HIGH)
    if actor_kind == "human" and tier == RiskTier.LOW:
        return False
    if settings.approval_mode == ApprovalMode.AUTO_LOW_RISK and tier == RiskTier.LOW:
        return False
    return True


def pending_for_project(db: Session, project_id: str) -> list[ApprovalRequest]:
    return list(
        db.execute(
            select(ApprovalRequest)
            .where(
                ApprovalRequest.project_id == project_id,
                ApprovalRequest.status == ApprovalStatus.PENDING,
            )
            .order_by(ApprovalRequest.created_at.desc())
        ).scalars()
    )
