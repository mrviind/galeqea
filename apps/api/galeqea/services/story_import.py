"""Import Jira stories as requirements (through the review gate).

A sprint / fixVersion / JQL selection is pulled from Jira, each story's ADF
description is rendered to Markdown, and each becomes a ``RequirementItem`` whose
ref *is* the Jira key, with a ``JiraIssueMap`` source anchor so re-imports are
idempotent and a later description change marks the linked tests stale. The
generated tests then flow through the normal requirements→tests proposal review.
"""

from __future__ import annotations

import hashlib
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import audit
from ..integrations import jira as jira_tracker
from ..integrations.base import IntegrationError, load_connection
from ..models import (
    DocKind,
    JiraIssueMap,
    Project,
    RequirementDoc,
    RequirementItem,
    TestCase,
)
from ..models.base import utcnow

_PRIORITY_RISK = {"highest": "critical", "high": "high", "medium": "medium",
                  "low": "low", "lowest": "low"}
_STALE_TAG = "stale-requirement"


class StoryImportError(RuntimeError):
    """Safe-to-surface problem importing stories."""


def _hash(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()[:32]


def resolve_jql(selector: str, *, project_key: str) -> str:
    """Turn a plain-English selector into JQL, always scoped to the Jira project."""
    s = (selector or "").strip()
    scope = f'project = "{project_key}"'
    low = s.lower()
    # explicit JQL passthrough
    if low.startswith("jql:"):
        return s[4:].strip()
    if any(tok in low for tok in (" = ", " in ", "project =", "sprint ", "fixversion")) and (
            "=" in s or " in " in low):
        # looks like the caller already wrote JQL
        if "project" in low:
            return s
        return f"{scope} AND ({s})"
    m = re.search(r"fix\s*version\s+(.+)$", s, re.I) or re.search(r"\bversion\s+(.+)$", s, re.I)
    if m:
        return f'{scope} AND fixVersion = "{m.group(1).strip().strip(chr(34))}"'
    m = re.search(r"sprint\s+(.+)$", s, re.I)
    if m:
        name = m.group(1).strip()
        if name.lower() in ("open", "active", "current", "current sprint", "open sprints"):
            return f"{scope} AND sprint in openSprints()"
        return f'{scope} AND sprint = "{name.strip(chr(34))}"'
    if low in ("open sprint", "current sprint", "active sprint", "open sprints"):
        return f"{scope} AND sprint in openSprints()"
    # default: all stories in the project
    return f"{scope} AND issuetype in (Story, Bug, Task)"


def _doc(db: Session, project_id: str) -> RequirementDoc:
    doc = db.execute(select(RequirementDoc).where(
        RequirementDoc.project_id == project_id,
        RequirementDoc.source_filename == "jira:stories")).scalars().first()
    if doc is None:
        doc = RequirementDoc(project_id=project_id, title="Jira stories",
                             kind=DocKind.REQUIREMENT, source_filename="jira:stories",
                             mime_type="application/json", meta={"source": "jira"})
        db.add(doc)
        db.flush()
    return doc


def import_stories(db: Session, project: Project, *, selector: str, actor) -> dict:
    try:
        connection = load_connection(db, project_id=project.id, provider="jira")
        project_key = connection.require("project_key")
        jql = resolve_jql(selector, project_key=project_key)
        issues = jira_tracker.search_issues(db, project_id=project.id, jql=jql)
    except IntegrationError as exc:
        raise StoryImportError(str(exc)) from exc

    doc = _doc(db, project.id)
    imported, updated, stale = [], [], []
    for issue in issues:
        key = issue.get("key", "")
        issue_id = str(issue.get("id") or key)
        fields = issue.get("fields", {}) or {}
        summary = fields.get("summary", "") or key
        text = jira_tracker.adf_to_markdown(fields.get("description")) or ""
        priority = ((fields.get("priority") or {}).get("name") or "medium").lower()
        risk = _PRIORITY_RISK.get(priority, "medium")
        dhash = _hash(text)

        item = db.execute(select(RequirementItem).where(
            RequirementItem.project_id == project.id, RequirementItem.ref == key)
        ).scalars().first()
        if item is None:
            item = RequirementItem(doc_id=doc.id, project_id=project.id, ref=key,
                                   title=summary[:400], text=text, kind="functional", risk=risk)
            db.add(item)
            db.flush()
            imported.append(key)
        else:
            item.title = summary[:400]
            item.text = text
            item.risk = risk
            updated.append(key)

        m = db.execute(select(JiraIssueMap).where(
            JiraIssueMap.project_id == project.id,
            JiraIssueMap.issue_id == issue_id)).scalars().first()
        changed = m is not None and m.description_hash and m.description_hash != dhash
        if m is None:
            m = JiraIssueMap(project_id=project.id, issue_id=issue_id, issue_key=key,
                             requirement_ref=key)
            db.add(m)
        m.issue_key = key
        m.requirement_ref = key
        m.remote_updated = str(fields.get("updated") or "")
        m.description_hash = dhash
        m.last_checked_at = utcnow()
        if changed:
            m.stale = True
            _mark_linked_tests_stale(db, project.id, key)
            stale.append(key)

    db.flush()
    audit.record(db, action="jira.stories_imported", actor_id=getattr(actor, "id", None),
                 actor_label=getattr(actor, "email", ""), project_id=project.id,
                 resource_type="requirement_doc", resource_id=doc.id,
                 detail={"jql": jql, "imported": len(imported), "updated": len(updated),
                         "stale": len(stale)})
    return {"ok": True, "jql": jql, "imported": imported, "updated": updated,
            "stale": stale, "doc_id": doc.id, "count": len(issues)}


def _mark_linked_tests_stale(db: Session, project_id: str, ref: str) -> int:
    n = 0
    for tc in db.execute(select(TestCase).where(TestCase.project_id == project_id)).scalars():
        if ref in (tc.requirement_refs or []) and _STALE_TAG not in (tc.tags or []):
            tc.tags = [*(tc.tags or []), _STALE_TAG]
            n += 1
    return n


def detect_stale(db: Session, project: Project) -> dict:
    """Re-fetch every mapped story and flag the ones whose description changed since
    import. Their linked tests get the stale tag + a re-propose prompt. Poll this on a
    schedule (or a jira:issue_updated webhook) to keep tests honest."""
    maps = db.execute(select(JiraIssueMap).where(
        JiraIssueMap.project_id == project.id)).scalars().all()
    if not maps:
        return {"ok": True, "checked": 0, "changed": []}
    keys = [m.issue_key for m in maps]
    try:
        jql = "issuekey in (" + ",".join(keys) + ")"
        issues = jira_tracker.search_issues(db, project_id=project.id, jql=jql)
    except IntegrationError as exc:
        raise StoryImportError(str(exc)) from exc
    by_key = {i.get("key"): i for i in issues}
    changed = []
    for m in maps:
        issue = by_key.get(m.issue_key)
        if issue is None:
            continue
        text = jira_tracker.adf_to_markdown((issue.get("fields", {}) or {}).get("description"))
        dhash = _hash(text)
        m.last_checked_at = utcnow()
        if m.description_hash and dhash != m.description_hash:
            m.description_hash = dhash
            m.stale = True
            item = db.execute(select(RequirementItem).where(
                RequirementItem.project_id == project.id,
                RequirementItem.ref == m.issue_key)).scalars().first()
            if item is not None:
                item.text = text
            _mark_linked_tests_stale(db, project.id, m.issue_key)
            changed.append(m.issue_key)
    db.flush()
    return {"ok": True, "checked": len(maps), "changed": changed}


def write_back_coverage(db: Session, project: Project, *, ref: str, actor) -> dict:
    """Tell the Jira story which approved GaleQEA tests cover it: a comment now, and a
    "Test" issue link once the test exists as a Jira/Xray issue (see 6-C)."""
    m = db.execute(select(JiraIssueMap).where(
        JiraIssueMap.project_id == project.id, JiraIssueMap.issue_key == ref)).scalars().first()
    if m is None:
        raise StoryImportError(f"{ref} was not imported from Jira")
    from ..models import TestStatus
    tests = [tc for tc in db.execute(select(TestCase).where(
        TestCase.project_id == project.id, TestCase.status == TestStatus.APPROVED)).scalars()
        if ref in (tc.requirement_refs or [])]
    if not tests:
        return {"ok": True, "ref": ref, "written": 0, "note": "no approved tests cover it yet"}
    lines = [f"- {tc.key}: {tc.title}" for tc in tests]
    comment = "GaleQEA test coverage for this story:\n\n" + "\n".join(lines)
    try:
        jira_tracker.add_comment(db, project_id=project.id, issue_key=ref, text=comment)
    except IntegrationError as exc:
        raise StoryImportError(str(exc)) from exc
    m.coverage_written = True
    db.flush()
    audit.record(db, action="jira.coverage_written", actor_id=getattr(actor, "id", None),
                 project_id=project.id, resource_type="jira_issue", resource_id=ref,
                 detail={"tests": [tc.key for tc in tests]})
    return {"ok": True, "ref": ref, "written": len(tests), "tests": [tc.key for tc in tests]}
