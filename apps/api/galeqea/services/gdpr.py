"""GDPR subject-access and erasure.

``export`` gathers everything tied to a user (Article 15 / data portability);
``erase`` anonymises their personal data (Article 17) while keeping the append-only
audit ledger verifiable: the hash chain covers ``actor_id`` but not ``actor_label``,
so the email is scrubbed and the chain stays intact. The erasure itself is audited.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import audit
from ..models import (
    ApiToken,
    ApprovalRequest,
    AuditEvent,
    Journey,
    Run,
    Schedule,
    User,
)
from ..models.base import utcnow


def _rows(db: Session, model, column, value) -> list[dict]:
    out = []
    for r in db.execute(select(model).where(column == value)).scalars():
        out.append({c.name: getattr(r, c.name) for c in model.__table__.columns})
    return out


def export(db: Session, user: User) -> dict:
    """Everything GaleQEA holds about ``user``, as portable JSON-able data."""
    return {
        "generated_at": utcnow().isoformat(),
        "user": {c.name: getattr(user, c.name) for c in User.__table__.columns
                 if c.name != "password_hash"},
        "api_tokens": [{"id": t.id, "name": t.name, "prefix": t.prefix,
                        "scopes": t.scopes, "created_at": t.created_at,
                        "last_used_at": t.last_used_at, "revoked": t.revoked}
                       for t in db.execute(
                           select(ApiToken).where(ApiToken.user_id == user.id)).scalars()],
        "runs_triggered": _rows(db, Run, Run.triggered_by, user.id),
        "schedules_created": _rows(db, Schedule, Schedule.created_by, user.id),
        "journeys_created": _rows(db, Journey, Journey.created_by, user.id),
        "approvals_decided": _rows(db, ApprovalRequest, ApprovalRequest.decided_by, user.id),
        "approvals_requested": _rows(db, ApprovalRequest, ApprovalRequest.requested_by, user.id),
        "audit_events": _rows(db, AuditEvent, AuditEvent.actor_id, user.id),
    }


def erase(db: Session, user: User, *, by: User) -> dict:
    """Anonymise the user's personal data. Returns a summary of what was scrubbed."""
    if user.is_machine:
        raise ValueError("the machine principal cannot be erased")
    if by.id == user.id:
        # Erasing yourself would revoke your own session mid-request; require another admin.
        raise ValueError("erase a user from a different admin account")

    original_email = user.email
    anon = f"erased-{user.id}@deleted.invalid"
    user.email = anon
    user.name = ""
    user.password_hash = ""
    user.preferences = {}
    user.is_active = False

    tokens = db.execute(select(ApiToken).where(ApiToken.user_id == user.id)).scalars().all()
    for t in tokens:
        t.revoked = True

    # Scrub the email from the ledger's actor labels; actor_id and the entry hash
    # are untouched, so the chain still verifies.
    scrubbed = 0
    for ev in db.execute(select(AuditEvent).where(AuditEvent.actor_id == user.id)).scalars():
        if ev.actor_label and ev.actor_label != anon:
            ev.actor_label = anon
            scrubbed += 1

    audit.record(
        db, action="user.erased", actor_id=by.id, actor_kind="human",
        actor_label=by.email, resource_type="user", resource_id=user.id,
        detail={"tokens_revoked": len(tokens), "audit_labels_scrubbed": scrubbed},
    )
    db.flush()
    return {"erased_user_id": user.id, "was_email_domain": original_email.split("@")[-1],
            "tokens_revoked": len(tokens), "audit_labels_scrubbed": scrubbed}
