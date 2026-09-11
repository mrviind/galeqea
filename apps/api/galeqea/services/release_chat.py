"""Deterministic chat verbs for release management, no model needed.

Parses a QA lead's plain-English commands into release-service calls and returns the
cards. Returns None when the text isn't a release command, so the orchestrator falls
through to its other paths.
"""

from __future__ import annotations

import re
from datetime import UTC

from sqlalchemy.orm import Session

from ..models import Project, Role, User
from ..models.base import utcnow
from . import release

_MONTHS = {m: i for i, full in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1) for m in (full, full[:3])}

# Phrase → (metric, op, value). Ratios are 0..1.
_CRITERIA = [
    (r"pass\s*rate\s*(?:>=|≥|of|at least)?\s*(\d+)\s*%", "pass_rate", ">=", lambda p: int(p) / 100),
    (r"(?:0|no|zero)\s+(?:open\s+)?blockers?", "open_blockers", "==", lambda _: 0),
    (r"p1\s+(?:requirements?\s+)?(\d+)\s*%", "p1_requirement_coverage", ">=", lambda p: int(p) / 100),
    (r"(?:requirement|req)\s*coverage\s*(?:>=|≥|of|at least)?\s*(\d+)\s*%",
     "requirement_coverage", ">=", lambda p: int(p) / 100),
    (r"flaky\s*(?:<=|≤|under|below|at most)?\s*(\d+)\s*%", "flaky_rate", "<=", lambda p: int(p) / 100),
    (r"automation\s*(?:>=|≥|of|at least)?\s*(\d+)\s*%", "automation_ratio", ">=", lambda p: int(p) / 100),
]


def _parse_date(s: str):
    from datetime import datetime

    s = s.strip()
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=UTC)
    m = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?", s)
    if m and m[1][:3].lower() in _MONTHS:
        year = int(m[3]) if m[3] else utcnow().year
        return datetime(year, _MONTHS[m[1][:3].lower()], int(m[2]), tzinfo=UTC)
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3,9})", s)   # "15 Sept"
    if m and m[2][:3].lower() in _MONTHS:
        return datetime(utcnow().year, _MONTHS[m[2][:3].lower()], int(m[1]), tzinfo=UTC)
    return None


def _parse_criteria(text: str) -> list[dict]:
    rules = []
    for pattern, metric, op, conv in _CRITERIA:
        m = re.search(pattern, text, re.I)
        if m:
            arg = m.group(1) if m.groups() else None
            rules.append({"metric": metric, "op": op, "value": conv(arg)})
    return rules


def _configurations(text: str) -> list[dict]:
    browsers = [b for b in ("chromium", "firefox", "webkit", "chrome", "safari")
                if re.search(rf"\b{b}\b", text, re.I)]
    browsers = ["chromium" if b in ("chrome",) else "webkit" if b == "safari" else b
                for b in browsers] or ["chromium"]
    viewports = []
    if re.search(r"\bmobile\b", text, re.I):
        viewports.append("mobile")
    if re.search(r"\bdesktop\b", text, re.I):
        viewports.append("desktop")
    viewports = viewports or ["desktop"]
    return [{"browser": b, "viewport": v} for b in browsers for v in viewports]


