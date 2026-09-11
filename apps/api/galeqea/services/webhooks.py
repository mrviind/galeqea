"""Outbound webhook delivery.

Registered as a sink on the event bus: when a run finishes/fails, a heal is
proposed, or an approval is requested, the matching endpoints get a signed POST
of the JSON report summary. Every attempt is logged. Delivery is fire-and-forget
from the run's point of view (a slow or dead endpoint never blocks a run) and
retried with backoff before it's marked failed.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets

import httpx
from sqlalchemy import select

from ..core.events import Ev, Event, bus
from ..db import session_scope
from ..models import Run, WebhookDelivery, WebhookEndpoint
from ..models.base import utcnow

_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 0.3  # seconds; steps 0.3, 0.6, 1.2 are small so it never stalls a run


def new_secret() -> str:
    """A Standard-Webhooks-style secret: ``whsec_`` + base64 random bytes."""
    return "whsec_" + base64.b64encode(secrets.token_bytes(24)).decode()


def _signing_key(secret: str) -> bytes:
    """The HMAC key. A ``whsec_``-prefixed secret is base64 after the prefix
    (the Standard Webhooks convention); anything else is used as raw bytes."""
    if secret.startswith("whsec_"):
        try:
            return base64.b64decode(secret[len("whsec_"):])
        except (ValueError, TypeError):
            pass
    return secret.encode()


def sign(secret: str, msg_id: str, timestamp: str, body: bytes) -> str:
    """A Standard Webhooks signature: ``v1,<base64(HMAC-SHA256(key, id.ts.body))>``.
    Receivers can verify it with any off-the-shelf Standard Webhooks library."""
    signed = f"{msg_id}.{timestamp}.".encode() + body
    digest = hmac.new(_signing_key(secret), signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


def _event_names(event: Event) -> list[str]:
    """Which webhook event(s) this bus event maps to. A failed run fires both
    ``run.finished`` and ``run.failed`` so subscribers to either are served."""
    if event.type == Ev.RUN_FINISHED:
        status = str((event.payload or {}).get("status", "")).lower()
        return ["run.finished", "run.failed"] if status in {"failed", "error"} else ["run.finished"]
    if event.type == Ev.HEAL_PROPOSED:
        return ["heal.proposed"]
    if event.type == Ev.APPROVAL_REQUESTED:
        return ["approval.requested"]
    if event.type == Ev.CYCLE_FINISHED:
        return ["cycle.finished"]
    if event.type == Ev.MILESTONE_SIGNED_OFF:
        return ["milestone.signed_off"]
    return []


def _build_payload(db, event: Event, name: str) -> dict:
    """The delivered body: a compact summary plus hrefs to the full reports, so a
    receiver can act on the summary and fetch detail if it wants it."""
    pid = event.project_id
    base = f"/api/projects/{pid}"
    payload = {
        "event": name,
        "project_id": pid,
        "ts": event.ts,
        "data": dict(event.payload or {}),
    }
    if event.run_id:
        run = db.get(Run, event.run_id)
        if run is not None:
            payload["run"] = {
                "id": run.id, "number": run.number, "status": run.status,
                "totals": run.totals, "environment": run.environment,
                "report_href": f"{base}/runs/{run.id}/report.json",
                "ui_href": f"/runs/{run.id}",
            }
    return payload


async def _post_with_retries(ep: WebhookEndpoint, name: str, body: bytes) -> tuple[str, int, int, str]:
    """POST the signed body, retrying with backoff. Returns
    (status, attempts, response_code, error). Uses the Standard Webhooks header
    set so any conformant receiver can verify it; the message id and timestamp are
    fixed across retries (the same message is being re-delivered)."""
    msg_id = "msg_" + secrets.token_hex(12)
    timestamp = str(int(utcnow().timestamp()))
    headers = {
        "content-type": "application/json",
        "webhook-id": msg_id,
        "webhook-timestamp": timestamp,
        "webhook-signature": sign(ep.secret or "", msg_id, timestamp, body),
        "x-galeqea-event": name,
    }
    last_code, last_error = 0, ""
    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                resp = await client.post(ep.url, content=body, headers=headers)
                last_code = resp.status_code
                if 200 <= resp.status_code < 300:
                    return "delivered", attempt, resp.status_code, ""
                last_error = f"HTTP {resp.status_code}"
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(_BACKOFF_BASE * (2 ** (attempt - 1)))
    return "failed", _MAX_ATTEMPTS, last_code, last_error


async def deliver(event: Event) -> None:
    """The bus sink. Finds subscribed endpoints and delivers to each, logging
    every result. Owns its own errors, so nothing here can break the caller."""
    names = _event_names(event)
    if not names:
        return

    with session_scope() as db:
        endpoints = list(db.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.project_id == event.project_id,
                WebhookEndpoint.active.is_(True),
            )
        ).scalars())
        # Pre-build the body per event name (same summary, different label).
        bodies = {n: json.dumps(_build_payload(db, event, n)).encode() for n in names}

    # Each (endpoint, event) delivery is enqueued as its own job: in-process it runs
    # on the loop as before; on the Postgres queue a worker delivers it with retries,
    # so a slow endpoint never rides on the web process and survives a restart (P2-1).
    from ..jobs import get_queue
    queue = get_queue()
    for name in names:
        body = bodies[name].decode()
        for ep in endpoints:
            if name not in (ep.events or []):
                continue
            if queue.kind == "procrastinate":
                await queue.enqueue("deliver_webhook", endpoint_id=ep.id,
                                    event_name=name, body=body)
            else:
                await deliver_to_endpoint(ep.id, name, body)


async def deliver_to_endpoint(endpoint_id: str, event_name: str, body: str) -> None:
    """Deliver one event to one endpoint (signed, retried) and log the result. The
    body of the ``deliver_webhook`` job, safe to run in-process or in a worker."""
    with session_scope() as db:
        ep = db.get(WebhookEndpoint, endpoint_id)
        if ep is None or not ep.active:
            return
        project_id = ep.project_id
        raw = body.encode()
        status, attempts, code, error = await _post_with_retries(ep, event_name, raw)
        db.add(WebhookDelivery(
            endpoint_id=endpoint_id, project_id=project_id, event=event_name,
            status=status, attempts=attempts, response_code=code or None,
            error=error[:2000], request_body=body[:8000],
            delivered_at=utcnow() if status == "delivered" else None,
        ))


def register() -> None:
    """Wire delivery into the event bus. Idempotent (add_sink dedupes)."""
    bus.add_sink(deliver)
