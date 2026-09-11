"""Exploratory session lifecycle.

The session lives here rather than in the supervisor because it has to survive
across many runner round trips: the runner asks "what next?" thirty times, and
each answer depends on everything already tried.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai.agents import explorer as policy
from ..ai.agents.findings import Finding, check, dedupe
from ..config import settings
from ..core import audit
from ..core.events import Ev, Event, bus
from ..db import session_scope
from ..models import ExplorationFinding, ExplorationSession, Project
from ..models.base import utcnow

log = logging.getLogger("galeqea.exploration")

#: Live policy state, keyed by session id. Deliberately in-process: a session is
#: a single continuous browser conversation, and persisting the working set
#: would buy nothing but complexity.
_STATE: dict[str, policy.ExplorerState] = {}
_SEEN: dict[str, set[str]] = {}
_TASKS: dict[str, asyncio.Task] = {}
_GUARDRAILS: dict[str, dict] = {}


async def start(
    db: Session,
    *,
    project_id: str,
    charter: str,
    environment: str = "",
    base_url: str = "",
    max_steps: int = 30,
    allow_transactional: bool = False,
    guardrails: dict | None = None,
    started_by: str | None = None,
) -> ExplorationSession:
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"unknown project {project_id}")

    env = environment or project.default_environment or "default"
    # An explicit base_url (a journey target that isn't a named environment) wins;
    # otherwise fall back to the configured environment URL.
    base_url = base_url or (project.environments or {}).get(env, "")
    if not base_url:
        raise ValueError(
            f"environment {env!r} has no URL configured. Exploration needs somewhere to go"
        )
    guardrails = guardrails or {}
    # Guardrails bound the exploration: read-only turns off any transactional
    # control, and the avoid-list / rate limit ride the explorer plan.
    if guardrails.get("read_only"):
        allow_transactional = False

    provider = _provider()
    session = ExplorationSession(
        project_id=project_id,
        charter=charter or "Explore the application and report anything surprising.",
        environment=env,
        base_url=base_url,
        strategy="model" if provider is not None else "deterministic",
        model=getattr(provider, "model", "") if provider is not None else "",
        max_steps=max(4, min(max_steps, 120)),
    )
    db.add(session)
    db.flush()

    audit.record(
        db, action="exploration.started", actor_id=started_by, project_id=project_id,
        resource_type="exploration_session", resource_id=session.id,
        detail={"charter": session.charter, "strategy": session.strategy,
                "max_steps": session.max_steps, "url": base_url,
                "allow_transactional": allow_transactional},
    )
    db.commit()

    _STATE[session.id] = policy.ExplorerState(
        charter=session.charter,
        base_url=base_url,
        allow_transactional=allow_transactional,
    )
    _SEEN[session.id] = set()
    _GUARDRAILS[session.id] = guardrails
    _TASKS[session.id] = asyncio.create_task(_drive(session.id, project_id))
    return session


async def _drive(session_id: str, project_id: str) -> None:
    """Spawn the runner in exploration mode and mediate its questions."""
    from ..engine.supervisor import RunSupervisor

    artifacts = Path(settings.artifacts_dir) / f"explore-{session_id}"
    artifacts.mkdir(parents=True, exist_ok=True)

    guardrails = _GUARDRAILS.get(session_id, {})
    with session_scope() as db:
        session = db.get(ExplorationSession, session_id)
        plan = {
            "runId": f"explore:{session_id}",
            "baseUrl": session.base_url,
            "browsers": ["chromium"],
            "headless": True,
            "trace": False,
            "artifactsDir": str(artifacts),
            "tests": [],
            # Honour the target's rate limit during exploration too.
            "rateLimitRps": guardrails.get("rate_limit_rps"),
            "explore": {
                "id": session_id,
                "charter": session.charter,
                "baseUrl": session.base_url,
                "maxSteps": session.max_steps,
                # The explorer stays within the charter's area and off the
                # destructive-action avoid-list; read-only means fill-not-submit.
                "readOnly": bool(guardrails.get("read_only")),
                "avoid": guardrails.get("avoid") or [],
            },
        }

    supervisor = RunSupervisor(provider=_provider())
    try:
        await supervisor.explore(plan, session_id=session_id, project_id=project_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("exploration %s failed", session_id)
        with session_scope() as db:
            record = db.get(ExplorationSession, session_id)
            if record:
                record.status = "error"
                record.summary = f"{type(exc).__name__}: {exc}"
                record.finished_at = utcnow()
    finally:
        _STATE.pop(session_id, None)
        _SEEN.pop(session_id, None)
        _TASKS.pop(session_id, None)
        _GUARDRAILS.pop(session_id, None)


# --------------------------------------------------------------------------- #
async def decide(event: dict, *, project_id: str) -> dict:
    """Answer one `explore_decide` question from the runner."""
    session_id = event.get("sessionId", "")
    observation = event.get("observation") or {}
    state = _STATE.setdefault(session_id, policy.ExplorerState())

    # Deterministic checks run on every observation regardless of strategy: a
    # 500 response should never depend on a model noticing it.
    await _record_findings(
        session_id=session_id, project_id=project_id,
        findings=check(observation, event.get("previous")),
        state=state,
    )

    if event.get("finalPass"):
        return {"requestId": event["requestId"], "ok": False, "action": "finish"}

    decision = await policy.decide(observation, state, provider=_provider())

    candidates = observation.get("candidates") or []
    label = "-"
    if decision.target_index is not None and 0 <= decision.target_index < len(candidates):
        target = candidates[decision.target_index]
        label = target.get("name") or target.get("role") or "?"
        state.touched.add(policy.element_key(target))
    state.remember(decision, observation, label)

    with session_scope() as db:
        record = db.get(ExplorationSession, session_id)
        if record:
            record.steps_taken = len(state.trail)
            record.trail = state.trail[-200:]
            record.screens_seen = len(state.visited_routes)

    return decision.as_response(event["requestId"])


async def _record_findings(
    *, session_id: str, project_id: str, findings: list[Finding], state: policy.ExplorerState
) -> None:
    """Record findings once, ever.

    Three layers of de-duplication, because the same defect arrives three ways:

    * within one batch - two unlabelled inputs on a page both raise `form-label`;
    * within one session - every screen visit re-checks the same page;
    * across sessions - exploring weekly would otherwise file the same finding
      fifty-two times, which is how a findings list becomes a thing nobody opens.
    """
    if not findings:
        return

    # Layer 1: within this batch.
    findings = dedupe(findings)

    # Layer 2: within this session.
    seen = _SEEN.setdefault(session_id, set())
    fresh = [f for f in findings if f.signature not in seen]
    if not fresh:
        return
    for finding in fresh:
        seen.add(finding.signature)

    published: list[Finding] = []
    with session_scope() as db:
        for finding in fresh:
            # Layer 3: across sessions. An open finding stays one finding; only
            # its occurrence count and last-seen session change.
            existing = db.execute(
                select(ExplorationFinding).where(
                    ExplorationFinding.project_id == project_id,
                    ExplorationFinding.signature == finding.signature,
                    ExplorationFinding.status.in_(["new", "accepted"]),
                )
            ).scalars().first()
            if existing is not None:
                existing.evidence = {
                    **(existing.evidence or {}),
                    "occurrences": (existing.evidence or {}).get("occurrences", 1) + 1,
                    "last_session_id": session_id,
                }
                state.recurring_findings += 1
                continue

            state.new_findings += 1
            published.append(finding)
            db.add(ExplorationFinding(
                project_id=project_id,
                session_id=session_id,
                kind=finding.kind,
                severity=finding.severity,
                title=finding.title[:400],
                detail=finding.detail,
                url=finding.url[:600],
                # The trail *is* the reproduction: these are the actions that
                # reached this state, in order.
                reproduction=list(state.trail[-12:]),
                evidence=finding.evidence,
                confidence=finding.confidence,
                found_by=finding.found_by,
                signature=finding.signature,
            ))

    for finding in published:
        await bus.publish(Event(
            type=Ev.NOTIFICATION, project_id=project_id,
            payload={"kind": "exploration_finding", "session_id": session_id, **finding.as_dict()},
        ))


async def finish(*, session_id: str, project_id: str, payload: dict) -> None:
    with session_scope() as db:
        session = db.get(ExplorationSession, session_id)
        if session is None:
            return
        count = db.execute(
            select(ExplorationFinding).where(ExplorationFinding.session_id == session_id)
        ).scalars().all()
        high = [f for f in count if f.severity == "high"]

        session.status = "completed"
        session.steps_taken = payload.get("steps", session.steps_taken)
        session.finished_at = utcnow()
        state = _STATE.get(session_id)
        skipped = (state.skipped if state else []) or []
        fresh = state.new_findings if state else len(count)
        recurring = state.recurring_findings if state else 0

        headline = f"Explored {session.screens_seen} screen(s) in {session.steps_taken} step(s); "
        if fresh:
            headline += f"{fresh} new finding(s)"
            if high:
                headline += f", {len(high)} high severity"
            headline += "."
        elif recurring:
            # Not the same as finding nothing: these defects are still there.
            headline += (
                f"no new findings, but {recurring} already-known issue(s) are still present."
            )
        else:
            headline += "nothing worth reporting."

        if skipped:
            names = ", ".join(sorted({s["label"] for s in skipped})[:4])
            headline += f" Deliberately did not click {len(skipped)} guarded control(s): {names}."
        session.summary = headline
        session.trail = (session.trail or [])[-200:]
        summary, findings_count = session.summary, len(count)

    await bus.publish(Event(
        type=Ev.NOTIFICATION, project_id=project_id,
        payload={"kind": "exploration_finished", "session_id": session_id,
                 "summary": summary, "findings": findings_count},
    ))


def _provider():
    from ..ai.providers.registry import default_provider

    return default_provider() if settings.ai_enabled else None


async def explore_now(db, *, project, journey, area: str = "", role: str = "a user",
                      minutes: int = 10, actor: str | None = None,
                      timeout_slack: float = 30.0) -> dict:
    """A guardrail-bounded, timeboxed exploration of an area, as a role. Awaits the
    session (bounded) and returns it with its findings for an anomaly card."""
    from urllib.parse import urljoin

    minutes = max(1, min(int(minutes or 10), 15))  # N ≤ 15, default 10
    area = area or "/"
    base = urljoin(journey.target if journey.target.endswith("/") else journey.target + "/",
                   area.lstrip("/"))
    guardrails = (journey.guardrails or {}) if journey else {}
    charter = (f"As {role}, explore {area} for up to {minutes} minute(s). Exercise safe "
               "controls only; report anomalies (errors, dead ends, slow pages, layout "
               "breaks, accessibility barriers).")
    session = await start(
        db, project_id=project.id, charter=charter, base_url=base,
        environment=journey.environment if journey else "",
        max_steps=min(60, minutes * 4), allow_transactional=False,
        guardrails=guardrails, started_by=actor,
    )
    task = _TASKS.get(session.id)
    timed_out = False
    if task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=minutes * 60 + timeout_slack)
        except TimeoutError:
            timed_out = True
    db.expire_all()
    session = db.get(ExplorationSession, session.id)
    return {"session": session, "findings": findings_for(db, session.id),
            "timed_out": timed_out, "role": role, "area": area, "minutes": minutes}


def _finding_dict(f: ExplorationFinding) -> dict:
    return {
        "id": f.id, "kind": f.kind, "severity": f.severity, "title": f.title,
        "detail": f.detail, "url": f.url, "confidence": f.confidence,
        "status": f.status, "promoted_test_id": f.promoted_test_id,
        "found_by": f.found_by, "signature": f.signature,
    }


def findings_for(db, session_id: str) -> list[dict]:
    rows = db.execute(
        select(ExplorationFinding).where(ExplorationFinding.session_id == session_id)
        .order_by(ExplorationFinding.severity.desc())
    ).scalars()
    return [_finding_dict(f) for f in rows]


#: Which runner step best re-checks each anomaly kind, so a promoted test actually
#: guards against the regression rather than just reopening the page.
def _steps_for_finding(kind: str, url: str) -> list[dict]:
    from ..models import StepAction
    goto = {"action": StepAction.GOTO, "intent": f"Open {url}", "value": {"url": url}}
    if kind in ("dead_end", "broken_link", "server_error"):
        return [goto]  # the GOTO itself asserts a <400 status in the runner
    if kind == "accessibility":
        return [goto, {"action": StepAction.ASSERT_A11Y, "intent": "No serious/critical axe violations",
                       "value": {"tags": ["wcag2a", "wcag2aa", "wcag22aa"], "fail_on": ["serious", "critical"]}}]
    if kind == "slow_response":
        return [goto, {"action": StepAction.ASSERT_PERF, "intent": "Within Core Web Vitals budget",
                       "value": {"lcp_ms": 2500, "cls": 0.1}}]
    body = {"action": StepAction.EXPECT_VISIBLE, "intent": "The page still renders",
            "target": {"ladder": [{"kind": "css", "value": "body"}]}}
    return [goto, body]


def promote_finding(db, *, project_id: str, finding_id: str, journey=None,
                    actor: str | None = None) -> dict:
    """Turn an exploratory finding into a proposed TestCase, so a one-off
    observation becomes a guarded, re-runnable check."""
    from ..models import Project, TestCase, TestCategory, TestStatus, TestStep

    finding = db.get(ExplorationFinding, finding_id)
    if finding is None or finding.project_id != project_id:
        return {"ok": False, "error": "no such finding"}
    if finding.promoted_test_id:
        return {"ok": True, "already": True, "test_id": finding.promoted_test_id}

    project = db.get(Project, project_id)
    key = f"{project.key}-EXP-{finding.id[:6].upper()}"
    steps = _steps_for_finding(finding.kind, finding.url or (journey.target if journey else ""))
    tc = TestCase(
        project_id=project_id, key=key,
        title=(finding.title or f"Exploratory: {finding.kind}")[:400],
        description=finding.detail[:2000],
        status=TestStatus.PROPOSED, category=TestCategory.AUTOMATED,
        tags=["exploratory", finding.kind],
        risk=finding.severity if finding.severity in ("high", "medium", "low") else "medium",
        provenance={"origin": "exploratory", "finding_id": finding.id, "kind": finding.kind,
                    "session_id": finding.session_id, "deterministic": True},
    )
    db.add(tc)
    db.flush()
    for i, step in enumerate(steps):
        db.add(TestStep(test_case_id=tc.id, index=i, action=step["action"],
                        intent=step.get("intent", ""), value=step.get("value") or {},
                        target=step.get("target") or {}))
    finding.status = "promoted"
    finding.promoted_test_id = tc.id
    audit.record(db, action="exploration.finding_promoted", actor_id=actor,
                 project_id=project_id, resource_type="test_case", resource_id=tc.id,
                 detail={"finding_id": finding.id, "kind": finding.kind, "key": key})
    db.commit()
    return {"ok": True, "test_id": tc.id, "key": key, "kind": finding.kind}


def sessions_for_journey(db, project_id: str, target: str, limit: int = 5) -> list[dict]:
    """Recent exploration sessions against a target, with their findings, for the
    run report's Exploratory section."""
    from .onramp import normalize_url
    base = normalize_url(target)
    rows = db.execute(
        select(ExplorationSession).where(
            ExplorationSession.project_id == project_id,
            ExplorationSession.base_url.like(f"{base.rstrip('/')}%"),
        ).order_by(ExplorationSession.created_at.desc()).limit(limit)
    ).scalars()
    out = []
    for s in rows:
        out.append({"id": s.id, "charter": s.charter, "status": s.status,
                    "summary": s.summary, "screens_seen": s.screens_seen,
                    "steps_taken": s.steps_taken, "findings": findings_for(db, s.id)})
    return out


def active() -> list[str]:
    return [sid for sid, task in _TASKS.items() if not task.done()]


async def shutdown() -> None:
    for task in list(_TASKS.values()):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    _TASKS.clear()
