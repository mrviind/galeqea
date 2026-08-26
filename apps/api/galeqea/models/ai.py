"""Chat, agent traces and long-term memory."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from .base import IdMixin, JSONish, TimestampMixin, UTCDateTime


class AgentRole(StrEnum):
    ORCHESTRATOR = "orchestrator"
    REQUIREMENT_ANALYST = "requirement_analyst"
    TEST_DESIGNER = "test_designer"
    SCRIPT_GENERATOR = "script_generator"
    EXECUTOR = "executor"
    EXPLORER = "explorer"          # autonomous Plan-Act-Verify exploratory agent
    HEALER = "healer"
    RCA_ANALYST = "rca_analyst"
    JUDGE = "judge"                # LLM-as-judge, ambiguous outcomes only
    COVERAGE_CARTOGRAPHER = "coverage_cartographer"
    DATA_ARCHITECT = "data_architect"


class ChatSession(Base, IdMixin, TimestampMixin):
    __tablename__ = "chat_sessions"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300), default="New conversation")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    token_usage: Mapped[dict] = mapped_column(JSONish, default=dict)
    context: Mapped[dict] = mapped_column(JSONish, default=dict)

    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.created_at"
    )


class ChatMessage(Base, IdMixin, TimestampMixin):
    __tablename__ = "chat_messages"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20), default="user")  # user|assistant|system|tool|event
    agent_role: Mapped[str] = mapped_column(String(48), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    # Rich, typed payloads the UI renders as cards: proposals, run controls,
    # approval prompts, timelines, charts.
    blocks: Mapped[list] = mapped_column(JSONish, default=list)
    tool_calls: Mapped[list] = mapped_column(JSONish, default=list)
    attachments: Mapped[list] = mapped_column(JSONish, default=list)
    trace_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    usage: Mapped[dict] = mapped_column(JSONish, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class AgentTrace(Base, IdMixin, TimestampMixin):
    """One end-to-end reasoning episode. The unit an auditor replays."""

    __tablename__ = "agent_traces"

    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    goal: Mapped[str] = mapped_column(Text, default="")
    agent_role: Mapped[str] = mapped_column(String(48), default=AgentRole.ORCHESTRATOR)
    provider: Mapped[str] = mapped_column(String(48), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(24), default="running")
    steps: Mapped[list] = mapped_column(JSONish, default=list)
    prompt_sha256: Mapped[str] = mapped_column(String(64), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class MemoryItem(Base, IdMixin, TimestampMixin):
    """Inspectable, editable, exportable agent memory.

    Nothing is remembered that a user cannot open, correct or delete. Memory that
    cannot be audited is a liability in a compliance-facing tool.
    """

    __tablename__ = "memory_items"

    project_id: Mapped[str] = mapped_column(String(40), index=True)
    scope: Mapped[str] = mapped_column(String(32), default="project")  # project|session|global
    session_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), default="fact")
    # fact | convention | glossary | preference | failure_pattern | app_knowledge
    key: Mapped[str] = mapped_column(String(200), default="", index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(200), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    embedding: Mapped[list | None] = mapped_column(JSONish, nullable=True)
    hits: Mapped[int] = mapped_column(Integer, default=0)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_by_kind: Mapped[str] = mapped_column(String(16), default="agent")
