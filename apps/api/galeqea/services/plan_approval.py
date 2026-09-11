"""First-class golden-path plan approval.

The plan a person approves before a site is tested used to live only as a chat
reply, with no audited gate and no HTTP/CLI/MCP surface. Here it is a real
``ApprovalRequest`` (action ``plan.approve``): *proposing* a plan files it (the
deterministic on-ramp, no model needed), and *approving* it (from chat, HTTP, CLI
or MCP, all through the one ``approvals.decide`` service) builds the plan's
golden-path tests as APPROVED and makes them runnable by ``galeqea test``.

The proposal is a machine action (the crawler proposed it); a human decides it. So
the request is filed as the agent, and the deciding human is never the proposer, so
the self-approval invariant holds without special-casing.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import approvals
from ..core.approvals import RiskTier, applier
from ..models import (
    ApprovalRequest,
    ApprovalStatus,
    Journey,
    Project,
    TestCase,
    TestStatus,
)


def propose(db: Session, project: Project, target: str,
            *, page_limit: int | None = None,
            pages: list[str] | None = None) -> tuple[Journey, ApprovalRequest]:
    """Deterministic on-ramp: crawl the site, build the typed plan, file the
    plan-approval request. No model required: the same service path the chat uses.
    ``page_limit`` bounds the crawl (defaults to the demo page budget). ``pages``
    overrides the discovered set with an explicit list, the owner's authority on
    which pages to test, so a stale/cached crawl never dictates scope."""
    from ..config import settings
    from ..models import JourneyStage
    from . import journeys
    from . import test_plan as tp
    from . import website_test as wt

    discovery = wt.discover_pages(target, limit=page_limit or settings.demo_page_budget)
    if not discovery.get("ok"):
        raise ValueError(discovery.get("error") or f"could not reach {target}")
    if pages:
        # Normalise to absolute URLs against the target, then take the owner's list
        # verbatim (deduped, order preserved).
        base = discovery.get("base", target).rstrip("/")
        norm = []
        for pg in pages:
            pg = pg.strip()
            if not pg:
                continue
            norm.append(pg if pg.startswith("http") else f"{base}/{pg.lstrip('/')}")
        discovery["pages"] = list(dict.fromkeys(norm))
        discovery["truncated"] = False
        discovery["method"] = discovery.get("method", "browser") + "+owner-selected"
    typed = tp.build_typed_plan(discovery, version=1)
    plan = {**wt.build_plan(discovery), "typed": typed}
    journey = journeys.start(db, project.id, plan["target"])
    journeys.advance(db, journey, JourneyStage.PLAN, discovery=discovery, plan=plan,
                     plan_version=typed["version"])
    req = request_plan_approval(db, project, journey)
    db.commit()
    return journey, req


def request_plan_approval(db: Session, project: Project, journey: Journey) -> ApprovalRequest:
    """Idempotently file the pending plan-approval request for a journey's plan."""
    existing = pending_for_journey(db, journey)
    if existing is not None:
        return existing
    typed = (journey.plan or {}).get("typed") or {}
    totals = typed.get("totals", {})
    summary = (f"{totals.get('types_enabled', 0)} test type(s), "
               f"{totals.get('test_count', 0)} test(s) for {journey.target}")
    return approvals.request(
        db,
        action="plan.approve",
        project_id=project.id,
        title=f"Golden Path plan: {journey.target}",
        summary=summary,
        resource_type="journey",
        resource_id=journey.id,
        payload={
            "journey_id": journey.id,
            "plan_version": journey.plan_version or 1,
            "target": journey.target,
            "types": [{"key": r["key"], "count": r.get("count", 0)}
                      for r in typed.get("types", []) if r.get("enabled")],
        },
        risk=RiskTier.LOW,
        requested_by=None,            # the crawler proposed it; a human decides it
        requested_by_kind="agent",
    )


def pending_for_journey(db: Session, journey: Journey) -> ApprovalRequest | None:
    return db.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.action == "plan.approve",
            ApprovalRequest.status == ApprovalStatus.PENDING,
            ApprovalRequest.resource_id == journey.id,
        ).order_by(ApprovalRequest.created_at.desc())
    ).scalars().first()


@applier("plan.approve")
def _apply_plan_approve(db: Session, request: ApprovalRequest) -> dict:
    """Approving a plan builds its golden-path tests as APPROVED and lists them on
    the journey, so ``galeqea test`` (and a chat re-run) can execute them."""
    from ..models import JourneyStage
    from . import journeys
    from .build import build_tests_from_plan

    journey = db.get(Journey, (request.payload or {}).get("journey_id"))
    if journey is None:
        raise ValueError("the plan's journey no longer exists")
    project = db.get(Project, journey.project_id)
    result = build_tests_from_plan(db, project, journey)
    keys = result["run_keys"]
    for tc in db.execute(
        select(TestCase).where(TestCase.project_id == project.id, TestCase.key.in_(keys))
    ).scalars():
        tc.status = TestStatus.APPROVED
    # Every approval entry point (chat, HTTP, CLI, MCP) shares this applier, so the
    # stage machine has to advance here too. Otherwise an HTTP/CLI/MCP approval
    # leaves the journey on "plan" and chat's stage-gated "run it" refuses to fire.
    journeys.advance(db, journey, JourneyStage.BUILD, test_ids=keys)
    return {"target": journey.target, "keys": keys,
            "suites": result["suites"], "smoke_keys": result["smoke_keys"]}
