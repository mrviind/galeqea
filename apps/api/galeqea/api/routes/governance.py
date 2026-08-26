"""Approvals, audit ledger, secrets and settings - the governance surface."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...ai.providers.registry import describe_modes, reset_default
from ...ai.toolset import tool_catalog
from ...config import AIMode, settings
from ...core import approvals, audit
from ...core.approvals import ApprovalError, SelfApprovalError
from ...core.events import Ev, Event, bus
from ...core.vault import hint_for, seal
from ...db import get_db
from ...models import ApprovalRequest, ApprovalStatus, Project, Role, User, VaultSecret
from ..deps import current_user, get_project

router = APIRouter(prefix="/api/projects/{project_id}", tags=["governance"])


# --------------------------------------------------------------------------- #
# Approvals
# --------------------------------------------------------------------------- #
@router.get("/approvals")
def list_approvals(
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    status: str = ApprovalStatus.PENDING,
):
    rows = db.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.project_id == project.id, ApprovalRequest.status == status
        ).order_by(ApprovalRequest.created_at.desc())
    ).scalars()
    return [
        {"id": r.id, "action": r.action, "title": r.title, "summary": r.summary,
         "risk": r.risk, "required_role": r.required_role, "status": r.status,
         "payload": r.payload, "diff": r.diff, "evidence": r.evidence,
         "requested_by_kind": r.requested_by_kind, "agent_role": r.agent_role,
         "batch_id": r.batch_id, "trace_id": r.trace_id,
         "created_at": r.created_at.isoformat(),
         "expires_at": r.expires_at.isoformat() if r.expires_at else None}
        for r in rows
    ]


@router.post("/approvals/{approval_id}/decide")
async def decide(
    approval_id: str,
    payload: dict,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    decision = payload.get("decision")
    if decision not in {"approve", "reject"}:
        raise HTTPException(400, "decision must be 'approve' or 'reject'")
    try:
        if decision == "approve":
            outcome = approvals.approve(db, approval_id, user, comment=payload.get("comment", ""))
            result = {"status": outcome.request.status, "applied": outcome.applied,
                      "result": outcome.result}
        else:
            request = approvals.reject(db, approval_id, user, comment=payload.get("comment", ""))
            result = {"status": request.status, "applied": False, "result": {}}
    except SelfApprovalError as exc:
        # 403 rather than 400: this is an authorisation boundary, not a typo.
        raise HTTPException(403, str(exc)) from exc
    except ApprovalError as exc:
        raise HTTPException(409, str(exc)) from exc

    db.commit()
    await bus.publish(Event(
        type=Ev.APPROVAL_DECIDED, project_id=project.id,
        payload={"approval_id": approval_id, "decision": decision, **result},
    ))
    return result


@router.post("/approvals/batch/{batch_id}/decide")
def decide_batch(
    batch_id: str,
    payload: dict,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    try:
        decisions = approvals.decide_batch(
            db, batch_id, user,
            approved=payload.get("decision") == "approve",
            comment=payload.get("comment", ""),
        )
    except SelfApprovalError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ApprovalError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return {"decided": len(decisions),
            "applied": sum(1 for d in decisions if d.applied)}


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
@router.get("/audit")
def read_audit(
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    limit: int = 100,
    verify: bool = True,
):
    from ...models import AuditEvent

    rows = db.execute(
        select(AuditEvent).where(
            (AuditEvent.project_id == project.id) | (AuditEvent.project_id.is_(None))
        ).order_by(AuditEvent.seq.desc()).limit(min(limit, 1000))
    ).scalars()
    out = {
        "events": [
            {"seq": e.seq, "at": e.created_at.isoformat(), "action": e.action,
             "actor": e.actor_label or e.actor_id or "system", "actor_kind": e.actor_kind,
             "resource_type": e.resource_type, "resource_id": e.resource_id,
             "outcome": e.outcome, "detail": e.detail, "approval_id": e.approval_id,
             "entry_hash": e.entry_hash[:16], "prev_hash": e.prev_hash[:16]}
            for e in rows
        ]
    }
    if verify:
        out["chain"] = audit.verify_chain(db).as_dict()
    return out


@router.get("/audit/export")
def export_audit(
    project: Project = Depends(get_project), db: Session = Depends(get_db), fmt: str = "json"
):
    """Compliance export: the full ledger plus a verification receipt."""
    from ...models import AuditEvent

    rows = list(
        db.execute(
            select(AuditEvent).where(
                (AuditEvent.project_id == project.id) | (AuditEvent.project_id.is_(None))
            ).order_by(AuditEvent.seq.asc())
        ).scalars()
    )
    verification = audit.verify_chain(db)
    payload = {
        "project": {"id": project.id, "key": project.key, "name": project.name},
        "verification": verification.as_dict(),
        "entry_count": len(rows),
        "events": [
            {"seq": e.seq, "at": e.created_at.isoformat(), "action": e.action,
             "actor_id": e.actor_id, "actor_kind": e.actor_kind, "actor": e.actor_label,
             "resource_type": e.resource_type, "resource_id": e.resource_id,
             "outcome": e.outcome, "detail": e.detail, "model_trace": e.model_trace,
             "approval_id": e.approval_id, "prev_hash": e.prev_hash, "entry_hash": e.entry_hash}
            for e in rows
        ],
    }
    if fmt == "csv":
        import csv
        import io

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["seq", "at", "action", "actor", "actor_kind", "resource",
                         "outcome", "entry_hash"])
        for e in rows:
            writer.writerow([e.seq, e.created_at.isoformat(), e.action,
                             e.actor_label or e.actor_id, e.actor_kind,
                             f"{e.resource_type}:{e.resource_id}", e.outcome, e.entry_hash])
        return {"format": "csv", "content": buffer.getvalue(),
                "verification": verification.as_dict()}
    return payload


# --------------------------------------------------------------------------- #
# Secrets
# --------------------------------------------------------------------------- #
@router.get("/secrets")
def list_secrets(project: Project = Depends(get_project), db: Session = Depends(get_db)):
    rows = db.execute(
        select(VaultSecret).where(VaultSecret.project_id == project.id)
    ).scalars()
    # Values are never returned. Only the hint, so a human can confirm which
    # credential is wired up without it appearing in a response body or a log.
    return [
        {"id": s.id, "name": s.name, "kind": s.kind, "hint": s.hint,
         "created_at": s.created_at.isoformat(),
         "last_used_at": s.last_used_at.isoformat() if s.last_used_at else None}
        for s in rows
    ]


@router.post("/secrets")
def write_secret(
    payload: dict,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if not user.at_least(Role.ADMIN):
        raise HTTPException(403, "writing a secret requires the admin role or above")
    name, value = payload.get("name"), payload.get("value")
    if not name or not value:
        raise HTTPException(400, "name and value are required")

    existing = db.execute(
        select(VaultSecret).where(
            VaultSecret.project_id == project.id, VaultSecret.name == name
        )
    ).scalar_one_or_none()
    if existing:
        existing.ciphertext = seal(value, aad=f"{project.id}:{name}")
        existing.hint = hint_for(value)
        secret = existing
    else:
        secret = VaultSecret(
            project_id=project.id, name=name, kind=payload.get("kind", "generic"),
            ciphertext=seal(value, aad=f"{project.id}:{name}"),
            hint=hint_for(value), created_by=user.id,
        )
        db.add(secret)
    db.flush()
    audit.record(db, action="secret.written", actor_id=user.id, actor_label=user.email,
                 project_id=project.id, resource_type="vault_secret", resource_id=secret.id,
                 detail={"name": name, "kind": secret.kind})
    db.commit()
    return {"id": secret.id, "name": secret.name, "hint": secret.hint}


@router.delete("/secrets/{secret_id}")
def delete_secret(
    secret_id: str,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if not user.at_least(Role.ADMIN):
        raise HTTPException(403, "requires the admin role or above")
    secret = db.get(VaultSecret, secret_id)
    if secret is None or secret.project_id != project.id:
        raise HTTPException(404, "secret not found")
    audit.record(db, action="secret.deleted", actor_id=user.id, project_id=project.id,
                 resource_type="vault_secret", resource_id=secret_id,
                 detail={"name": secret.name})
    db.delete(secret)
    db.commit()
    return {"deleted": secret_id}


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
settings_router = APIRouter(prefix="/api/settings", tags=["settings"])


@settings_router.get("")
def read_settings(user: User = Depends(current_user)):
    return {
        "ai": {
            "mode": settings.ai_mode.value,
            "provider": settings.provider,
            "model": settings.model,
            "base_url": settings.base_url,
            "api_key_set": bool(settings.api_key),
            "enabled": settings.ai_enabled,
            # True when the operator has chosen a mode that needs a model but
            # has not finished choosing one. The UI prompts on this rather than
            # rendering an empty picker that looks broken.
            "needs_configuration": (
                settings.ai_mode is not AIMode.NO_AI
                and not (settings.provider and settings.provider != "none" and settings.model)
            ),
            "web_research_enabled": settings.web_research_enabled,
            "max_tokens_per_run": settings.max_tokens_per_run,
            "max_agent_steps": settings.max_agent_steps,
        },
        "governance": {
            "approval_mode": settings.approval_mode.value,
            # Reported so an operator can see it is a structural guarantee, not
            # a setting they might have left on.
            "ai_self_approval": "structurally prohibited",
        },
        "execution": {
            "max_parallel_runs": settings.max_parallel_runs,
            "default_browser": settings.default_browser,
            "default_timeout_ms": settings.default_timeout_ms,
            "runner_entry": settings.runner_entry,
        },
        "telemetry_enabled": settings.telemetry_enabled,
        "modes": describe_modes(),
        "tools": tool_catalog(),
        "user": {"id": user.id, "email": user.email, "role": user.role},
    }


@settings_router.post("/model")
async def update_model(
    payload: dict, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    if not user.at_least(Role.ADMIN):
        raise HTTPException(403, "changing the model configuration requires the admin role")

    mode = payload.get("mode", settings.ai_mode.value)
    try:
        settings.ai_mode = AIMode(mode)
    except ValueError as exc:
        raise HTTPException(400, f"unknown mode {mode!r}") from exc

    for field in ("provider", "model", "base_url"):
        if field in payload:
            setattr(settings, field, payload[field] or "")
    if "api_key" in payload and payload["api_key"]:
        settings.api_key = payload["api_key"]
    if settings.ai_mode is AIMode.NO_AI:
        # Clear the whole selection, not just the provider. Leaving a stale model
        # and base_url behind means the next switch to a hosted provider silently
        # inherits a URL from whatever was configured before — which is how a
        # request meant for Anthropic ends up at somebody's old test proxy.
        settings.provider = "none"
        settings.model = ""
        settings.base_url = ""
        settings.api_key = ""

    reset_default()
    audit.record(
        db, action="settings.model_changed", actor_id=user.id, actor_label=user.email,
        resource_type="settings",
        detail={"mode": settings.ai_mode.value, "provider": settings.provider,
                "model": settings.model, "api_key_set": bool(settings.api_key)},
    )
    db.commit()

    from ...ai.providers.registry import default_provider

    health = await default_provider().health()
    return {"ai": {"mode": settings.ai_mode.value, "provider": settings.provider,
                   "model": settings.model, "enabled": settings.ai_enabled},
            "health": health}


@settings_router.get("/model/health")
async def model_health():
    from ...ai.providers.registry import default_provider

    return await default_provider().health()


# --------------------------------------------------------------------------- #
# Bring your own key
# --------------------------------------------------------------------------- #
@settings_router.get("/keys")
def list_keys(
    project_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Stored model credentials. Values never leave the vault — only hints."""
    from ...ai import keys

    return {
        "keys": [c.as_dict() for c in keys.listing(db, project_id)],
        "note": (
            "Keys are sealed in the local vault and are never returned by this API. "
            "A project key overrides the global one."
        ),
    }


