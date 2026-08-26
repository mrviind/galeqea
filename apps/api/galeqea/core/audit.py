"""Append-only, hash-chained audit ledger.

Each entry commits to its predecessor, so the ledger is verifiable end to end:
if any row is edited or removed, verification fails *at that row* and every
later row is reported as orphaned. This is what turns "we log things" into
"we can prove what happened", which is the point of the approval gate.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuditEvent
from ..models.base import utcnow

GENESIS = "0" * 64


def _canonical(payload: dict[str, Any]) -> bytes:
    """Deterministic serialisation - key order and separators must never drift."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


def compute_entry_hash(
    *,
    prev_hash: str,
    created_at: datetime,
    actor_id: str | None,
    actor_kind: str,
    action: str,
    resource_type: str,
    resource_id: str | None,
    outcome: str,
    detail: dict,
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "prev": prev_hash,
                "ts": created_at.isoformat(),
                "actor": actor_id,
                "actor_kind": actor_kind,
                "action": action,
                "rtype": resource_type,
                "rid": resource_id,
                "outcome": outcome,
                "detail": detail,
            }
        )
    ).hexdigest()


def record(
    db: Session,
    *,
    action: str,
    actor_id: str | None = None,
    actor_kind: str = "human",
    actor_label: str = "",
    project_id: str | None = None,
    resource_type: str = "",
    resource_id: str | None = None,
    outcome: str = "success",
    detail: dict | None = None,
    model_trace: dict | None = None,
    approval_id: str | None = None,
    ip_address: str = "",
) -> AuditEvent:
    """Append one entry. Callers must never set hashes themselves."""
    detail = detail or {}
    prev = db.execute(
        select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(1)
    ).scalar_one_or_none()
    prev_hash = prev.entry_hash if prev else GENESIS
    created_at = utcnow()

    entry = AuditEvent(
        created_at=created_at,
        project_id=project_id,
        actor_id=actor_id,
        actor_kind=actor_kind,
        actor_label=actor_label,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        detail=detail,
        model_trace=model_trace,
        approval_id=approval_id,
        ip_address=ip_address,
        prev_hash=prev_hash,
    )
    entry.entry_hash = compute_entry_hash(
        prev_hash=prev_hash,
        created_at=created_at,
        actor_id=actor_id,
        actor_kind=actor_kind,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        detail=detail,
    )
    db.add(entry)
    db.flush()
    return entry


@dataclass(slots=True)
class ChainVerification:
    ok: bool
    checked: int
    first_bad_seq: int | None = None
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "first_bad_seq": self.first_bad_seq,
            "reason": self.reason,
        }


def verify_chain(db: Session, *, limit: int | None = None) -> ChainVerification:
    """Walk the ledger and confirm every link. O(n) and safe to run on demand."""
    stmt = select(AuditEvent).order_by(AuditEvent.seq.asc())
    if limit:
        stmt = stmt.limit(limit)
    entries = list(db.execute(stmt).scalars())

    expected_prev = GENESIS
    for i, e in enumerate(entries):
        if e.prev_hash != expected_prev:
            return ChainVerification(
                ok=False,
                checked=i,
                first_bad_seq=e.seq,
                reason=(
                    f"prev_hash mismatch: expected {expected_prev[:12]}…, "
                    f"stored {e.prev_hash[:12]}… (an entry was deleted or reordered)"
                ),
            )
        recomputed = compute_entry_hash(
            prev_hash=e.prev_hash,
            created_at=e.created_at,
            actor_id=e.actor_id,
            actor_kind=e.actor_kind,
            action=e.action,
            resource_type=e.resource_type,
            resource_id=e.resource_id,
            outcome=e.outcome,
            detail=e.detail or {},
        )
        if recomputed != e.entry_hash:
            return ChainVerification(
                ok=False,
                checked=i,
                first_bad_seq=e.seq,
                reason="entry content does not match its recorded hash (row was modified)",
            )
        expected_prev = e.entry_hash

    return ChainVerification(ok=True, checked=len(entries))
