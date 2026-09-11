"""Deterministic chat verbs for Slack/Teams notifications.

"connect slack" opens a secure form (the webhook URL is a secret and never enters the
transcript); "notify slack on failures" / "on everything" tunes what a connected target
posts.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import IntegrationConnection, Project, User

_DEFAULT_EVENTS = ["run.finished", "run.failed", "heal.proposed", "approval.requested",
                   "milestone.signed_off", "cycle.finished"]


def _connected(db: Session, project_id: str, provider: str) -> IntegrationConnection | None:
    return db.execute(select(IntegrationConnection).where(
        IntegrationConnection.project_id == project_id,
        IntegrationConnection.provider == provider,
        IntegrationConnection.enabled.is_(True))).scalars().first()


def try_handle(db: Session, project: Project, text: str, user: User) -> tuple[str, list[dict]] | None:
    low = text.strip().lower()

    m = re.search(r"\bconnect\s+(slack|teams|microsoft teams)\b", low)
    if m:
        provider = "teams" if "teams" in m.group(1) else "slack"
        label = "Microsoft Teams" if provider == "teams" else "Slack"
        return (f"Opening the {label} connection form. Paste your incoming-webhook URL in the "
                "secure field below. It's sealed in the vault and never shown in this chat.",
                [{"type": "integration_connect", "provider": provider, "base_url": ""}])

    m = re.search(r"notify\s+(slack|teams)\s+(?:me\s+)?(?:on|for|about)\s+(.+)$", low)
    if m:
        provider, what = m.group(1), m.group(2).strip()
        if _connected(db, project.id, provider) is None:
            return (f"{provider.title()} isn't connected. Say \"connect {provider}\" first.",
                    [{"type": "integration_connect", "provider": provider, "base_url": ""}])
        from ..services import notify
        if "fail" in what:
            notify.set_events(db, project_id=project.id, provider=provider, only_failures=True)
            db.commit()
            return (f"{provider.title()} will now be notified on failed runs only.", [])
        if what in ("everything", "all", "all events"):
            notify.set_events(db, project_id=project.id, provider=provider,
                              events=_DEFAULT_EVENTS, only_failures=False)
            db.commit()
            return (f"{provider.title()} will be notified on runs, heals, approvals, sign-offs "
                    "and finished cycles.", [])
        # a comma/space list of event keywords
        wanted = []
        for token, name in (("run", "run.finished"), ("fail", "run.failed"),
                            ("heal", "heal.proposed"), ("approval", "approval.requested"),
                            ("sign", "milestone.signed_off"), ("cycle", "cycle.finished")):
            if token in what:
                wanted.append(name)
        if wanted:
            notify.set_events(db, project_id=project.id, provider=provider, events=wanted,
                              only_failures=False)
            db.commit()
            return (f"{provider.title()} will be notified on: {', '.join(wanted)}.", [])
        return (f"I couldn't read which events. Try \"notify {provider} on failures\", "
                f"\"on everything\", or list them (runs, approvals, sign-offs, cycles).", [])

    return None
