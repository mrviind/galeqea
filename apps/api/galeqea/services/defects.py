"""File a tracked defect from a failing result, idempotently.

The same underlying failure (matched by its normalised fingerprint) maps to exactly
one tracker issue: the first sighting opens it (rich body + evidence), later sightings
comment on it and bump ``reopened_count`` instead of opening a duplicate. Jira Cloud is
the primary tracker (ADF body + real attachments); GitHub/GitLab issues are the
secondary path (Markdown body, evidence linked back to the GaleQEA run).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import audit
from ..integrations import git as git_tracker
from ..integrations import jira as jira_tracker
from ..integrations.base import IntegrationError
from ..intelligence import signatures
from ..models import (
    Artifact,
    DefectLink,
    DefectMap,
    IntegrationConnection,
    Project,
    Run,
    RunStepRecord,
    RunTest,
)
from ..models.base import utcnow

#: Tracker preference: Jira first, then GitHub, then GitLab.
_TRACKERS = ("jira", "github", "gitlab")
#: Evidence kinds worth attaching, most-useful first, and how many to cap at.
_EVIDENCE_KINDS = ("screenshot", "trace", "har", "console", "video")
_MAX_ATTACHMENTS = 6


class DefectError(RuntimeError):
    """Safe-to-surface problem while filing a defect."""


# --------------------------------------------------------------------------- #
# Resolution helpers
# --------------------------------------------------------------------------- #
def connected_tracker(db: Session, project_id: str, prefer: str | None = None) -> str | None:
    """The provider of the connected issue tracker, honouring an explicit preference."""
    order = ([prefer] if prefer else []) + [t for t in _TRACKERS if t != prefer]
    for provider in order:
        if provider is None:
            continue
        row = db.execute(
            select(IntegrationConnection).where(
                IntegrationConnection.project_id == project_id,
                IntegrationConnection.provider == provider,
                IntegrationConnection.enabled.is_(True))
        ).scalar_one_or_none()
        if row is not None:
            return provider
    return None


def fingerprint_for(run_test: RunTest) -> str:
    """Stable failure identity: the runner's signature, or computed on the fly."""
    if run_test.failure_signature:
        return run_test.failure_signature
    return signatures.compute_signature(
        run_test.error_type, run_test.error_message, run_test.test_key)


def _evidence(db: Session, run_test: RunTest) -> list[Artifact]:
    rows = list(db.execute(
        select(Artifact).where(Artifact.run_test_id == run_test.id)).scalars())
    rows.sort(key=lambda a: (_EVIDENCE_KINDS.index(a.kind) if a.kind in _EVIDENCE_KINDS else 99))
    return [a for a in rows if a.kind in _EVIDENCE_KINDS][:_MAX_ATTACHMENTS]


def _repro_steps(db: Session, run_test: RunTest) -> list[str]:
    steps = db.execute(
        select(RunStepRecord).where(RunStepRecord.run_test_id == run_test.id)
        .order_by(RunStepRecord.index)).scalars().all()
    out = []
    for s in steps:
        mark = "✗" if s.status in ("failed", "error") else "•"
        out.append(f"{mark} {s.intent or s.action}".strip())
    return out


def _facts(run: Run | None, run_test: RunTest) -> list[tuple[str, str]]:
    facts = [
        ("Test", f"{run_test.test_key or run_test.title}"),
        ("Browser", run_test.browser or "-"),
        ("Error", f"{run_test.error_type}: {run_test.error_message}".strip(": ")[:400] or "-"),
    ]
    if run is not None:
        facts += [
            ("Environment", run.environment or "-"),
            ("Build", run.git_sha[:12] or run.base_url or "-"),
            ("Run", f"#{run.number}"),
        ]
    return facts


def _links(run: Run | None, run_test: RunTest) -> list[tuple[str, str]]:
    if run is None:
        return []
    return [
        ("Run report", f"/api/projects/{run.project_id}/runs/{run.id}/report.json"),
        ("Run in GaleQEA", f"/runs/{run.id}"),
    ]


# --------------------------------------------------------------------------- #
# Bodies
# --------------------------------------------------------------------------- #
def _markdown_body(run: Run | None, run_test: RunTest, steps: list[str]) -> str:
    lines = ["**What failed**", ""]
    lines += [f"- {k}: {v}" for k, v in _facts(run, run_test)]
    if steps:
        lines += ["", "**Reproduction**", ""] + [f"- {s}" for s in steps]
    if run_test.error_message:
        lines += ["", "**Error**", "", "```", f"{run_test.error_type}",
                  run_test.error_message[:2000], "```"]
    links = _links(run, run_test)
    if links:
        lines += ["", "**Links**", ""] + [f"- [{label}]({href})" for label, href in links]
    lines += ["", "_Filed by GaleQEA from a failing test result._"]
    return "\n".join(lines)


