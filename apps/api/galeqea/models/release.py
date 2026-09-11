"""The test-manager core: the objects a QA lead runs a release on.

The chain: Requirement → TestCase (versioned) → TestPlan → Cycle × Configuration →
Result (a RunTest) → DefectLink, rolled up to a Milestone/Release. Nothing here sits
outside that chain.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import IdMixin, JSONish, TimestampMixin, UTCDateTime


class MilestoneStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    RELEASED = "released"
    ARCHIVED = "archived"


class Milestone(Base, IdMixin, TimestampMixin):
    """A release: the thing exit criteria are evaluated against and signed off."""

    __tablename__ = "milestones"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(64), index=True)     # e.g. "1.4"
    target_date: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=MilestoneStatus.PLANNED, index=True)
    #: Ordered rules, e.g. [{"metric":"pass_rate","op":">=","value":0.95}, …]
    exit_criteria: Mapped[list] = mapped_column(JSONish, default=list)
    #: Immutable once set: {"by","at","decision":"go|no_go","note"}. Empty = unsigned.
    signoff: Mapped[dict] = mapped_column(JSONish, default=dict)


class Environment(Base, IdMixin, TimestampMixin):
    """A place tests run against; a run snapshots the environment it used."""

    __tablename__ = "environments"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    base_url: Mapped[str] = mapped_column(String(500), default="")
    build_label: Mapped[str] = mapped_column(String(120), default="")
    #: Vault reference for login credentials, never the secret itself.
    credentials_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tags: Mapped[list] = mapped_column(JSONish, default=list)


class TestPlan(Base, IdMixin, TimestampMixin):
    """What to run for a milestone, across which configurations."""

    __tablename__ = "test_plans"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    milestone_id: Mapped[str | None] = mapped_column(
        ForeignKey("milestones.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    #: Either static ids {"ids":[…]} or a saved query {"query":{"tags":[…],"type":…,"risk":…}}.
    selection: Mapped[dict] = mapped_column(JSONish, default=dict)
    #: [{"browser":"chromium","viewport":"desktop","env":"<environment id or name>"}, …]
    configurations: Mapped[list] = mapped_column(JSONish, default=list)


class CycleStatus(StrEnum):
    ACTIVE = "active"
    COMPLETE = "complete"


class Cycle(Base, IdMixin, TimestampMixin):
    """A plan × one configuration. Pins each case's VERSION at creation, and gathers
    the manual and automated results for that configuration in one place."""

    __tablename__ = "cycles"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("test_plans.id", ondelete="CASCADE"), index=True)
    milestone_id: Mapped[str | None] = mapped_column(
        ForeignKey("milestones.id", ondelete="SET NULL"), nullable=True, index=True)
    environment_id: Mapped[str | None] = mapped_column(
        ForeignKey("environments.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(240), default="")
    configuration: Mapped[dict] = mapped_column(JSONish, default=dict)
    status: Mapped[str] = mapped_column(String(20), default=CycleStatus.ACTIVE, index=True)
    #: [{"test_case_id","key","version"}], the versions pinned when the cycle opened.
    pinned_cases: Mapped[list] = mapped_column(JSONish, default=list)
    #: {"planned","executed","passed","failed","blocked","skipped"}.
    counters: Mapped[dict] = mapped_column(JSONish, default=dict)


class DefectLink(Base, IdMixin, TimestampMixin):
    """A result linked to a tracked defect."""

    __tablename__ = "defect_links"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    result_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)  # RunTest id
    test_case_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    tracker: Mapped[str] = mapped_column(String(16), default="jira")  # jira|github|gitlab|azure
    key: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(String(600), default="")
    status_cached: Mapped[str] = mapped_column(String(60), default="")
    last_synced: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class DefectMap(Base, IdMixin, TimestampMixin):
    """Failure fingerprint → the one tracker issue that stands for it.

    This is what makes "file a bug" idempotent: the same underlying failure, seen
    again in a later run, comments on the existing issue and bumps ``reopened_count``
    instead of opening a duplicate. One row per (project, provider, fingerprint)."""

    __tablename__ = "defect_maps"
    __table_args__ = (
        UniqueConstraint("project_id", "provider", "failure_fingerprint",
                         name="uq_defect_map_fingerprint"),
    )

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    failure_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(16), default="jira")  # jira|github|gitlab
    issue_key: Mapped[str] = mapped_column(String(120), default="")
    issue_id: Mapped[str] = mapped_column(String(64), default="")
    url: Mapped[str] = mapped_column(String(600), default="")
    status_cached: Mapped[str] = mapped_column(String(60), default="")
    #: True once the cached status is a terminal/done state. Cheap for readiness.
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    reopened_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_synced: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class SharedStep(Base, IdMixin, TimestampMixin):
    """A reusable step block a TestCase can reference by id."""

    __tablename__ = "shared_steps"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    #: The step list, same shape as a TestCase's inline steps.
    steps: Mapped[list] = mapped_column(JSONish, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