@settings_router.get("/models")
def list_models(
    project_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Models the Copilot may select, resolved from the server-side vault.

    This is what makes a model picker safe to put in a browser on an enterprise
    deployment. The client never learns a key and never holds one: it asks which
    providers are credentialed and receives *identifiers*. Selecting one sends
    the identifier back, and the server resolves the matching credential out of
    the sealed vault at request time.

    That is the difference between a BYOK client and a BYOK platform. The
    credential is centrally held, centrally rotated, and every use of it lands in
    the audit ledger against the principal that caused it — none of which is
    possible when the key lives in someone's localStorage.
    """
    from ...ai import keys
    from ...ai.providers.registry import LOCAL_PROVIDERS, MODEL_CATALOGUE

    del user  # authentication is the requirement; there is no per-user filtering

    configured = {c.provider: c for c in keys.listing(db, project_id)}
    out: list[dict] = []

    for provider, catalogue in MODEL_CATALOGUE.items():
        credential = configured.get(provider)
        local = provider in LOCAL_PROVIDERS
        available = bool(credential) or local
        # A credential may name a model this catalogue has never heard of.
        # Merging it in means a newer model needs configuring, not a code change.
        entries = list(catalogue)
        if credential and credential.model and not any(e["id"] == credential.model for e in entries):
            entries.insert(0, {"id": credential.model, "label": credential.model, "context": None})

        for entry in entries:
            reason = ""
            if not available:
                reason = f"No {provider} credential in the vault. Add one in Settings."
            elif credential and credential.over_budget:
                reason = (
                    f"This month's spend has reached the ${credential.monthly_budget_usd:.2f} "
                    f"budget for {provider}."
                )
            out.append({
                "id": entry["id"],
                "label": entry["label"],
                "provider": provider,
                "context": entry.get("context") or None,
                "available": available and not (credential and credential.over_budget),
                "reason": reason,
                "local": local,
                "scope": ("global" if credential and credential.scope == keys.GLOBAL_SCOPE
                          else (credential.scope if credential else "")),
            })

    return {
        "models": out,
        "configured_providers": sorted(configured),
        "active": {"provider": settings.provider, "model": settings.model},
        "ai_enabled": settings.ai_enabled,
        "mode": settings.ai_mode.value,
        "note": (
            "Credentials are resolved server-side from the sealed vault. This "
            "endpoint returns model identifiers only and never returns a key."
        ),
    }


@settings_router.post("/keys/verify")
async def verify_key(payload: dict, user: User = Depends(current_user)):
    """Probe a key without storing it, so a bad key is caught while you are here."""
    from ...ai import keys

    if not user.at_least(Role.ADMIN):
        raise HTTPException(403, "managing model keys requires the admin role")
    return await keys.verify(
        payload.get("provider", ""),
        api_key=payload.get("api_key", ""),
        model=payload.get("model", ""),
        base_url=payload.get("base_url", ""),
    )


@settings_router.post("/keys")
async def save_key(
    payload: dict, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    """Verify, then store. An unverified key is never written."""
    from ...ai import keys

    if not user.at_least(Role.ADMIN):
        raise HTTPException(403, "managing model keys requires the admin role")

    provider = payload.get("provider", "")
    api_key = payload.get("api_key", "")
    if not provider or not api_key:
        raise HTTPException(400, "provider and api_key are required")

    probe = await keys.verify(
        provider, api_key=api_key,
        model=payload.get("model", ""), base_url=payload.get("base_url", ""),
    )
    if not probe["ok"]:
        # Storing it anyway trades a five-second wait now for a failure in the
        # middle of an unattended run later, with the real error long gone.
        raise HTTPException(
            400, f"that key did not work, so it was not saved: {probe['error']}"
        )

    scope = payload.get("project_id") or keys.GLOBAL_SCOPE
    try:
        credential = keys.store(
            db, provider=provider, api_key=api_key, scope=scope,
            model=payload.get("model", ""), base_url=payload.get("base_url", ""),
            monthly_budget_usd=float(payload.get("monthly_budget_usd") or 0.0),
            created_by=user.id,
        )
    except keys.KeyError_ as exc:
        raise HTTPException(400, str(exc)) from exc

    audit.record(
        db, action="model_key.stored", actor_id=user.id, actor_label=user.email,
        project_id=None if scope == keys.GLOBAL_SCOPE else scope,
        resource_type="model_key", resource_id=provider,
        # The hint, never the key.
        detail={"provider": provider, "scope": credential.scope,
                "hint": credential.hint, "model": credential.model,
                "budget_usd": credential.monthly_budget_usd},
    )
    db.commit()
    return {"key": credential.as_dict(), "health": probe.get("health", {})}


@settings_router.delete("/keys/{provider}")
def revoke_key(
    provider: str,
    project_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    from ...ai import keys

    if not user.at_least(Role.ADMIN):
        raise HTTPException(403, "managing model keys requires the admin role")
    scope = project_id or keys.GLOBAL_SCOPE
    if not keys.revoke(db, provider=provider, scope=scope):
        raise HTTPException(404, "no key stored for that provider and scope")
    audit.record(db, action="model_key.revoked", actor_id=user.id, actor_label=user.email,
                 project_id=project_id, resource_type="model_key", resource_id=provider,
                 detail={"provider": provider, "scope": scope})
    db.commit()
    return {"revoked": provider, "scope": scope}


@settings_router.get("/usage")
def usage(
    project_id: str | None = None,
    days: int = 30,
    db: Session = Depends(get_db),
):
    """Token and cost attribution — what the key has actually been spent on."""
    from datetime import timedelta

    from ...models import UsageLedger
    from ...models.base import utcnow

    since = utcnow() - timedelta(days=min(days, 365))
    stmt = select(UsageLedger).where(UsageLedger.created_at >= since)
    if project_id:
        stmt = stmt.where(UsageLedger.project_id == project_id)

    rows = list(db.execute(stmt).scalars())
    by_operation: dict[str, dict] = {}
    for row in rows:
        bucket = by_operation.setdefault(
            row.agent_role or row.operation or "other",
            {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        )
        bucket["calls"] += 1
        bucket["input_tokens"] += row.input_tokens or 0
        bucket["output_tokens"] += row.output_tokens or 0
        bucket["cost_usd"] = round(bucket["cost_usd"] + (row.cost_usd or 0.0), 6)

    return {
        "days": days,
        "calls": len(rows),
        "input_tokens": sum(r.input_tokens or 0 for r in rows),
        "output_tokens": sum(r.output_tokens or 0 for r in rows),
        "cost_usd": round(sum(r.cost_usd or 0.0 for r in rows), 4),
        "by_operation": by_operation,
    }
