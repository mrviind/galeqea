"""Cron scheduling for recurring runs."""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from ..db import session_scope
from ..models import Schedule
from ..models.base import utcnow

log = logging.getLogger("galeqea.scheduler")
_scheduler: AsyncIOScheduler | None = None


def scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


async def _fire(schedule_id: str) -> None:
    from .runs import start_run

    with session_scope() as db:
        schedule = db.get(Schedule, schedule_id)
        if schedule is None or not schedule.enabled:
            return
        project_id = schedule.project_id
        selection = dict(schedule.selection or {})
        if schedule.suite_id:
            from ..models import SuiteMember

            members = db.execute(
                select(SuiteMember).where(SuiteMember.suite_id == schedule.suite_id)
            ).scalars()
            selection["test_ids"] = [m.test_case_id for m in members]
        environment = schedule.environment
        name = schedule.name
        schedule.last_fired_at = utcnow()

    with session_scope() as db:
        await start_run(
            db, project_id=project_id, selection=selection, environment=environment,
            trigger="schedule", title=f"Scheduled: {name}", command=f"cron:{name}",
        )


def register(schedule: Schedule) -> None:
    trigger = CronTrigger.from_crontab(schedule.cron, timezone=schedule.timezone or "UTC")
    scheduler().add_job(
        _fire, trigger=trigger, args=[schedule.id], id=f"schedule:{schedule.id}",
        replace_existing=True, misfire_grace_time=600, coalesce=True,
    )


def unregister(schedule_id: str) -> None:
    job = scheduler().get_job(f"schedule:{schedule_id}")
    if job:
        job.remove()


def load_all() -> int:
    """Re-register every enabled schedule at boot."""
    count = 0
    with session_scope() as db:
        for schedule in db.execute(
            select(Schedule).where(Schedule.enabled.is_(True))
        ).scalars():
            try:
                register(schedule)
                count += 1
            except ValueError as exc:
                # A malformed cron must not stop the server from starting; it is
                # reported and the schedule stays visible as broken.
                log.warning("schedule %s has an invalid cron %r: %s",
                            schedule.id, schedule.cron, exc)
                schedule.enabled = False
    return count


def start() -> None:
    sched = scheduler()
    if not sched.running:
        sched.start()


def shutdown() -> None:
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)


def describe_cron(expression: str) -> str:
    """Plain-English description, so a schedule is reviewable before approval."""
    try:
        minute, hour, dom, month, dow = expression.split()
    except ValueError:
        return expression
    days = {"0": "Sunday", "1": "Monday", "2": "Tuesday", "3": "Wednesday",
            "4": "Thursday", "5": "Friday", "6": "Saturday", "7": "Sunday"}
    if hour == "*" and minute.isdigit():
        return f"every hour at :{int(minute):02d}"
    if dom == "*" and month == "*" and dow == "*":
        return f"every day at {int(hour):02d}:{int(minute):02d} UTC"
    if dow in days:
        return f"every {days[dow]} at {int(hour):02d}:{int(minute):02d} UTC"
    if dom.isdigit():
        return f"on day {dom} of each month at {int(hour):02d}:{int(minute):02d} UTC"
    return expression
