"""Journey lifecycle: create, resume, advance, and describe the Golden Path.

The orchestrator leans on ``active_journey`` to give "continue" / "what's next" a
referent, and on ``advance`` to move a target along the rail as each stage
completes. Everything here is deterministic and model-free: a journey is state,
not judgement.
"""

from __future__ import annotations

from sqlalchemy import select

from ..models import STAGE_ORDER, Journey, JourneyStage, JourneyStatus
from ..services.onramp import normalize_url

#: What the user does next at each stage, as a chat command + a human label. Used
#: to render the stage card's primary action and to answer "what's next".
_NEXT_ACTION: dict[str, tuple[str, str]] = {
    JourneyStage.TARGET: ("explore it", "Explore the site"),
    JourneyStage.ACCESS: ("continue", "Handle access"),
    JourneyStage.GUARDRAILS: ("continue", "Set guardrails"),
    JourneyStage.EXPLORE: ("plan it", "Review the test plan"),
    JourneyStage.PLAN: ("approve", "Approve & run the plan"),
    JourneyStage.BUILD: ("run it", "Run the tests"),
    JourneyStage.SMOKE: ("run it", "Run the full suite"),
    JourneyStage.RUN: ("triage", "Triage the results"),
    JourneyStage.TRIAGE: ("show the report", "Open the report"),
    JourneyStage.READINESS: ("show the report", "Open the report"),
    JourneyStage.REPORT: ("keep it green", "Schedule it / add to CI"),
    JourneyStage.KEEP_GREEN: ("status brief", "Check status"),
}


def active_journey(db, project_id: str) -> Journey | None:
    """The project's current in-flight journey, the most recently touched one."""
    return db.execute(
        select(Journey).where(
            Journey.project_id == project_id, Journey.status == JourneyStatus.ACTIVE
        ).order_by(Journey.updated_at.desc()).limit(1)
    ).scalars().first()


def _canon(target: str) -> str:
    """One identity for a target regardless of a trailing slash, so `x.com` and
    `x.com/` are the same journey (and `start` at the raw URL matches `for_target`
    at the discovered base)."""
    base = normalize_url(target)
    return base.rstrip("/") if base.rstrip("/") else base


def for_target(db, project_id: str, target: str, environment: str = "") -> Journey | None:
    base = _canon(target)
    return db.execute(
        select(Journey).where(
            Journey.project_id == project_id, Journey.target == base,
            Journey.status == JourneyStatus.ACTIVE,
        ).order_by(Journey.updated_at.desc()).limit(1)
    ).scalars().first()


def start(db, project_id: str, target: str, *, environment: str = "", created_by: str | None = None) -> Journey:
    """Begin (or resume) a journey for a target. One active journey per target."""
    base = _canon(target)
    existing = for_target(db, project_id, base, environment)
    if existing is not None:
        return existing
    env = environment or _env_from_host(base)
    journey = Journey(
        project_id=project_id, target=base, environment=env,
        stage=JourneyStage.TARGET, status=JourneyStatus.ACTIVE,
        title=f"{base} ({env})", created_by=created_by,
    )
    db.add(journey)
    db.flush()
    return journey


def advance(db, journey: Journey, stage: JourneyStage, *, ran: bool = True, **fields) -> Journey:
    """Move a journey to a stage and record any references produced there.

    ``ran=True`` marks the *previous* stage as genuinely completed; stages jumped
    over (never in ``completed``) render on the rail as skipped, not done, so
    "done" always means the stage actually happened.
    """
    stage_value = stage.value if isinstance(stage, JourneyStage) else stage
    if ran and journey.stage and journey.stage not in (journey.completed or []):
        journey.completed = [*(journey.completed or []), journey.stage]
    journey.stage = stage_value
    for key, value in fields.items():
        setattr(journey, key, value)
    db.add(journey)
    db.flush()
    return journey


def next_action(journey: Journey) -> dict:
    """The primary next step for the journey's current stage."""
    command, label = _NEXT_ACTION.get(journey.stage, ("what's next", "Continue"))
    return {"command": command, "label": label, "stage": journey.stage}


def describe(journey: Journey) -> dict:
    """Everything the UI needs to draw the rail and the current stage card.

    Each rail stage is one of three states: **current**, **done** (it actually
    ran, so it's in ``completed``), or **skipped** (it was passed over). A stage the
    journey hasn't reached yet is none of these (a future stage).
    """
    here = _index(journey.stage)
    completed = set(journey.completed or [])
    rail = []
    for i, s in enumerate(STAGE_ORDER):
        current = i == here
        done = (not current) and s.value in completed
        skipped = (not current) and (not done) and i < here
        rail.append({"stage": s.value, "current": current, "done": done, "skipped": skipped})
    return {
        "id": journey.id,
        "target": journey.target,
        "environment": journey.environment,
        "stage": journey.stage,
        "status": journey.status,
        "plan_version": journey.plan_version,
        "run_id": journey.run_id,
        "guardrails": journey.guardrails or {},
        "rail": rail,
        "next": next_action(journey),
    }


#: Hostnames that are safe to write against. Anything else is treated as
#: production and defaults to read-only.
_NONPROD = ("localhost", "127.0.0.1", "0.0.0.0", "staging", "stage", "test",
            "dev", "qa", "uat", "preview", "demo", "sandbox", "local")


def default_guardrails(target: str, environment: str) -> dict:
    """The safe defaults for a target: read-only on anything that looks like
    production, synthetic test data, a destructive-action avoid-list, and a polite
    rate limit. The user can loosen these per target; they are remembered on the
    journey."""
    host = target.split("://", 1)[-1].split("/", 1)[0].lower()
    is_prod = not any(marker in host for marker in _NONPROD)
    return {
        "read_only": is_prod,
        "data_policy": "synthetic",  # synthetic | seeded | none
        "avoid": ["payments", "emails", "delete", "destructive", "purchase", "checkout"],
        "rate_limit_rps": 2,
        "reason": ("looks like production, so writes are off by default"
                   if is_prod else f"non-production ({environment}), writes allowed"),
    }


def set_guardrails(db, journey: Journey, **changes) -> Journey:
    guardrails = dict(journey.guardrails or {})
    guardrails.update(changes)
    journey.guardrails = guardrails
    db.add(journey)
    db.flush()
    return journey


def _index(stage: str) -> int:
    for i, s in enumerate(STAGE_ORDER):
        if s.value == stage:
            return i
    return 0


def _env_from_host(url: str) -> str:
    host = url.split("://", 1)[-1].split("/", 1)[0].lower()
    if any(h in host for h in ("localhost", "127.0.0.1", "0.0.0.0")):
        return "local"
    for name in ("staging", "stage", "test", "dev", "qa", "uat", "preview", "demo"):
        if name in host:
            return name
    return "production"
