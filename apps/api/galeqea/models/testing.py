"""Requirements, test cases, suites, runs.

Design note - **tests are data, not code**. A test case owns an ordered list of
typed steps; each step carries a *semantic intent* plus an optional reference
into the App Model (see ``appmodel.py``) rather than a bare CSS selector. That
single choice is what makes healing durable, replay deterministic and export to
Playwright/pytest/Robot a rendering concern instead of a rewrite.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from .base import IdMixin, JSONish, TimestampMixin, UTCDateTime


# --------------------------------------------------------------------------- #
# Requirements
# --------------------------------------------------------------------------- #
class DocKind(StrEnum):
    REQUIREMENT = "requirement"
    SUPPORTING = "supporting"   # client context, design notes, API specs
    GLOSSARY = "glossary"


class RequirementDoc(Base, IdMixin, TimestampMixin):
    __tablename__ = "requirement_docs"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(400))
    kind: Mapped[str] = mapped_column(String(24), default=DocKind.REQUIREMENT)
    source_filename: Mapped[str] = mapped_column(String(400), default="")
    mime_type: Mapped[str] = mapped_column(String(120), default="text/plain")
    content: Mapped[str] = mapped_column(Text, default="")
    content_sha256: Mapped[str] = mapped_column(String(64), default="", index=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    meta: Mapped[dict] = mapped_column(JSONish, default=dict)

    items: Mapped[list[RequirementItem]] = relationship(
        back_populates="doc", cascade="all, delete-orphan"
    )


class RequirementItem(Base, IdMixin, TimestampMixin):
    """An atomic, addressable requirement. The anchor of the traceability graph."""

    __tablename__ = "requirement_items"

    doc_id: Mapped[str] = mapped_column(
        ForeignKey("requirement_docs.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    ref: Mapped[str] = mapped_column(String(64), index=True)      # e.g. REQ-014
    title: Mapped[str] = mapped_column(String(400))
    text: Mapped[str] = mapped_column(Text, default="")
    section: Mapped[str] = mapped_column(String(200), default="")
    kind: Mapped[str] = mapped_column(String(32), default="functional")
    risk: Mapped[str] = mapped_column(String(16), default="medium")  # low|medium|high|critical
    acceptance_criteria: Mapped[list] = mapped_column(JSONish, default=list)
    # Ambiguities the Requirement Analyst could not resolve - surfaced to humans
    # instead of being silently guessed at.
    open_questions: Mapped[list] = mapped_column(JSONish, default=list)
    embedding: Mapped[list | None] = mapped_column(JSONish, nullable=True)

    doc: Mapped[RequirementDoc] = relationship(back_populates="items")


# --------------------------------------------------------------------------- #
# Test cases
# --------------------------------------------------------------------------- #
class TestCategory(StrEnum):
    MANUAL = "manual"
    EXPLORATORY = "exploratory"   # a charter/block, not a scripted path
    AUTOMATED = "automated"


class TestStatus(StrEnum):
    PROPOSED = "proposed"       # AI authored, awaiting the human gate
    APPROVED = "approved"
    REJECTED = "rejected"
    DRAFT = "draft"             # human authored, not yet submitted
    ARCHIVED = "archived"


class TestCase(Base, IdMixin, TimestampMixin):
    __tablename__ = "test_cases"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(48), index=True)      # PROJ-T-0042
    title: Mapped[str] = mapped_column(String(400))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(20), default=TestCategory.MANUAL, index=True)
    status: Mapped[str] = mapped_column(String(20), default=TestStatus.PROPOSED, index=True)
    priority: Mapped[str] = mapped_column(String(16), default="medium")
    risk: Mapped[str] = mapped_column(String(16), default="medium")
    tags: Mapped[list] = mapped_column(JSONish, default=list)

    # Why this test exists, in the agent's words. Reviewers approve rationale,
    # not just steps.
    rationale: Mapped[str] = mapped_column(Text, default="")
    preconditions: Mapped[list] = mapped_column(JSONish, default=list)
    test_data: Mapped[dict] = mapped_column(JSONish, default=dict)
    charter: Mapped[str] = mapped_column(Text, default="")  # exploratory only

    # Traceability: list of RequirementItem.ref
    requirement_refs: Mapped[list] = mapped_column(JSONish, default=list)

    # Provenance of every generated artifact: source doc, prompt hash, model,
    # provider, agent role, approver. Required for the compliance export.
    provenance: Mapped[dict] = mapped_column(JSONish, default=dict)

    # Semantic fingerprint used for near-duplicate suppression.
    embedding: Mapped[list | None] = mapped_column(JSONish, nullable=True)
    dedupe_hash: Mapped[str] = mapped_column(String(64), default="", index=True)

    version: Mapped[int] = mapped_column(Integer, default=1)
    approved_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    review_comment: Mapped[str] = mapped_column(Text, default="")

    # Rolling intelligence, maintained by the intelligence layer.
    flake_score: Mapped[float] = mapped_column(Float, default=0.0)
    last_status: Mapped[str] = mapped_column(String(20), default="unknown")
    avg_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    quarantined: Mapped[bool] = mapped_column(Boolean, default=False)

    steps: Mapped[list[TestStep]] = relationship(
        back_populates="test_case",
        cascade="all, delete-orphan",
        order_by="TestStep.index",
    )
    versions: Mapped[list[TestVersion]] = relationship(
        back_populates="test_case", cascade="all, delete-orphan"
    )


class StepAction(StrEnum):
    """The executable vocabulary. Deliberately small and auditable."""

    GOTO = "goto"
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    FILL = "fill"
    TYPE = "type"
    PRESS = "press"
    SELECT = "select"
    CHECK = "check"
    UNCHECK = "uncheck"
    HOVER = "hover"
    UPLOAD = "upload"
    SCROLL = "scroll"
    WAIT_FOR = "wait_for"
    EXPECT_VISIBLE = "expect_visible"
    EXPECT_TEXT = "expect_text"
    EXPECT_VALUE = "expect_value"
    EXPECT_URL = "expect_url"
    EXPECT_COUNT = "expect_count"
    EXPECT_ATTRIBUTE = "expect_attribute"
    # Judged by a model against the page snapshot when AI is on; skipped with a
    # loud "unverified" marker when it is off. Never silently passed.
    EXPECT_SEMANTIC = "expect_semantic"
    SNAPSHOT = "snapshot"          # named visual baseline
    API_REQUEST = "api_request"
    SET_STORAGE = "set_storage"
    SCRIPT = "script"              # sandboxed page function, approval-gated
    # Chaos / resilience injection
    NETWORK_CONDITION = "network_condition"
    ROUTE_FAULT = "route_fault"
    # Human-in-the-loop escape hatch: pause the live browser, hand the session
    # to a person (SSO, CAPTCHA, shadow-DOM blocker), resume on their signal.
    HANDOFF = "handoff"
    ASSERT_A11Y = "assert_a11y"
    ASSERT_PERF = "assert_perf"
    NOTE = "note"                  # manual/exploratory instruction, no automation


class TestStep(Base, IdMixin, TimestampMixin):
    __tablename__ = "test_steps"

    test_case_id: Mapped[str] = mapped_column(
        ForeignKey("test_cases.id", ondelete="CASCADE"), index=True
    )
    index: Mapped[int] = mapped_column(Integer, default=0)
    action: Mapped[str] = mapped_column(String(32), default=StepAction.NOTE)

    # Human-readable, always present. This is the durable contract; selectors are
    # an implementation detail that may be re-derived at any time.
    intent: Mapped[str] = mapped_column(Text, default="")
    expected: Mapped[str] = mapped_column(Text, default="")

    # Locator strategy, ordered by preference. The first entry is the fast
    # deterministic path; the rest are healing fallbacks.
    target: Mapped[dict] = mapped_column(JSONish, default=dict)
    element_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)

    value: Mapped[dict] = mapped_column(JSONish, default=dict)
    options: Mapped[dict] = mapped_column(JSONish, default=dict)
    timeout_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    optional: Mapped[bool] = mapped_column(Boolean, default=False)
    continue_on_failure: Mapped[bool] = mapped_column(Boolean, default=False)

    test_case: Mapped[TestCase] = relationship(back_populates="steps")


class TestVersion(Base, IdMixin, TimestampMixin):
    """Immutable snapshot taken on every approved change. Enables diff review."""

    __tablename__ = "test_versions"

    test_case_id: Mapped[str] = mapped_column(
        ForeignKey("test_cases.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    snapshot: Mapped[dict] = mapped_column(JSONish, default=dict)
    change_summary: Mapped[str] = mapped_column(Text, default="")
    author_kind: Mapped[str] = mapped_column(String(16), default="human")  # human|agent
    author_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(40), nullable=True)

    test_case: Mapped[TestCase] = relationship(back_populates="versions")


# --------------------------------------------------------------------------- #
# Suites & scheduling
# --------------------------------------------------------------------------- #
class TestSuite(Base, IdMixin, TimestampMixin):
    __tablename__ = "test_suites"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    # Static membership, or a saved query ("tags contains uat and priority=high").
    kind: Mapped[str] = mapped_column(String(16), default="static")  # static|dynamic
    query: Mapped[dict] = mapped_column(JSONish, default=dict)
    parallelism: Mapped[int] = mapped_column(Integer, default=2)
    browsers: Mapped[list] = mapped_column(JSONish, default=lambda: ["chromium"])

    members: Mapped[list[SuiteMember]] = relationship(
        back_populates="suite", cascade="all, delete-orphan", order_by="SuiteMember.position"
    )


class SuiteMember(Base, IdMixin):
    __tablename__ = "suite_members"
    __table_args__ = (UniqueConstraint("suite_id", "test_case_id", name="uq_suite_member"),)

    suite_id: Mapped[str] = mapped_column(ForeignKey("test_suites.id", ondelete="CASCADE"))
    test_case_id: Mapped[str] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=0)

    suite: Mapped[TestSuite] = relationship(back_populates="members")


class Schedule(Base, IdMixin, TimestampMixin):
    __tablename__ = "schedules"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    cron: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    suite_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    selection: Mapped[dict] = mapped_column(JSONish, default=dict)
    environment: Mapped[str] = mapped_column(String(64), default="default")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    next_fire_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #
class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    BLOCKED = "blocked"        # waiting on a human handoff
    FLAKY = "flaky"            # passed only after retry
    NEEDS_REVIEW = "needs_review"


TERMINAL_RUN_STATES = {
    RunStatus.PASSED, RunStatus.FAILED, RunStatus.ERROR,
    RunStatus.CANCELLED, RunStatus.FLAKY, RunStatus.NEEDS_REVIEW,
}


class Run(Base, IdMixin, TimestampMixin):
    __tablename__ = "runs"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer, default=0, index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    trigger: Mapped[str] = mapped_column(String(32), default="manual")  # manual|chat|schedule|ci|api|rerun
    triggered_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Free-text chat command that produced this run, when applicable.
    command: Mapped[str] = mapped_column(Text, default="")
    environment: Mapped[str] = mapped_column(String(64), default="default")
    base_url: Mapped[str] = mapped_column(String(500), default="")
    browsers: Mapped[list] = mapped_column(JSONish, default=lambda: ["chromium"])
    suite_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    parent_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    selection: Mapped[dict] = mapped_column(JSONish, default=dict)

    status: Mapped[str] = mapped_column(String(20), default=RunStatus.QUEUED, index=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)

    totals: Mapped[dict] = mapped_column(JSONish, default=dict)
    # Populated by the regression triage pass: new vs known vs flaky.
    triage: Mapped[dict] = mapped_column(JSONish, default=dict)
    git_sha: Mapped[str] = mapped_column(String(64), default="")
    git_branch: Mapped[str] = mapped_column(String(200), default="")
    ci_metadata: Mapped[dict] = mapped_column(JSONish, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")

    results: Mapped[list[RunTest]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class RunTest(Base, IdMixin, TimestampMixin):
    __tablename__ = "run_tests"

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    test_case_id: Mapped[str] = mapped_column(String(40), index=True)
    test_key: Mapped[str] = mapped_column(String(48), default="")
    title: Mapped[str] = mapped_column(String(400), default="")
    browser: Mapped[str] = mapped_column(String(32), default="chromium")
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.QUEUED, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(Text, default="")
    error_type: Mapped[str] = mapped_column(String(80), default="")
    # Fingerprint of the failure signature; how "known vs new" is decided.
    failure_signature: Mapped[str] = mapped_column(String(64), default="", index=True)
    healed: Mapped[bool] = mapped_column(Boolean, default=False)
    classification: Mapped[str] = mapped_column(String(32), default="")  # new|known|flaky|env|data
    console_errors: Mapped[list] = mapped_column(JSONish, default=list)
    network_failures: Mapped[list] = mapped_column(JSONish, default=list)
    metrics: Mapped[dict] = mapped_column(JSONish, default=dict)

    run: Mapped[Run] = relationship(back_populates="results")
    steps: Mapped[list[RunStepRecord]] = relationship(
        back_populates="run_test", cascade="all, delete-orphan", order_by="RunStepRecord.index"
    )


class RunStepRecord(Base, IdMixin):
    __tablename__ = "run_steps"

    run_test_id: Mapped[str] = mapped_column(
        ForeignKey("run_tests.id", ondelete="CASCADE"), index=True
    )
    step_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    index: Mapped[int] = mapped_column(Integer, default=0)
    action: Mapped[str] = mapped_column(String(32), default="")
    intent: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.QUEUED)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    resolved_locator: Mapped[str] = mapped_column(Text, default="")
    heal_applied: Mapped[dict | None] = mapped_column(JSONish, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, default="")
    logs: Mapped[list] = mapped_column(JSONish, default=list)
    artifacts: Mapped[list] = mapped_column(JSONish, default=list)

    run_test: Mapped[RunTest] = relationship(back_populates="steps")


class Artifact(Base, IdMixin, TimestampMixin):
    __tablename__ = "artifacts"

    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    run_test_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), default="screenshot")
    # screenshot | video | trace | har | console | a11y | snapshot | report | diff
    path: Mapped[str] = mapped_column(String(1000), default="")
    label: Mapped[str] = mapped_column(String(300), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict] = mapped_column(JSONish, default=dict)
