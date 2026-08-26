"""App Model discovery.

The App Model is the difference between healing a *selector* and healing an
*element*: heal once, and every test referencing it is repaired together. That
only works if the model actually contains the application, so it is populated
automatically from ordinary runs rather than requiring anyone to curate it.

Nothing here is guesswork about intent - it is observation. A screen was
visited; an element was interacted with; this locator resolved it. Those are
facts of the same kind as a run result, so they are recorded directly. The one
thing that *does* change test behaviour - binding a step to a model element - is
only done when it is provably a no-op (see ``maybe_link_step``).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai.embeddings import cosine, local_embed
from ..models import AppElement, AppScreen, AppTransition, TestStep
from ..models.base import utcnow

#: Path segments that identify a *record* rather than a *screen*. Collapsing
#: them is what makes /orders/1 and /orders/2 the same screen.
_VOLATILE_SEGMENT = re.compile(
    r"^(?:\d+"                                  # 42
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"  # uuid
    r"|[0-9a-f]{16,}"                           # long hex id
    r"|[A-Z]{2,6}-\d+"                          # ticket-style key
    r")$",
    re.IGNORECASE,
)

#: Above this, two elements on a screen are treated as the same element.
ELEMENT_MATCH = 0.82


@dataclass(slots=True)
class ScreenKey:
    route: str
    aria: str


def route_signature(url: str) -> str:
    """Collapse record identifiers so a screen is a screen, not a row."""
    without_scheme = re.sub(r"^[a-z]+://[^/]+", "", url or "", flags=re.IGNORECASE)
    path = without_scheme.split("?")[0].split("#")[0] or "/"
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "/"
    return "/" + "/".join(":id" if _VOLATILE_SEGMENT.match(p) else p for p in parts)


def aria_signature(roles: list[str]) -> str:
    """Structural fingerprint of a screen's accessibility skeleton.

    Sorted and counted rather than ordered: a reflow that moves the nav below
    the content is the same screen, while a screen that loses its form is not.
    """
    counts: dict[str, int] = {}
    for role in roles:
        counts[role] = counts.get(role, 0) + 1
    canonical = ";".join(f"{role}:{min(count, 9)}" for role, count in sorted(counts.items()))
    return hashlib.blake2b(canonical.encode(), digest_size=16).hexdigest()


def observe_screen(db: Session, *, project_id: str, observation: dict) -> AppScreen:
    """Upsert the screen a run just landed on."""
    route = route_signature(observation.get("url", ""))
    aria = aria_signature(observation.get("roles") or [])

    screen = db.execute(
        select(AppScreen).where(
            AppScreen.project_id == project_id,
            AppScreen.route_signature == route,
        )
    ).scalar_one_or_none()

    if screen is None:
        screen = AppScreen(
            project_id=project_id,
            name=_screen_name(route, observation.get("title", "")),
            route_signature=route,
            url_pattern=route,
            title_pattern=(observation.get("title") or "")[:400],
            aria_signature=aria,
            embedding=local_embed(f"{observation.get('title', '')} {route}"),
        )
        db.add(screen)
        db.flush()

    screen.visit_count += 1
    screen.last_seen_at = utcnow()
    # A changed skeleton on a known route is worth recording: it is the first
    # sign of a redesign, ahead of any locator actually breaking.
    if aria and screen.aria_signature and aria != screen.aria_signature:
        screen.aria_signature = aria
    db.flush()
    return screen


def observe_element(
    db: Session, *, project_id: str, screen: AppScreen, observation: dict
) -> AppElement:
    """Upsert an element that a step just interacted with."""
    role = (observation.get("role") or "").strip()
    name = (observation.get("accessibleName") or "").strip()
    locator = observation.get("locator") or {}

    existing = list(
        db.execute(
            select(AppElement).where(
                AppElement.screen_id == screen.id,
                AppElement.deprecated.is_(False),
            )
        ).scalars()
    )
    match = _best_match(existing, role=role, name=name, observation=observation)

    if match is None:
        match = AppElement(
            project_id=project_id,
            screen_id=screen.id,
            intent=observation.get("intent", "")[:1000],
            role=role,
            accessible_name=name[:400],
            tag=(observation.get("tag") or "")[:32],
            locators=[locator] if locator else [],
            fingerprint=observation.get("fingerprint") or {},
            bounding_box=observation.get("box") or {},
            embedding=local_embed(f"{role} {name} {observation.get('intent', '')}"),
        )
        db.add(match)
        db.flush()
        return match

    # Known element: promote the locator that actually worked to the front of
    # the ladder, keeping the others as fallbacks. Over time the ladder becomes
    # ordered by what really resolves this element in this application.
    if locator:
        ladder = [rung for rung in (match.locators or []) if rung != locator]
        match.locators = [locator, *ladder][:6]
    if observation.get("fingerprint"):
        match.fingerprint = observation["fingerprint"]
    if observation.get("box"):
        match.bounding_box = observation["box"]
    if name and not match.accessible_name:
        match.accessible_name = name[:400]
    if observation.get("intent") and not match.intent:
        match.intent = observation["intent"][:1000]
    match.last_verified_at = utcnow()
    # Resolving cleanly is evidence of stability, but recovery is slower than
    # damage: a heal costs 0.08, a clean resolution returns 0.02.
    match.stability_score = min(1.0, match.stability_score + 0.02)
    db.flush()
    return match


def observe_transition(
    db: Session, *, project_id: str, from_screen_id: str, to_screen_id: str,
    via_element_id: str | None, action: str = "click",
) -> AppTransition | None:
    """Record an edge in the screen graph, for journey coverage."""
    if from_screen_id == to_screen_id:
        return None
    edge = db.execute(
        select(AppTransition).where(
            AppTransition.project_id == project_id,
            AppTransition.from_screen_id == from_screen_id,
            AppTransition.to_screen_id == to_screen_id,
            AppTransition.via_element_id == via_element_id,
        )
    ).scalar_one_or_none()
    if edge is None:
        edge = AppTransition(
            project_id=project_id,
            from_screen_id=from_screen_id,
            to_screen_id=to_screen_id,
            via_element_id=via_element_id,
            action=action,
        )
        db.add(edge)
    else:
        edge.observations += 1
    db.flush()
    return edge


def maybe_link_step(db: Session, *, step_id: str | None, element: AppElement, locator: dict) -> bool:
    """Bind a step to the element it resolved - but only when that is a no-op.

    Binding gives the step the element's whole locator ladder, which is how one
    approved heal repairs every test at once. It is done automatically *only*
    when the locator the step used is already in the element's ladder, so the
    same element resolves by the same means either way; nothing the test does or
    asserts changes. Any other binding is a behavioural change and belongs
    behind the approval gate, not here.
    """
    if not step_id or not locator:
        return False
    step = db.get(TestStep, step_id)
    if step is None or step.element_id:
        return False
    if locator not in (element.locators or []):
        return False
    step.element_id = element.id
    db.flush()
    return True


# --------------------------------------------------------------------------- #
def _best_match(
    candidates: list[AppElement], *, role: str, name: str, observation: dict
) -> AppElement | None:
    test_id = ((observation.get("fingerprint") or {}).get("attrs") or {}).get("data-testid")

    for element in candidates:
        # A stable test id is an identity, not a hint.
        existing_id = ((element.fingerprint or {}).get("attrs") or {}).get("data-testid")
        if test_id and existing_id and test_id == existing_id:
            return element
        if role and name and element.role == role and element.accessible_name == name:
            return element

    if not (role or name):
        return None
    probe = local_embed(f"{role} {name}")
    scored = [
        (cosine(probe, e.embedding or []), e)
        for e in candidates
        if e.embedding and e.role == role
    ]
    if not scored:
        return None
    score, best = max(scored, key=lambda pair: pair[0])
    return best if score >= ELEMENT_MATCH else None


def _screen_name(route: str, title: str) -> str:
    if title:
        return title[:200]
    if route == "/":
        return "Home"
    tail = [p for p in route.split("/") if p and p != ":id"]
    return (tail[-1].replace("-", " ").replace("_", " ").title() if tail else route)[:200]


def summarize(db: Session, project_id: str) -> dict:
    screens = list(
        db.execute(select(AppScreen).where(AppScreen.project_id == project_id)).scalars()
    )
    elements = list(
        db.execute(select(AppElement).where(AppElement.project_id == project_id)).scalars()
    )
    edges = list(
        db.execute(select(AppTransition).where(AppTransition.project_id == project_id)).scalars()
    )
    return {
        "screens": len(screens),
        "elements": len(elements),
        "transitions": len(edges),
        "shared_elements": sum(1 for e in elements if e.heal_count > 0),
    }