def try_handle(db: Session, project: Project, text: str, user: User) -> tuple[str, list[dict]] | None:
    """(reply text, blocks) if this is a release command, else None."""
    t = text.strip()
    low = t.lower()

    # create release / milestone (idempotent per project+version)
    m = re.search(r"(?:create|new|start|add)\s+(?:release|milestone|version)\s+([\w.\-]+)", low)
    if m:
        version = m.group(1)
        existing = release.milestone_for(db, project.id, version)
        if existing is not None:
            return (f"Release {version} already exists ({_milestone_summary(existing)}). Say "
                    f"\"exit criteria: …\" to set its gate, or \"plan … for {version}\".",
                    [release.milestone_card(existing)])
        due = re.search(r"due\s+(.+)$", t, re.I)
        target = _parse_date(due.group(1)) if due else None
        ms = release.create_milestone(db, project, name=f"Release {version}", version=version,
                                      target_date=target)
        db.commit()
        return (f"Created release {_milestone_summary(ms)}.", [release.milestone_card(ms)])

    # exit criteria
    if low.startswith("exit criteria") or ("exit criteria" in low and ":" in t):
        rules = _parse_criteria(t)
        ms = _target_milestone(db, project, t)
        if ms is None:
            return ("Which release are these exit criteria for? Create one first, e.g. "
                    "\"create release 1.4\".", [])
        if not rules:
            return ("I couldn't read any criteria. Try: \"exit criteria: pass rate ≥ 95%, "
                    "0 open blockers, P1 requirements 100% covered, flaky ≤ 2%\".", [])
        release.set_exit_criteria(db, ms, rules)
        db.commit()
        return (f"Set {len(rules)} exit criteria for {ms.version}.",
                [release.milestone_card(ms)])

    # add environment
    m = re.search(r"add\s+environment\s+(\S+)\s+(https?://\S+)", t, re.I)
    if m:
        env = release.add_environment(db, project, name=m.group(1), base_url=m.group(2))
        db.commit()
        return (f"Added environment {env.name} → {env.base_url}.",
                [release.environment_card(env)])

    # set / change an environment's base URL (create it if new)
    m = re.search(r"set\s+base\s*url\s+for\s+(\S+)\s+to\s+(https?://\S+)", t, re.I)
    if m:
        from sqlalchemy import func, select

        from ..models import Environment
        env = db.execute(select(Environment).where(
            Environment.project_id == project.id,
            func.lower(Environment.name) == m.group(1).lower())).scalars().first()
        if env is None:
            env = release.add_environment(db, project, name=m.group(1), base_url=m.group(2))
            verb = "Added"
        else:
            env.base_url = m.group(2)
            verb = "Updated"
        db.commit()
        return (f"{verb} environment {env.name} → {env.base_url}.",
                [release.environment_card(env)])

    # plan
    m = re.search(r"\bplan\s+(\w+)?\s*(?:for|on)\s+(?:release\s+)?([\w.\-]+)", low)
    if m and ("plan" in low):
        name = (m.group(1) or "regression").capitalize()
        version = m.group(2)
        ms = release.milestone_for(db, project.id, version)
        if ms is None:
            return (f"No release {version} yet. Create it first "
                    f"(\"create release {version}\").", [])
        configs = _configurations(t)
        plan, count = release.create_plan(db, project, milestone=ms,
                                          name=f"{name} {version}", selection={"query": {}},
                                          configurations=configs)
        db.commit()
        return (f"Planned {name} for {version}: {count} case(s) × {len(configs)} "
                f"configuration(s).", [release.plan_card(plan, count)])

    # start cycle(s)
    if re.search(r"\bstart\s+cycles?\b", low):
        ms = _target_milestone(db, project, t)
        plan = _latest_plan(db, project, ms)
        if plan is None:
            return ("There's no plan to start a cycle from yet. Say "
                    "\"plan regression for <version> on chromium\".", [])
        cycles = release.start_cycles(db, plan)
        db.commit()
        return (f"Started {len(cycles)} cycle(s) for {plan.name}.",
                [release.cycle_card(c) for c in cycles])

    # readiness
    if re.search(r"(?:ready|readiness|go/?no.?go).*release\s+([\w.\-]+)", low) or \
       re.search(r"are we ready to (?:release|ship)\s+([\w.\-]+)", low):
        version = re.search(r"release\s+([\w.\-]+)|ship\s+([\w.\-]+)", low)
        ver = next((g for g in (version.groups() if version else []) if g), None)
        ms = release.milestone_for(db, project.id, ver) if ver else _target_milestone(db, project, t)
        if ms is None:
            return ("Which release? e.g. \"are we ready to release 1.4?\"", [])
        ev = release.evaluate_readiness(db, ms)
        verdict = ev["verdict"].upper().replace("_", "-")
        met = sum(1 for c in ev["criteria"] if c["met"])
        total = len(ev["criteria"])
        return (f"Release {ms.version}: {verdict} ({met}/{total} exit criteria met).",
                [release.readiness_card(ev)])

    # sign off
    m = re.search(r"sign\s*off\s+([\w.\-]+)\s+as\s+(go|no.?go)", low)
    if m:
        ver, decision = m.group(1), ("go" if m.group(2).replace("-", "").replace(" ", "") == "go" else "no_go")
        ms = release.milestone_for(db, project.id, ver)
        if ms is None:
            return (f"No release {ver} to sign off.", [])
        from ..core.approvals import SelfApprovalError
        try:
            release.sign_off(db, ms, decider=user, decision=decision,
                             note=_signoff_note(t))
            db.commit()
        except SelfApprovalError as exc:
            return (f"Can't sign off: {exc}", [])
        except ValueError as exc:
            return (f"Can't sign off: {exc}", [])
        return (f"Signed off {ver} as {decision.upper().replace('_', '-')} "
                f"by {user.email}. This decision is immutable.",
                [release.milestone_card(ms)])

    # archive / delete a release
    m = re.search(r"(archive|delete|remove)\s+(?:release|milestone|version)\s+([\w.\-]+)", low)
    if m:
        action, ver = m.group(1), m.group(2)
        ms = release.milestone_for(db, project.id, ver)
        if ms is None:
            return (f"No release {ver}.", [])
        if action == "archive":
            release.archive_milestone(db, ms)
            db.commit()
            return (f"Archived release {ver}. It's hidden from the list (say \"show archived\" to see it).", [])
        # delete is destructive → admin only
        if not user.at_least(Role.ADMIN):
            return ("Deleting a release needs the admin role. An approver can "
                    f"\"archive release {ver}\" instead.", [])
        release.delete_milestone(db, ms)
        db.commit()
        return (f"Deleted release {ver}.", [])

    # publish a release report to Confluence (gated external write)
    m = re.search(r"publish\s+(?:release\s+)?report\s+(?:for\s+)?([\w.\-]+)"
                  r"(?:\s+to\s+confluence)?(?:\s+space\s+(\S+))?", low)
    if m and "publish" in low:
        version, space = m.group(1), (m.group(2) or "")
        ms = release.milestone_for(db, project.id, version)
        if ms is None:
            return (f"No release {version} to publish.", [])
        from ..core import approvals
        req = approvals.request(
            db, action="report.publish",
            title=f"Publish release {version} report to Confluence"
            + (f" (space {space})" if space else ""),
            project_id=project.id, resource_type="milestone", resource_id=ms.id,
            payload={"arguments": {"version": version, "space_key": space}},
            requested_by=user.id, requested_by_kind="agent")
        db.commit()
        return (f"Queued publishing the {version} release report to Confluence"
                + (f" space {space}" if space else "")
                + f". Approval #{req.id} is waiting. Nothing is published until a human accepts.",
                [{"type": "approval_pending", "approval_id": req.id, "action": "report.publish",
                  "provider": "confluence", "version": version}])

    # release report
    m = re.search(r"release report\s+([\w.\-]+)", low)
    if m:
        ms = release.milestone_for(db, project.id, m.group(1))
        if ms is None:
            return (f"No release {m.group(1)} to report on.", [])
        from ..reports import release_report
        report = release_report.build(db, ms)
        return (f"Release report for {ms.version}: {report['readiness']['verdict'].upper()}.",
                [{"type": "release_report", **report}])

    return None


