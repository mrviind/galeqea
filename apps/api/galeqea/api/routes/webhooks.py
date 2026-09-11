"""Outbound webhook configuration and delivery log.

Endpoints subscribe to events and receive a signed POST when one fires. The
secret is shown once, at creation, and never returned again; the API returns a
hint. The delivery log is visible so a failing endpoint is discoverable, not
silent.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import WEBHOOK_EVENTS, Project, WebhookDelivery, WebhookEndpoint
from ...services.webhooks import new_secret
from ..deps import get_project

router = APIRouter(prefix="/api/projects/{project_id}/webhooks", tags=["webhooks"])


def _public(ep: WebhookEndpoint) -> dict:
    return {
        "id": ep.id, "url": ep.url, "events": ep.events, "active": ep.active,
        "description": ep.description,
        "secret_hint": (ep.secret[:4] + "…") if ep.secret else "",
        "created_at": ep.created_at.isoformat() if ep.created_at else None,
    }


@router.get("")
def list_webhooks(db: Session = Depends(get_db), project: Project = Depends(get_project)):
    rows = db.execute(
        select(WebhookEndpoint).where(WebhookEndpoint.project_id == project.id)
        .order_by(WebhookEndpoint.created_at.desc())
    ).scalars()
    return {"webhooks": [_public(e) for e in rows], "available_events": list(WEBHOOK_EVENTS)}


@router.post("")
def create_webhook(payload: dict, db: Session = Depends(get_db), project: Project = Depends(get_project)):
    url = (payload.get("url") or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(400, "url must be http(s)")
    events = payload.get("events") or []
    unknown = [e for e in events if e not in WEBHOOK_EVENTS]
    if unknown:
        raise HTTPException(400, f"unknown event(s): {', '.join(unknown)}. Allowed: {', '.join(WEBHOOK_EVENTS)}")
    if not events:
        raise HTTPException(400, "subscribe to at least one event")

    secret = (payload.get("secret") or "").strip() or new_secret()
    ep = WebhookEndpoint(
        project_id=project.id, url=url, secret=secret, events=events,
        active=bool(payload.get("active", True)), description=(payload.get("description") or "")[:300],
    )
    db.add(ep)
    db.commit()
    # The secret is returned exactly once, the only time the API ever reveals it.
    return {**_public(ep), "secret": secret,
            "note": "Store this secret now; it is shown only once. Deliveries use the Standard "
                    "Webhooks header set (webhook-id, webhook-timestamp, webhook-signature); verify "
                    "with any Standard Webhooks library."}


@router.delete("/{webhook_id}")
def delete_webhook(webhook_id: str, db: Session = Depends(get_db), project: Project = Depends(get_project)):
    ep = db.get(WebhookEndpoint, webhook_id)
    if ep is None or ep.project_id != project.id:
        raise HTTPException(404, "webhook not found")
    db.execute(delete(WebhookDelivery).where(WebhookDelivery.endpoint_id == ep.id))
    db.delete(ep)
    db.commit()
    return {"ok": True, "deleted": webhook_id}


@router.get("/deliveries")
def list_deliveries(
    limit: int = 50, db: Session = Depends(get_db), project: Project = Depends(get_project),
):
    rows = db.execute(
        select(WebhookDelivery).where(WebhookDelivery.project_id == project.id)
        .order_by(WebhookDelivery.created_at.desc()).limit(min(limit, 200))
    ).scalars()
    return {"deliveries": [
        {
            "id": d.id, "endpoint_id": d.endpoint_id, "event": d.event, "status": d.status,
            "attempts": d.attempts, "response_code": d.response_code,
            "error": d.error or "", "at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in rows
    ]}
