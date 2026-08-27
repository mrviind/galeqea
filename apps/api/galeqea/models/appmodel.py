"""The App Model - a persistent digital twin of the application under test.

Most tools heal a *selector*. That is patching a symptom: the same button gets
re-found independently by forty tests, forty times. QE Agent instead maintains a
durable graph of screens and elements. A test step points at ``element_id``; when
the UI shifts, the element is re-resolved **once** and every test that references
it is fixed at the same moment - and the change is shown to a human as a diff
before it is trusted.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from .base import IdMixin, JSONish, TimestampMixin, UTCDateTime


class AppScreen(Base, IdMixin, TimestampMixin):
    """A logical screen/state, not a URL. ``/orders/1`` and ``/orders/2`` collapse."""

    __tablename__ = "app_screens"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    url_pattern: Mapped[str] = mapped_column(String(600), default="")
    route_signature: Mapped[str] = mapped_column(String(200), default="", index=True)
    title_pattern: Mapped[str] = mapped_column(String(400), default="")
    # Structural hash of the accessibility skeleton; how a screen is recognised
    # again after a redesign that keeps the same semantics.
    aria_signature: Mapped[str] = mapped_column(String(64), default="", index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    visit_count: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    embedding: Mapped[list | None] = mapped_column(JSONish, nullable=True)

    elements: Mapped[list[AppElement]] = relationship(
        back_populates="screen", cascade="all, delete-orphan"
    )


class AppTransition(Base, IdMixin, TimestampMixin):
    """Directed edge in the screen graph; feeds journey coverage and path search."""

    __tablename__ = "app_transitions"

    project_id: Mapped[str] = mapped_column(String(40), index=True)
    from_screen_id: Mapped[str] = mapped_column(ForeignKey("app_screens.id", ondelete="CASCADE"))
    to_screen_id: Mapped[str] = mapped_column(ForeignKey("app_screens.id", ondelete="CASCADE"))
    via_element_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    action: Mapped[str] = mapped_column(String(32), default="click")
    observations: Mapped[int] = mapped_column(Integer, default=1)
    success_rate: Mapped[float] = mapped_column(Float, default=1.0)


class AppElement(Base, IdMixin, TimestampMixin):
    """A stable identity for an interactive element across UI churn."""

    __tablename__ = "app_elements"

    project_id: Mapped[str] = mapped_column(String(40), index=True)
    screen_id: Mapped[str] = mapped_column(ForeignKey("app_screens.id", ondelete="CASCADE"), index=True)

    # What the element *means* to a user. Durable across framework rewrites.
    intent: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[str] = mapped_column(String(48), default="")        # ARIA role
    accessible_name: Mapped[str] = mapped_column(String(400), default="")
    tag: Mapped[str] = mapped_column(String(32), default="")

    # Ordered locator ladder. Index 0 is tried first and runs at full native
    # speed; the rest exist so healing has somewhere to fall back to.
    locators: Mapped[list] = mapped_column(JSONish, default=list)
    # Weighted attribute fingerprint used for scoring candidates during a heal.
    fingerprint: Mapped[dict] = mapped_column(JSONish, default=dict)
    bounding_box: Mapped[dict] = mapped_column(JSONish, default=dict)
    embedding: Mapped[list | None] = mapped_column(JSONish, nullable=True)

    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    stability_score: Mapped[float] = mapped_column(Float, default=1.0)
    heal_count: Mapped[int] = mapped_column(Integer, default=0)
    last_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    deprecated: Mapped[bool] = mapped_column(Boolean, default=False)

    screen: Mapped[AppScreen] = relationship(back_populates="elements")


class HealEvent(Base, IdMixin, TimestampMixin):
    """Every heal is a *proposal* with evidence, never a silent mutation."""

    __tablename__ = "heal_events"

    project_id: Mapped[str] = mapped_column(String(40), index=True)
    element_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    test_case_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    step_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    run_test_id: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # fingerprint | dom_scoring | semantic_llm | visual | vision_model
    strategy: Mapped[str] = mapped_column(String(32), default="fingerprint")
    old_locator: Mapped[str] = mapped_column(Text, default="")
    new_locator: Mapped[str] = mapped_column(Text, default="")
    candidates: Mapped[list] = mapped_column(JSONish, default=list)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    evidence: Mapped[dict] = mapped_column(JSONish, default=dict)

    # proposed -> approved | rejected. Applied *only* after approval; a heal used
    # to rescue a single run is marked ``used_transiently`` and still needs a
    # human before it is written back to the App Model.
    status: Mapped[str] = mapped_column(String(20), default="proposed", index=True)
    used_transiently: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class VisualBaseline(Base, IdMixin, TimestampMixin):
    """The approved reference for a named screen.

    Versioned rather than overwritten: accepting a change should never destroy
    the evidence of what it replaced, because "when did this start looking like
    that?" is a question people actually ask.
    """

    __tablename__ = "visual_baselines"

    project_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(300), index=True)
    screen_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    browser: Mapped[str] = mapped_column(String(32), default="chromium")
    viewport: Mapped[str] = mapped_column(String(32), default="1280x720")
    image_path: Mapped[str] = mapped_column(String(1000), default="")
    perceptual_hash: Mapped[str] = mapped_column(String(64), default="")
    aria_snapshot: Mapped[str] = mapped_column(Text, default="")
    approved_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class VisualComparison(Base, IdMixin, TimestampMixin):
    """A candidate screenshot measured against its baseline, awaiting a verdict.

    Kept separate from the baseline because the two answer different questions:
    a baseline is what the screen *should* look like, a comparison is a claim
    that it no longer does. Storing only the baseline - which the first version
    of this did - means the diff is computed, reported once, and then lost, so
    there is nothing to review.
    """

    __tablename__ = "visual_comparisons"

    project_id: Mapped[str] = mapped_column(String(40), index=True)
    baseline_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(300), index=True)

    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    run_test_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    url: Mapped[str] = mapped_column(String(600), default="")
    browser: Mapped[str] = mapped_column(String(32), default="chromium")
    viewport: Mapped[str] = mapped_column(String(32), default="")

    candidate_path: Mapped[str] = mapped_column(String(1000), default="")
    baseline_path: Mapped[str] = mapped_column(String(1000), default="")
    diff_path: Mapped[str] = mapped_column(String(1000), default="")
    candidate_aria: Mapped[str] = mapped_column(Text, default="")

    # none | cosmetic | notable | breaking
    severity: Mapped[str] = mapped_column(String(16), default="none", index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    #: What the accessibility tree gained and lost - the half of the comparison
    #: that survives a re-render and means something in words.
    structural: Mapped[dict] = mapped_column(JSONish, default=dict)
    #: Changed regions as boxes, not pixel confetti.
    regions: Mapped[list] = mapped_column(JSONish, default=list)
    changed_pct: Mapped[float] = mapped_column(Float, default=0.0)
    perceptual_distance: Mapped[int] = mapped_column(Integer, default=0)
    dimensions_changed: Mapped[bool] = mapped_column(Boolean, default=False)

    # new | accepted | rejected | auto_passed
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    review_comment: Mapped[str] = mapped_column(Text, default="")
    judged_by: Mapped[str] = mapped_column(String(24), default="deterministic")