def _milestone_summary(ms) -> str:
    """A complete one-liner for the transcript / a screen reader (not just the card)."""
    parts = [f"{ms.version} ({ms.status})"]
    if ms.target_date:
        parts.append(f"due {ms.target_date.date().isoformat()}")
    n = len(ms.exit_criteria or [])
    parts.append(f"{n} exit criteria" if n else "no exit criteria yet")
    if ms.signoff:
        parts.append(f"signed off {ms.signoff['decision']}")
    return ", ".join(parts)


def _signoff_note(text: str) -> str:
    m = re.search(r"(?:note|because|reason)[:\s]+(.+)$", text, re.I)
    return m.group(1).strip() if m else ""


def _target_milestone(db, project, text):
    m = re.search(r"([\w.\-]*\d[\w.\-]*)", text)
    if m:
        found = release.milestone_for(db, project.id, m.group(1))
        if found:
            return found
    from sqlalchemy import select

    from ..models import Milestone, MilestoneStatus
    return db.execute(
        select(Milestone).where(Milestone.project_id == project.id,
                                Milestone.status == MilestoneStatus.ACTIVE)
        .order_by(Milestone.created_at.desc())
    ).scalars().first()


def _latest_plan(db, project, milestone):
    from sqlalchemy import select

    from ..models import TestPlan
    q = select(TestPlan).where(TestPlan.project_id == project.id)
    if milestone is not None:
        q = q.where(TestPlan.milestone_id == milestone.id)
    return db.execute(q.order_by(TestPlan.created_at.desc())).scalars().first()
