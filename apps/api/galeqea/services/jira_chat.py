"""Deterministic chat verbs for the Jira daily loop: connect, import stories,
check for stale stories, and write coverage back. No model needed.

Credentials never travel through chat: "connect jira <site>" returns a block the UI
renders as a secure form (email + API-token typed into password fields, POSTed
straight to the sealed vault), so the token never lands in the transcript.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from ..core import approvals
from ..models import Project, User


def try_handle(db: Session, project: Project, text: str, user: User) -> tuple[str, list[dict]] | None:
    low = text.strip().lower()

    # connect jira <site> opens the secure connect form (no secret in chat)
    m = re.search(r"connect\s+jira\s+(https?://\S+)", text, re.I)
    if m:
        base_url = m.group(1).rstrip("/")
        return (f"Opening the Jira connection form for {base_url}. Enter your account email "
                "and API token in the secure fields below. They're sealed in the vault and "
                "never shown in this conversation. Then press Verify.",
                [{"type": "integration_connect", "provider": "jira", "base_url": base_url}])
    if re.search(r"\bconnect\s+jira\b", low):
        return ("Which Jira site? Say \"connect jira https://your-site.atlassian.net\" and "
                "I'll open a secure form for the email + API token (never typed into chat).",
                [{"type": "integration_connect", "provider": "jira", "base_url": ""}])

    # import stories from <sprint|fixVersion|JQL>
    m = re.search(r"import\s+(?:stories|issues|requirements)\s+(?:from\s+)?(.+)$", text, re.I)
    if m:
        from ..integrations.base import IntegrationError, load_connection
        from ..services import story_import
        try:
            load_connection(db, project_id=project.id, provider="jira")
        except IntegrationError:
            return ("Connect Jira first. Say \"connect jira https://your-site.atlassian.net\".", [])
        try:
            out = story_import.import_stories(db, project, selector=m.group(1).strip(), actor=user)
        except story_import.StoryImportError as exc:
            return (f"Couldn't import: {exc}", [])
        db.commit()
        n_new, n_upd = len(out["imported"]), len(out["updated"])
        stale = f", {len(out['stale'])} now stale" if out["stale"] else ""
        return (f"Imported {out['count']} Jira issue(s) as requirements "
                f"({n_new} new, {n_upd} updated{stale}). JQL: {out['jql']}. "
                "Run \"generate tests\" to propose tests for them (they land in the review board).",
                [{"type": "jira_import", "imported": out["imported"], "updated": out["updated"],
                  "stale": out["stale"], "count": out["count"], "jql": out["jql"]}])

    # check for stale stories (re-fetch mapped issues; mark changed ones)
    if re.search(r"(?:check|find|refresh).*(?:stale|changed).*(?:stor|issue|requirement)", low) or \
       re.search(r"stale\s+(?:stor(?:y|ies)|issues|requirements)", low):
        from ..services import story_import
        try:
            out = story_import.detect_stale(db, project)
        except story_import.StoryImportError as exc:
            return (f"Couldn't check: {exc}", [])
        db.commit()
        if not out["changed"]:
            return (f"Checked {out['checked']} imported stor(y/ies). None changed since import.", [])
        return (f"{len(out['changed'])} stor(y/ies) changed since import: "
                f"{', '.join(out['changed'])}. Their linked tests are tagged stale, so "
                "re-propose to refresh them.", [])

    # write coverage back to a story (gated external write)
    m = re.search(r"write\s*back\s+(?:coverage\s+)?(?:for\s+)?([A-Z][A-Z0-9]+-\d+)", text, re.I)
    if m:
        ref = m.group(1).upper()
        req = approvals.request(
            db, action="jira.writeback",
            title=f"Write test coverage back to {ref}",
            project_id=project.id, resource_type="jira_issue", resource_id=ref,
            payload={"arguments": {"ref": ref}}, requested_by=user.id, requested_by_kind="agent")
        db.commit()
        return (f"Queued a coverage write-back to {ref}. Approval #{req.id} is waiting. "
                "Nothing is posted to Jira until a human accepts.",
                [{"type": "approval_pending", "approval_id": req.id, "action": "jira.writeback",
                  "provider": "jira", "ref": ref}])

    return None