def _title(run_test: RunTest) -> str:
    label = signatures.classify_error(run_test.error_type, run_test.error_message)
    unit = run_test.test_key or run_test.title or "test"
    return f"{label.replace('_', ' ')} · {unit}"[:250]


# --------------------------------------------------------------------------- #
# The main verb
# --------------------------------------------------------------------------- #
def file_defect(db: Session, project: Project, *, run_test_id: str, actor,
                provider: str | None = None) -> dict:
    """Create (or, on recurrence, comment on) the tracker issue for a failure and
    link it to the result. Idempotent per (project, provider, fingerprint)."""
    run_test = db.get(RunTest, run_test_id)
    if run_test is None:
        raise DefectError("no such result")
    provider = connected_tracker(db, project.id, prefer=provider)
    if provider is None:
        raise DefectError("no issue tracker is connected. Add Jira, GitHub or GitLab "
                          "under Settings → Integrations first")

    run = db.get(Run, run_test.run_id)
    fp = fingerprint_for(run_test)
    existing = db.execute(
        select(DefectMap).where(
            DefectMap.project_id == project.id, DefectMap.provider == provider,
            DefectMap.failure_fingerprint == fp)
    ).scalar_one_or_none()

    steps = _repro_steps(db, run_test)
    now = utcnow()

    if existing is not None:
        # Known failure recurred → comment + bump, never a duplicate issue.
        note = (f"Recurred in run #{run.number if run else '?'} "
                f"({run_test.test_key or run_test.title}, {run_test.browser}). "
                f"Seen {existing.reopened_count + 2} times.")
        try:
            if provider == "jira":
                jira_tracker.add_comment(db, project_id=project.id,
                                         issue_key=existing.issue_key, text=note)
            else:
                git_tracker.comment_issue(db, project_id=project.id, provider=provider,
                                          issue_key=existing.issue_key, body_markdown=note)
        except IntegrationError as exc:
            raise DefectError(str(exc)) from exc
        existing.reopened_count += 1
        existing.last_seen = now
        link = _ensure_link(db, run_test, provider, existing.issue_key, existing.url,
                            existing.status_cached)
        db.flush()
        audit.record(db, action="defect.recurred", actor_id=getattr(actor, "id", None),
                     actor_label=getattr(actor, "email", ""), project_id=project.id,
                     resource_type="defect", resource_id=existing.issue_key,
                     detail={"provider": provider, "fingerprint": fp,
                             "reopened_count": existing.reopened_count})
        return {"ok": True, "recurrence": True, "provider": provider,
                "key": existing.issue_key, "url": existing.url,
                "reopened_count": existing.reopened_count, "link_id": link.id}

    # First sighting → open the issue.
    try:
        if provider == "jira":
            body = _jira_adf_with_steps(run, run_test, steps)
            created = jira_tracker.create_issue(
                db, project_id=project.id, summary=_title(run_test), adf=body,
                labels=["defect", signatures.classify_error(run_test.error_type,
                                                            run_test.error_message)])
            _attach_evidence(db, project.id, created["key"], run_test)
        else:
            created = git_tracker.create_issue(
                db, project_id=project.id, provider=provider, title=_title(run_test),
                body_markdown=_markdown_body(run, run_test, steps), labels=["defect"])
    except IntegrationError as exc:
        raise DefectError(str(exc)) from exc

    dm = DefectMap(project_id=project.id, provider=provider, failure_fingerprint=fp,
                   issue_key=created["key"], issue_id=str(created.get("id") or ""),
                   url=created.get("url", ""), first_seen=now, last_seen=now)
    db.add(dm)
    link = _ensure_link(db, run_test, provider, created["key"], created.get("url", ""), "")
    db.flush()
    audit.record(db, action="defect.created", actor_id=getattr(actor, "id", None),
                 actor_label=getattr(actor, "email", ""), project_id=project.id,
                 resource_type="defect", resource_id=created["key"],
                 detail={"provider": provider, "fingerprint": fp, "url": created.get("url", "")})
    return {"ok": True, "recurrence": False, "provider": provider, "key": created["key"],
            "url": created.get("url", ""), "link_id": link.id}


def _jira_adf_with_steps(run, run_test, steps) -> dict:
    j = jira_tracker
    facts = _facts(run, run_test)
    content = [j.adf_heading("What failed", 3),
               j.adf_bullet_list([[j.adf_text(f"{k}: "), j.adf_text(v)] for k, v in facts])]
    if steps:
        content += [j.adf_heading("Reproduction", 3),
                    j.adf_bullet_list([[j.adf_text(s)] for s in steps])]
    if run_test.error_message:
        content += [j.adf_heading("Error", 3),
                    j.adf_code_block(f"{run_test.error_type}\n{run_test.error_message}")]
    links = _links(run, run_test)
    if links:
        content += [j.adf_heading("Links", 3),
                    j.adf_bullet_list([[j.adf_link(label, href)] for label, href in links])]
    content += [j.adf_paragraph(j.adf_text("Filed by GaleQEA from a failing test result."))]
    return j.adf_doc(*content)


