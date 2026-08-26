"""Intelligence layer persistence: flakiness, RCA, anomalies, coverage, cost."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import IdMixin, JSONish, TimestampMixin, UTCDateTime


class TestStat(Base, IdMixin, TimestampMixin):
    """Rolling per-test statistics. The feature store for flakiness and PTS."""

    __tablename__ = "test_stats"

    project_id: Mapped[str] = mapped_column(String(40), index=True)
    test_case_id: Mapped[str] = mapped_column(String(40), index=True, unique=True)

    runs: Mapped[int] = mapped_column(Integer, default=0)
    passes: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    # Times the outcome flipped without the code under test changing - the
    # single strongest flakiness signal available without re-running anything.
    flips: Mapped[int] = mapped_column(Integer, default=0)
    same_sha_disagreements: Mapped[int] = mapped_column(Integer, default=0)
    retry_rescues: Mapped[int] = mapped_column(Integer, default=0)

    flake_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    flake_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    flake_reasons: Mapped[list] = mapped_column(JSONish, default=list)

    mean_duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    m2_duration: Mapped[float] = mapped_column(Float, default=0.0)  # Welford, for online stddev
    p95_duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    duration_samples: Mapped[list] = mapped_column(JSONish, default=list)

    heal_count: Mapped[int] = mapped_column(Integer, default=0)
    last_status: Mapped[str] = mapped_column(String(20), default="unknown")
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    outcome_window: Mapped[list] = mapped_column(JSONish, default=list)  # newest-first, capped

    # Files whose change has historically preceded this test failing. Drives
    # predictive test selection without needing a trained model to be present.
    correlated_paths: Mapped[dict] = mapped_column(JSONish, default=dict)
    value_score: Mapped[float] = mapped_column(Float, default=0.5)


class FailureSignature(Base, IdMixin, TimestampMixin):
    """A normalised failure identity. Turns 'is this new?' into a lookup."""

    __tablename__ = "failure_signatures"

    project_id: Mapped[str] = mapped_column(String(40), index=True)
    signature: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str] = mapped_column(String(400), default="")
    normalized_message: Mapped[str] = mapped_column(Text, default="")
    error_type: Mapped[str] = mapped_column(String(80), default="")
    first_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    occurrences: Mapped[int] = mapped_column(Integer, default=0)
    affected_tests: Mapped[list] = mapped_column(JSONish, default=list)
    known_issue: Mapped[bool] = mapped_column(Boolean, default=False)
    ticket_ref: Mapped[str] = mapped_column(String(120), default="")
    muted_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class RCAReport(Base, IdMixin, TimestampMixin):
    """Confidence-scored, evidence-cited root-cause analysis."""

    __tablename__ = "rca_reports"

    project_id: Mapped[str] = mapped_column(String(40), index=True)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    run_test_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    test_case_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    signature: Mapped[str] = mapped_column(String(64), default="", index=True)

    # product_defect | test_defect | environment | data | timing_flake |
    # dependency | infrastructure | requirement_gap
    category: Mapped[str] = mapped_column(String(48), default="unknown")
    summary: Mapped[str] = mapped_column(Text, default="")
    # Ranked hypotheses; each carries its own confidence and citations so a
    # reader can disagree with the ranking without losing the evidence.
    hypotheses: Mapped[list] = mapped_column(JSONish, default=list)
    evidence: Mapped[list] = mapped_column(JSONish, default=list)
    suspect_commits: Mapped[list] = mapped_column(JSONish, default=list)
    suggested_fix: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    generated_by: Mapped[str] = mapped_column(String(32), default="heuristic")  # heuristic|llm|hybrid
    model: Mapped[str] = mapped_column(String(120), default="")
    reviewed_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    human_verdict: Mapped[str] = mapped_column(String(32), default="")
    ticket_ref: Mapped[str] = mapped_column(String(120), default="")


class AnomalyRecord(Base, IdMixin, TimestampMixin):
    __tablename__ = "anomaly_records"

    project_id: Mapped[str] = mapped_column(String(40), index=True)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    scope: Mapped[str] = mapped_column(String(32), default="run")
    metric: Mapped[str] = mapped_column(String(64), default="duration_ms")
    subject_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    observed: Mapped[float] = mapped_column(Float, default=0.0)
    expected: Mapped[float] = mapped_column(Float, default=0.0)
    deviation_sigma: Mapped[float] = mapped_column(Float, default=0.0)
    severity: Mapped[str] = mapped_column(String(16), default="info")
    detail: Mapped[dict] = mapped_column(JSONish, default=dict)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)


class JudgeVerdict(Base, IdMixin, TimestampMixin):
    """LLM-as-judge output for an ambiguous outcome. Always human-overridable."""

    __tablename__ = "judge_verdicts"

    project_id: Mapped[str] = mapped_column(String(40), index=True)
    run_test_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    step_index: Mapped[int] = mapped_column(Integer, default=0)
    question: Mapped[str] = mapped_column(Text, default="")
    verdict: Mapped[str] = mapped_column(String(24), default="unknown")  # pass|fail|inconclusive
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    evidence_refs: Mapped[list] = mapped_column(JSONish, default=list)
    model: Mapped[str] = mapped_column(String(120), default="")
    # Multiple independent samples; disagreement lowers confidence rather than
    # being averaged away.
    samples: Mapped[list] = mapped_column(JSONish, default=list)
    human_override: Mapped[str] = mapped_column(String(24), default="")
    overridden_by: Mapped[str | None] = mapped_column(String(40), nullable=True)


class CoverageSnapshot(Base, IdMixin, TimestampMixin):
    """Requirement- and journey-level coverage, including explicit gaps."""

    __tablename__ = "coverage_snapshots"

    project_id: Mapped[str] = mapped_column(String(40), index=True)
    total_requirements: Mapped[int] = mapped_column(Integer, default=0)
    covered_requirements: Mapped[int] = mapped_column(Integer, default=0)
    automated_requirements: Mapped[int] = mapped_column(Integer, default=0)
    uncovered_refs: Mapped[list] = mapped_column(JSONish, default=list)
    weak_refs: Mapped[list] = mapped_column(JSONish, default=list)
    by_risk: Mapped[dict] = mapped_column(JSONish, default=dict)
    journey_coverage: Mapped[dict] = mapped_column(JSONish, default=dict)


class UsageLedger(Base, IdMixin, TimestampMixin):
    """Token/cost accounting so AI spend is visible before it is a surprise."""

    __tablename__ = "usage_ledger"

    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(48), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    agent_role: Mapped[str] = mapped_column(String(48), default="")
    operation: Mapped[str] = mapped_column(String(64), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    trace_id: Mapped[str | None] = mapped_column(String(40), nullable=True)


class ExplorationSession(Base, IdMixin, TimestampMixin):
    """One autonomous exploratory run against a live application.

    Exploration is not a test: it has a charter and a time box rather than an
    expected result, and its output is *findings* a human triages - not a
    pass/fail. Modelling it separately keeps it out of the pass-rate statistics,
    where it would be meaningless.
    """

    __tablename__ = "exploration_sessions"

    project_id: Mapped[str] = mapped_column(String(40), index=True)
    charter: Mapped[str] = mapped_column(Text, default="")
    test_case_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    environment: Mapped[str] = mapped_column(String(64), default="default")
    base_url: Mapped[str] = mapped_column(String(500), default="")

    # deterministic | model
    strategy: Mapped[str] = mapped_column(String(24), default="deterministic")
    model: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)

    max_steps: Mapped[int] = mapped_column(Integer, default=40)
    steps_taken: Mapped[int] = mapped_column(Integer, default=0)
    screens_seen: Mapped[int] = mapped_column(Integer, default=0)
    #: Every action taken, so a finding can be replayed exactly.
    trail: Mapped[list] = mapped_column(JSONish, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class ExplorationFinding(Base, IdMixin, TimestampMixin):
    """Something worth a human's attention, with the steps to reproduce it."""

    __tablename__ = "exploration_findings"

    project_id: Mapped[str] = mapped_column(String(40), index=True)
    session_id: Mapped[str] = mapped_column(String(40), index=True)

    # console_error | server_error | dead_end | accessibility | data_loss |
    # broken_link | unlabelled_control | slow_response | surprising_behaviour
    kind: Mapped[str] = mapped_column(String(40), default="surprising_behaviour", index=True)
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    title: Mapped[str] = mapped_column(String(400), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(600), default="")

    #: Ordered actions that reach this state. Without these a finding is a
    #: rumour, not a report.
    reproduction: Mapped[list] = mapped_column(JSONish, default=list)
    evidence: Mapped[dict] = mapped_column(JSONish, default=dict)
    screenshot_path: Mapped[str] = mapped_column(String(1000), default="")

    #: How confident the explorer is that this is a genuine defect rather than
    #: intended behaviour. Deterministic checks are certain; judgement is not.
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    found_by: Mapped[str] = mapped_column(String(24), default="deterministic")

    # new | accepted | dismissed | promoted (turned into a test case)
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    promoted_test_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    signature: Mapped[str] = mapped_column(String(64), default="", index=True)


