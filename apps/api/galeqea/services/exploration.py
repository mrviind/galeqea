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


async def start(
    db: Session,
    *,
    project_id: str,
    charter: str,
    environment: str = "",
    max_steps: int = 30,
    allow_transactional: bool = False,
    started_by: str | None = None,
) -> ExplorationSession:
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"unknown project {project_id}")

    env = environment or project.default_environment or "default"
    base_url = (project.environments or {}).get(env, "")
    if not base_url:
        raise ValueError(
            f"environment {env!r} has no URL configured — exploration needs somewhere to go"
        )

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
    _TASKS[session.id] = asyncio.create_task(_drive(session.id, project_id))
    return session


async def _drive(session_id: str, project_id: str) -> None:
    """Spawn the runner in exploration mode and mediate its questions."""
    from ..engine.supervisor import RunSupervisor

    artifacts = Path(settings.artifacts_dir) / f"explore-{session_id}"
    artifacts.mkdir(parents=True, exist_ok=True)

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
            "explore": {
                "id": session_id,
                "charter": session.charter,
                "baseUrl": session.base_url,
                "maxSteps": session.max_steps,
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
    label = "—"
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
                f"no new findings — but {recurring} already-known issue(s) are still present."
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


def active() -> list[str]:
    return [sid for sid, task in _TASKS.items() if not task.done()]


async def shutdown() -> None:
    for task in list(_TASKS.values()):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    _TASKS.clear()