def _attach_evidence(db: Session, project_id: str, issue_key: str, run_test: RunTest) -> int:
    from .artifacts import open_artifact
    attached = 0
    for art in _evidence(db, run_test):
        try:
            data, content_type, filename = open_artifact(art)
        except FileNotFoundError:
            continue
        try:
            jira_tracker.add_attachment(db, project_id=project_id, issue_key=issue_key,
                                        filename=filename, data=data, content_type=content_type)
            attached += 1
        except IntegrationError:
            # Evidence is best-effort; the issue itself is the important artefact.
            continue
    return attached


def _ensure_link(db: Session, run_test: RunTest, provider: str, key: str, url: str,
                 status: str) -> DefectLink:
    link = db.execute(
        select(DefectLink).where(DefectLink.result_id == run_test.id,
                                 DefectLink.tracker == provider,
                                 DefectLink.key == key)).scalar_one_or_none()
    if link is None:
        link = DefectLink(project_id=run_test.run.project_id if run_test.run else "",
                          result_id=run_test.id, test_case_id=run_test.test_case_id,
                          tracker=provider, key=key, url=url, status_cached=status,
                          last_synced=utcnow())
        db.add(link)
    return link


# --------------------------------------------------------------------------- #
# Status refresh + readiness helper
# --------------------------------------------------------------------------- #
def refresh_status(db: Session, project: Project, defect_map: DefectMap) -> DefectMap:
    """Refresh the cached tracker status (and the done/resolved flag)."""
    try:
        if defect_map.provider == "jira":
            info = jira_tracker.get_issue_status(db, project_id=project.id,
                                                 issue_key=defect_map.issue_key)
        else:
            info = git_tracker.issue_status(db, project_id=project.id,
                                            provider=defect_map.provider,
                                            issue_key=defect_map.issue_key)
    except IntegrationError as exc:
        raise DefectError(str(exc)) from exc
    defect_map.status_cached = info.get("status", "")
    defect_map.resolved = bool(info.get("done"))
    defect_map.last_synced = utcnow()
    # keep every DefectLink for this issue in sync too
    for link in db.execute(select(DefectLink).where(
            DefectLink.project_id == project.id, DefectLink.tracker == defect_map.provider,
            DefectLink.key == defect_map.issue_key)).scalars():
        link.status_cached = defect_map.status_cached
        link.last_synced = defect_map.last_synced
    db.flush()
    return defect_map


def open_defect_keys(db: Session, project_id: str, result_ids: list[str]) -> set[str]:
    """The tracker keys of *open* (not-done) defects linked to any of these results.
    Used by release readiness so a fixed-and-closed bug no longer counts as a blocker."""
    if not result_ids:
        return set()
    links = db.execute(select(DefectLink).where(
        DefectLink.project_id == project_id,
        DefectLink.result_id.in_(result_ids))).scalars().all()
    if not links:
        return set()
    keys = {link.key for link in links}
    resolved = {dm.issue_key for dm in db.execute(select(DefectMap).where(
        DefectMap.project_id == project_id, DefectMap.issue_key.in_(keys),
        DefectMap.resolved.is_(True))).scalars()}
    return {k for k in keys if k not in resolved}


def resolved_result_ids(db: Session, project_id: str, result_ids: list[str]) -> set[str]:
    """Result ids whose linked defect has been fixed (resolved/closed). Readiness
    stops counting these as open blockers even though the result itself is red."""
    if not result_ids:
        return set()
    links = db.execute(select(DefectLink).where(
        DefectLink.project_id == project_id,
        DefectLink.result_id.in_(result_ids))).scalars().all()
    if not links:
        return set()
    resolved_keys = {dm.issue_key for dm in db.execute(select(DefectMap).where(
        DefectMap.project_id == project_id,
        DefectMap.issue_key.in_({link.key for link in links}),
        DefectMap.resolved.is_(True))).scalars()}
    return {link.result_id for link in links if link.key in resolved_keys}


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #
def defect_card(dm: DefectMap, *, recurrence: bool | None = None) -> dict:
    return {"type": "defect_card", "provider": dm.provider, "key": dm.issue_key,
            "url": dm.url, "status": dm.status_cached or "open",
            "resolved": dm.resolved, "reopened_count": dm.reopened_count,
            "recurrence": recurrence}
