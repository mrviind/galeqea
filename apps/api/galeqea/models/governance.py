"""Approval gates, the audit ledger and the secret vault.

Two invariants are enforced structurally rather than by policy:

1. Every write proposed by an agent lands in ``ApprovalRequest`` first.
2. ``approver_id`` must reference a human principal that is not the proposer.
   The check lives in ``core/approvals.py`` and is unconditional.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import IdMixin, JSONish, TimestampMixin, UTCDateTime, new_id


class RiskTier(StrEnum):
    LOW = "low"          # local, reversible (edit a draft test)
    MEDIUM = "medium"    # writes durable state (approve a test, apply a heal)
    HIGH = "high"        # leaves the building (commit, PR, file a ticket)
    CRITICAL = "critical"  # destructive or spends money


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    APPLIED = "applied"


class ApprovalRequest(Base, IdMixin, TimestampMixin):
    __tablename__ = "approval_requests"

    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    batch_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)

    action: Mapped[str] = mapped_column(String(64), index=True)   # e.g. test.approve, heal.apply
    resource_type: Mapped[str] = mapped_column(String(48), default="")
    resource_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    title: Mapped[str] = mapped_column(String(400), default="")
    summary: Mapped[str] = mapped_column(Text, default="")

    # Exactly what will happen if approved, as a reviewable diff.
    payload: Mapped[dict] = mapped_column(JSONish, default=dict)
    diff: Mapped[dict] = mapped_column(JSONish, default=dict)
    evidence: Mapped[dict] = mapped_column(JSONish, default=dict)

    risk: Mapped[str] = mapped_column(String(16), default=RiskTier.MEDIUM, index=True)
    required_role: Mapped[str] = mapped_column(String(20), default="approver")

    requested_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    requested_by_kind: Mapped[str] = mapped_column(String(16), default="agent")  # agent|human
    agent_role: Mapped[str] = mapped_column(String(48), default="")
    trace_id: Mapped[str | None] = mapped_column(String(40), nullable=True)

    status: Mapped[str] = mapped_column(String(20), default=ApprovalStatus.PENDING, index=True)
    decided_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    decision_comment: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    apply_error: Mapped[str] = mapped_column(Text, default="")


class ApprovalBatch(Base, IdMixin, TimestampMixin):
    """Gated-workflow mode: review N related writes as one unit."""

    __tablename__ = "approval_batches"

    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(400), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=ApprovalStatus.PENDING, index=True)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    decided_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class AuditEvent(Base):
    """Append-only, hash-chained ledger.

    Each row stores ``prev_hash`` and its own ``entry_hash`` over the canonical
    JSON of the entry. Tampering with or deleting any row breaks verification
    from that point forward, which ``core/audit.verify_chain`` reports precisely.
    """

    __tablename__ = "audit_events"

    # `seq` is the primary key, not merely a unique column: on SQLite only an
    # INTEGER PRIMARY KEY autoincrements, and a ledger whose ordering is
    # implicit rather than enforced is not a ledger.
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(40), unique=True, index=True, default=lambda: new_id("aud"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)

    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    actor_kind: Mapped[str] = mapped_column(String(16), default="human")
    actor_label: Mapped[str] = mapped_column(String(200), default="")

    action: Mapped[str] = mapped_column(String(80), index=True)
    resource_type: Mapped[str] = mapped_column(String(48), default="")
    resource_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(String(24), default="success")

    detail: Mapped[dict] = mapped_column(JSONish, default=dict)
    # Full model trace for AI-originated actions: provider, model, prompt hash,
    # token counts, latency. Answers "why did it do that" months later.
    model_trace: Mapped[dict | None] = mapped_column(JSONish, nullable=True)
    approval_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ip_address: Mapped[str] = mapped_column(String(64), default="")

    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    entry_hash: Mapped[str] = mapped_column(String(64), default="", index=True)


class VaultSecret(Base, IdMixin, TimestampMixin):
    """Envelope-encrypted credential. Plaintext never leaves ``core/vault.py``."""

    __tablename__ = "vault_secrets"

    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    scope: Mapped[str] = mapped_column(String(48), default="project")  # project|user|global
    owner_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    ciphertext: Mapped[str] = mapped_column(Text, default="")
    # Shown in the UI so a human can confirm which key is wired up without
    # ever revealing it: "sk-ant-...4f2a".
    hint: Mapped[str] = mapped_column(String(64), default="")
    kind: Mapped[str] = mapped_column(String(48), default="generic")
    rotated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: Non-secret configuration that belongs with the credential — which model
    #: the key is for, its endpoint, its spend cap. Keeping it here means a
    #: project's whole model setup moves, and is revoked, as one unit.
    meta: Mapped[dict] = mapped_column(JSONish, default=dict)


class PolicyRule(Base, IdMixin, TimestampMixin):
    """Declarative guardrails evaluated before any agent tool call."""

    __tablename__ = "policy_rules"

    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    # {"tool": "create_jira_ticket", "when": {...}} -> allow | require_approval | deny
    matcher: Mapped[dict] = mapped_column(JSONish, default=dict)
    effect: Mapped[str] = mapped_column(String(24), default="require_approval")
    min_role: Mapped[str] = mapped_column(String(20), default="approver")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
