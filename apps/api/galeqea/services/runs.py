"""Run lifecycle: create, dispatch, observe.

Runs are dispatched onto a bounded worker pool rather than a thread per run, so
a "run everything on four browsers" request cannot exhaust the machine, and a
queued run is visible as queued instead of silently absent.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..core import audit
from ..core.events import Ev, Event, bus
from ..engine.plan import select_tests
from ..models import Project, Run, RunStatus
from ..models.base import utcnow

log = logging.getLogger("galeqea.runs")

_semaphore: asyncio.Semaphore | None = None


def _gate() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.max_parallel_runs)
    return _semaphore


async def start_run(
    db: Session,
    *,
    project_id: str,
    selection: dict,
    environment: str = "",
    browsers: list[str] | None = None,
    trigger: str = "manual",
    triggered_by: str | None = None,
    command: str = "",
    title: str = "",
    suite_id: str | None = None,
    parent_run_id: str | None = None,
    git_sha: str = "",
    git_branch: str = "",
    auth: dict | None = None,
    headless: bool | None = None,
    base_url: str = "",
    rate_limit_rps: float | None = None,
    rerun_kind: str = "",
    attempt: int = 0,
) -> Run:
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"unknown project {project_id}")

    env = environment or project.default_environment or "default"
    # A target that isn't a named environment (an ad-hoc URL from a journey) still
    # needs a base_url on the run, so the report never shows a blank target.
    base_url = base_url or (project.environments or {}).get(env, "")

    number = (
        db.execute(
            select(func.coalesce(func.max(Run.number), 0)).where(Run.project_id == project_id)
        ).scalar_one()
        + 1
    )
    matched = select_tests(db, project_id, selection)

    run = Run(
        project_id=project_id,
        number=number,
        title=title or f"Run #{number}",
        trigger=trigger,
        triggered_by=triggered_by,
        command=command,
        environment=env,
        base_url=base_url,
        browsers=browsers or (project.settings or {}).get("browsers") or ["chromium"],
        suite_id=suite_id,
        parent_run_id=parent_run_id,
        selection=selection,
        status=RunStatus.QUEUED,
        git_sha=git_sha,
        git_branch=git_branch,
        # The sealed session/credentials to replay for this run (see engine.plan
        # ._inject_auth). Sealed here; unsealed only into the transient plan.
        # A per-run headless override lets a log-in-for-me run open headed.
        ci_metadata={**({"auth": auth} if auth else {}),
                     **({"headless": headless} if headless is not None else {}),
                     **({"rate_limit_rps": rate_limit_rps} if rate_limit_rps else {}),
                     **({"rerun_kind": rerun_kind} if rerun_kind else {}),
                     **({"attempt": attempt} if attempt else {})},
        totals={"total": len(matched), "passed": 0, "failed": 0, "skipped": 0},
    )
    db.add(run)
    db.flush()

    audit.record(
        db,
        action="run.started",
        actor_id=triggered_by,
        actor_kind="agent" if trigger == "chat" else "human",
        project_id=project_id,
        resource_type="run",
        resource_id=run.id,
        detail={
            "trigger": trigger, "environment": env, "command": command,
            "test_count": len(matched), "browsers": run.browsers,
        },
    )
    db.commit()

    await bus.publish(Event(
        type=Ev.RUN_QUEUED, project_id=project_id, run_id=run.id,
        payload={"run_id": run.id, "number": run.number, "title": run.title,
                 "test_count": len(matched), "environment": env},
    ))

    if not matched:
        run.status = RunStatus.SKIPPED
        run.error = (
            "No approved automated tests matched that selection. "
            "Check the filter, or approve the proposed tests first."
        )
        run.finished_at = utcnow()
        db.commit()
        await bus.publish(Event(
            type=Ev.RUN_FINISHED, project_id=project_id, run_id=run.id,
            payload={"run_id": run.id, "status": run.status, "error": run.error},
        ))
        return run

    from ..jobs import get_queue
    job_id = await get_queue().enqueue("run_execute", run_id=run.id, project_id=project_id)
    run.ci_metadata = {**(run.ci_metadata or {}), "job_id": job_id}
    db.commit()
    return run


def _provider_for(project_id: str):
    """A run uses its own project's key, not whatever the process booted with."""
    from ..ai.providers.registry import for_project
    from ..db import session_scope

    with session_scope() as db:
        return for_project(db, project_id)