class RecordingSession(Base, IdMixin, TimestampMixin):
    """One session where a person drove the browser and GaleQEA watched.

    The raw captured interactions are kept alongside the compressed step list on
    purpose. Compression is a set of judgement calls - which click was only a
    focus, which navigation was an outcome - and a reviewer who disagrees with
    one of them needs the original to argue with. Keeping only the tidy version
    would make every compression bug unfalsifiable.
    """

    __tablename__ = "recording_sessions"

    project_id: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(400), default="")
    environment: Mapped[str] = mapped_column(String(64), default="default")
    base_url: Mapped[str] = mapped_column(String(500), default="")
    start_url: Mapped[str] = mapped_column(String(600), default="")

    # starting | recording | finished | error | promoted
    status: Mapped[str] = mapped_column(String(24), default="starting", index=True)
    stop_reason: Mapped[str] = mapped_column(String(40), default="")
    started_by: Mapped[str | None] = mapped_column(String(40), nullable=True)

    max_actions: Mapped[int] = mapped_column(Integer, default=300)
    max_minutes: Mapped[int] = mapped_column(Integer, default=30)

    #: Every captured interaction, exactly as the browser reported it.
    actions: Mapped[list] = mapped_column(JSONish, default=list)
    #: The compressed proposal built from those actions.
    proposal: Mapped[dict] = mapped_column(JSONish, default=dict)
    stats: Mapped[dict] = mapped_column(JSONish, default=dict)

    test_case_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
