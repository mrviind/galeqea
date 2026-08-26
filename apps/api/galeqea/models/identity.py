"""Users, roles, workspaces and projects."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from .base import IdMixin, JSONish, TimestampMixin, UTCDateTime, new_id


class Role(StrEnum):
    """Ordered least- to most-privileged. Comparisons use ``RANK``."""

    VIEWER = "viewer"
    AUTHOR = "author"
    APPROVER = "approver"
    ADMIN = "admin"
    OWNER = "owner"
    # A non-human principal. Structurally barred from approving anything.
    AGENT = "agent"


RANK: dict[str, int] = {
    Role.VIEWER: 0,
    Role.AUTHOR: 1,
    Role.APPROVER: 2,
    Role.ADMIN: 3,
    Role.OWNER: 4,
    Role.AGENT: -1,
}


class User(Base, IdMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[str] = mapped_column(String(20), default=Role.OWNER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # True for the agent principal; can never satisfy an approval gate.
    is_machine: Mapped[bool] = mapped_column(Boolean, default=False)
    preferences: Mapped[dict] = mapped_column(JSONish, default=dict)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    def at_least(self, role: Role) -> bool:
        return RANK.get(self.role, -1) >= RANK[role]


class ApiToken(Base, IdMixin, TimestampMixin):
    """Least-privilege scoped token, used by CI and by the remote MCP server."""

    __tablename__ = "api_tokens"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(128), index=True)
    prefix: Mapped[str] = mapped_column(String(12))
    scopes: Mapped[list] = mapped_column(JSONish, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Project(Base, IdMixin, TimestampMixin):
    __tablename__ = "projects"

    key: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    # Application-under-test targets: {"staging": "https://...", "uat": "..."}
    environments: Mapped[dict] = mapped_column(JSONish, default=dict)
    default_environment: Mapped[str] = mapped_column(String(64), default="default")
    settings: Mapped[dict] = mapped_column(JSONish, default=dict)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)

    members: Mapped[list[ProjectMember]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ProjectMember(Base, TimestampMixin):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_member"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("pm"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20), default=Role.AUTHOR)

    project: Mapped[Project] = relationship(back_populates="members")