async def _dispatch(run_id: str, project_id: str) -> None:
    from ..engine.supervisor import RunSupervisor

    async with _gate():
        provider = _provider_for(project_id) if settings.ai_enabled else None
        supervisor = RunSupervisor(provider=provider)
        try:
            await supervisor.execute(run_id)
        except Exception as exc:  # noqa: BLE001
            # The stored message stays short for the UI; the full traceback goes
            # to the log, because "TypeError: ..." with no frame is unfixable.
            log.exception("run %s failed", run_id)
            from ..db import session_scope

            with session_scope() as db:
                run = db.get(Run, run_id)
                if run:
                    run.status = RunStatus.ERROR
                    run.error = f"{type(exc).__name__}: {exc}"
                    run.finished_at = utcnow()
            await bus.publish(Event(
                type=Ev.RUN_FINISHED, project_id=project_id, run_id=run_id,
                payload={"run_id": run_id, "status": "error", "error": str(exc)},
            ))
        finally:
            await _post_run(run_id, project_id)


async def _post_run(run_id: str, project_id: str) -> None:
    """Anomaly detection and selection learning, after results are durable."""
    from ..db import session_scope
    from ..intelligence.anomaly import detect_for_run
    from ..intelligence.selection import learn_from_run

    cycle_finished = None
    with session_scope() as db:
        run = db.get(Run, run_id)
        if run is None:
            return
        anomalies = detect_for_run(db, run)
        changed = (run.ci_metadata or {}).get("changed_paths") or []
        if changed:
            learn_from_run(db, project_id, run_id, changed)
        # Roll the run up into its cycle, if it belongs to one.
        if run.cycle_id:
            from ..models import Cycle
            from ..services import release
            cycle = db.get(Cycle, run.cycle_id)
            if cycle is not None:
                finished = release.roll_up_cycle(db, cycle)
                if finished:
                    cycle_finished = {"cycle_id": cycle.id, "milestone_id": cycle.milestone_id,
                                      "name": cycle.name, "counters": cycle.counters}

    for anomaly in anomalies:
        await bus.publish(Event(
            type=Ev.ANOMALY, project_id=project_id, run_id=run_id,
            payload=anomaly.as_dict(),
        ))

    if cycle_finished:
        await bus.publish(Event(
            type=Ev.CYCLE_FINISHED, project_id=project_id, run_id=run_id,
            payload=cycle_finished,
        ))


def run_task(run_id: str):
    """The awaitable executing a run in-process, or None when it runs in a worker
    (distributed queue). Callers that need to block should use ``wait_for_run``."""
    from ..jobs import get_queue
    queue = get_queue()
    return queue.run_task(run_id) if hasattr(queue, "run_task") else None


async def wait_for_run(run_id: str, *, timeout: float = 120.0) -> None:
    """Block until a run finishes (or the timeout). Awaits the in-process task when
    there is one; otherwise (distributed worker, or already finished) polls the row."""
    from ..jobs import get_queue
    queue = get_queue()
    task = queue.run_task(run_id) if hasattr(queue, "run_task") else None
    if task is not None:
        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        return
    # In-process with no live task means the run already finished, so return at once
    # (never poll, or a stubbed run that never reaches a terminal status would hang).
    if queue.kind == "inprocess":
        return
    from ..db import session_scope
    _TERMINAL = {RunStatus.PASSED, RunStatus.FAILED, RunStatus.FLAKY, RunStatus.ERROR,
                 RunStatus.NEEDS_REVIEW, RunStatus.SKIPPED}
    for _ in range(max(1, int(timeout / 0.5))):
        with session_scope() as db:
            run = db.get(Run, run_id)
            if run is None or run.status in _TERMINAL:
                return
        await asyncio.sleep(0.5)


def active_runs() -> list[str]:
    """Run ids executing right now: from the in-process queue, or (on a distributed
    Postgres queue where runs execute in a worker) from the RUNNING rows in the DB."""
    from ..jobs import get_queue
    queue = get_queue()
    if queue.kind == "inprocess":
        return queue.running_run_ids()
    from ..db import session_scope
    with session_scope() as db:
        return [r.id for r in db.execute(
            select(Run.id).where(Run.status == RunStatus.RUNNING)).scalars()]


async def shutdown() -> None:
    from ..jobs import get_queue
    await get_queue().stop()
