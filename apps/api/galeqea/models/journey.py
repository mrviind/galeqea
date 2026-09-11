"""The Golden Path journey: one chat-driven run from a URL to a signed-off report.

A journey is the spine the whole flow hangs on: it remembers where a target is in
the arc (target → access → guardrails → explore → plan → build → smoke → run →
triage → exploratory → readiness → report → keep-green), so a reload resumes at
the same stage and "what's next" / "continue" / "approve" / "run it" always have a
concrete referent. One journey per target+environment; several can be open at once.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import IdMixin, JSONish, TimestampMixin


class JourneyStage(StrEnum):
    TARGET = "target"
    ACCESS = "access"
    GUARDRAILS = "guardrails"
    EXPLORE = "explore"
    PLAN = "plan"
    BUILD = "build"
    SMOKE = "smoke"
    RUN = "run"
    TRIAGE = "triage"
    EXPLORATORY = "exploratory"
    READINESS = "readiness"
    REPORT = "report"
    KEEP_GREEN = "keep_green"


#: The rail, in order. The UI draws it and the orchestrator uses it to answer
#: "what's next"; ``EXPLORATORY`` is optional and skipped by default advancement.
STAGE_ORDER: tuple[JourneyStage, ...] = (
    JourneyStage.TARGET, JourneyStage.ACCESS, JourneyStage.GUARDRAILS,
    JourneyStage.EXPLORE, JourneyStage.PLAN, JourneyStage.BUILD, JourneyStage.SMOKE,
    JourneyStage.RUN, JourneyStage.TRIAGE, JourneyStage.READINESS,
    JourneyStage.REPORT, JourneyStage.KEEP_GREEN,
)


class JourneyStatus(StrEnum):
    ACTIVE = "active"
    DONE = "done"
    ABANDONED = "abandoned"


class Journey(Base, IdMixin, TimestampMixin):
    __tablename__ = "journeys"

    project_id: Mapped[str] = mapped_column(String(40), index=True)
    target: Mapped[str] = mapped_column(String(2000), default="")
    environment: Mapped[str] = mapped_column(String(64), default="")
    stage: Mapped[str] = mapped_column(String(24), default=JourneyStage.TARGET, index=True)
    #: Stages that *actually ran* (as opposed to being auto-skipped), so the rail
    #: can distinguish a done stage from one that was passed over. Ordered.
    completed: Mapped[list] = mapped_column(JSONish, default=list)
    status: Mapped[str] = mapped_column(String(16), default=JourneyStatus.ACTIVE, index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    plan_version: Mapped[int] = mapped_column(Integer, default=0)

    #: References into the rest of the system, filled in as stages complete.
    discovery: Mapped[dict] = mapped_column(JSONish, default=dict)
    plan: Mapped[dict] = mapped_column(JSONish, default=dict)
    test_ids: Mapped[list] = mapped_column(JSONish, default=list)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    report: Mapped[dict] = mapped_column(JSONish, default=dict)
    guardrails: Mapped[dict] = mapped_column(JSONish, default=dict)

    created_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
