"""Slack and Microsoft Teams notifications: first-class targets off the event bus.

A Slack Incoming Webhook or a Teams connector is configured like any integration (its
URL sealed in the vault); when a subscribed event fires (a finished/failed run, a
proposed heal, a queued approval, a signed-off milestone, a finished cycle), a compact
message is posted. This is the same bus the webhooks and metrics sinks listen on, so
nothing here can block the caller.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from ..core.events import Event, bus
from ..db import session_scope
from ..integrations.base import IntegrationError, http_client, load_connection
from ..models import IntegrationConnection, Run

log = logging.getLogger("galeqea.notify")

_PROVIDERS = ("slack", "teams")
#: Default subscription when a target doesn't pin its own events.
_DEFAULT_EVENTS = ("run.finished", "run.failed", "heal.proposed", "approval.requested",
                   "milestone.signed_off", "cycle.finished")


def _event_names(event: Event) -> list[str]:
    # Reuse the webhook mapping so the two surfaces never drift.
    from .webhooks import _event_names as webhook_names
    return webhook_names(event)


def _subscribed(connection: IntegrationConnection, names: list[str]) -> bool:
    cfg = connection.config or {}
    events = cfg.get("events") or list(_DEFAULT_EVENTS)
    if cfg.get("only_failures"):
        events = ["run.failed"]
    return any(n in events for n in names)


def _summary(db, event: Event, name: str) -> tuple[str, str]:
    """(title, detail-line) for the message."""
    data = event.payload or {}
    if name in ("run.finished", "run.failed"):
        run = db.get(Run, event.run_id) if event.run_id else None
        if run is not None:
            t = run.totals or {}
            icon = "❌" if name == "run.failed" or run.status in ("failed", "error") else "✅"
            return (f"{icon} Run #{run.number} {run.status}: {run.title}",
                    f"{t.get('passed', 0)} passed · {t.get('failed', 0)} failed "
                    f"on {run.environment}")
        return (f"Run event: {name}", "")
    if name == "heal.proposed":
        return ("🔧 A self-heal was proposed", "Review it in the Approvals view.")
    if name == "approval.requested":
        return ("📋 An action is waiting for approval",
                str(data.get("title") or data.get("action") or ""))
    if name == "milestone.signed_off":
        return (f"🚦 Release {data.get('version', '')} signed off {data.get('decision', '')}", "")
    if name == "cycle.finished":
        counters = data.get("counters") or {}
        return (f"🔵 Cycle finished: {data.get('name', '')}",
                f"{counters.get('passed', 0)} passed · {counters.get('failed', 0)} failed")
    return (name, "")


def _slack_body(title: str, detail: str, href: str) -> dict:
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{title}*"}}]
    if detail:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": detail}]})
    if href:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"<{href}|Open in GaleQEA>"}]})
    return {"text": title + (f": {detail}" if detail else ""), "blocks": blocks}


def _teams_body(title: str, detail: str, href: str) -> dict:
    card = {"@type": "MessageCard", "@context": "http://schema.org/extensions",
            "summary": title, "themeColor": "0B5FFF",
            "sections": [{"activityTitle": title,
                          "text": detail or ""}]}
    if href:
        card["potentialAction"] = [{"@type": "OpenUri", "name": "Open in GaleQEA",
                                    "targets": [{"os": "default", "uri": href}]}]
    return card


def _href(event: Event) -> str:
    if event.run_id:
        return f"/runs/{event.run_id}"
    return ""


async def deliver(event: Event) -> None:
    """Bus sink. Owns its errors, so a failing notifier never breaks the run."""
    names = _event_names(event)
    if not names or not event.project_id:
        return
    with session_scope() as db:
        connections = list(db.execute(select(IntegrationConnection).where(
            IntegrationConnection.project_id == event.project_id,
            IntegrationConnection.provider.in_(_PROVIDERS),
            IntegrationConnection.enabled.is_(True))).scalars())
        targets = []
        for c in connections:
            if not _subscribed(c, names):
                continue
            title, detail = _summary(db, event, names[0])
            targets.append((c.provider, title, detail))
        href = _href(event)

    for provider, title, detail in targets:
        try:
            await _post(provider, event.project_id, title, detail, href)
        except Exception as exc:  # noqa: BLE001 - best effort
            log.warning("notify %s failed: %s", provider, exc)


async def _post(provider: str, project_id: str, title: str, detail: str, href: str) -> None:
    import httpx
    with session_scope() as db:
        try:
            connection = load_connection(db, project_id=project_id, provider=provider)
            url = connection.secret("webhook_url")
        except IntegrationError:
            return
    body = _slack_body(title, detail, href) if provider == "slack" else _teams_body(title, detail, href)
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(url, json=body)


def send_test(db, *, project_id: str, provider: str) -> dict:
    """Post a one-off test message so a user can confirm the webhook works."""
    connection = load_connection(db, project_id=project_id, provider=provider)
    url = connection.secret("webhook_url")
    body = (_slack_body("GaleQEA test notification", "If you can see this, notifications work.", "")
            if provider == "slack"
            else _teams_body("GaleQEA test notification", "If you can see this, notifications work.", ""))
    with http_client() as client:
        r = client.post(url, json=body)
    if r.status_code >= 400:
        raise IntegrationError(f"{provider} returned HTTP {r.status_code}")
    return {"ok": True, "provider": provider}


def set_events(db, *, project_id: str, provider: str, events: list[str] | None = None,
               only_failures: bool | None = None) -> dict:
    connection = db.execute(select(IntegrationConnection).where(
        IntegrationConnection.project_id == project_id,
        IntegrationConnection.provider == provider)).scalars().first()
    if connection is None:
        raise IntegrationError(f"{provider} is not connected")
    cfg = dict(connection.config or {})
    if events is not None:
        cfg["events"] = events
    if only_failures is not None:
        cfg["only_failures"] = only_failures
    connection.config = cfg
    db.flush()
    return {"ok": True, "provider": provider, "events": cfg.get("events"),
            "only_failures": cfg.get("only_failures", False)}


def register() -> None:
    """Wire notification delivery into the event bus. Idempotent."""
    bus.add_sink(deliver)
