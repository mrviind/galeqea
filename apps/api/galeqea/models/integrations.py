"""External system connections and the plugin registry."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import IdMixin, JSONish, TimestampMixin, UTCDateTime


class IntegrationConnection(Base, IdMixin, TimestampMixin):
    """A configured external system. Credentials live in the vault, never here."""

    __tablename__ = "integration_connections"

    project_id: Mapped[str] = mapped_column(String(40), index=True)
    provider: Mapped[str] = mapped_column(String(48), index=True)
    # jira | xray | github | gitlab | bitbucket | jenkins | github_actions |
    # gitlab_ci | azure_devops | slack | webhook
    name: Mapped[str] = mapped_column(String(200), default="")
    config: Mapped[dict] = mapped_column(JSONish, default=dict)   # non-secret only
    secret_refs: Mapped[dict] = mapped_column(JSONish, default=dict)  # name -> VaultSecret.id
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(24), default="unverified")
    status_detail: Mapped[str] = mapped_column(Text, default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Cached bearer tokens (e.g. Xray's 24h token) with their expiry so we do not
    # re-authenticate on every call.
    token_cache: Mapped[dict] = mapped_column(JSONish, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(40), nullable=True)


class JiraIssueMap(Base, IdMixin, TimestampMixin):
    """A Jira story imported as a requirement. Keeps the source anchor so re-imports
    are idempotent and a description change can mark the linked tests stale."""

    __tablename__ = "jira_issue_maps"
    __table_args__ = (
        UniqueConstraint("project_id", "issue_id", name="uq_jira_issue_map"),
    )

    project_id: Mapped[str] = mapped_column(String(40), index=True)
    issue_id: Mapped[str] = mapped_column(String(64), index=True)
    issue_key: Mapped[str] = mapped_column(String(64), index=True)
    #: The RequirementItem.ref this story became (the key itself).
    requirement_ref: Mapped[str] = mapped_column(String(64), default="")
    #: Hash of the imported description, so a later change is detectable.
    description_hash: Mapped[str] = mapped_column(String(64), default="")
    remote_updated: Mapped[str] = mapped_column(String(40), default="")
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Whether coverage (a "Test" link/comment) has been written back to the story.
    coverage_written: Mapped[bool] = mapped_column(Boolean, default=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class RunExport(Base, IdMixin, TimestampMixin):
    """A run's results pushed to an external test-management system. The
    ``idempotency_key`` makes a re-push of the same run to the same target a no-op
    that returns the stored execution key instead of creating a duplicate."""

    __tablename__ = "run_exports"
    __table_args__ = (
        UniqueConstraint("run_id", "provider", "idempotency_key", name="uq_run_export"),
    )

    project_id: Mapped[str] = mapped_column(String(40), index=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    provider: Mapped[str] = mapped_column(String(24), index=True)  # xray|zephyr_scale|testrail
    target: Mapped[str] = mapped_column(String(120), default="")   # plan/cycle/run key
    idempotency_key: Mapped[str] = mapped_column(String(64), default="")
    exec_key: Mapped[str] = mapped_column(String(120), default="")
    url: Mapped[str] = mapped_column(String(600), default="")
    pushed: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="pushed")


class ReportPage(Base, IdMixin, TimestampMixin):
    """A published Confluence page for a release report. Kept so a re-publish updates
    the same page in place (version+1) instead of creating a new one each time."""

    __tablename__ = "report_pages"
    __table_args__ = (
        UniqueConstraint("project_id", "milestone_id", "space_key", name="uq_report_page"),
    )

    project_id: Mapped[str] = mapped_column(String(40), index=True)
    milestone_id: Mapped[str] = mapped_column(String(40), index=True)
    space_key: Mapped[str] = mapped_column(String(64), default="")
    page_id: Mapped[str] = mapped_column(String(64), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(300), default="")
    url: Mapped[str] = mapped_column(String(600), default="")


class PluginRecord(Base, IdMixin, TimestampMixin):
    __tablename__ = "plugins"

    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    version: Mapped[str] = mapped_column(String(32), default="0.0.0")
    kind: Mapped[str] = mapped_column(String(32), default="reporter")
    # reporter | integration | model_provider | step_action | ui_panel | analyzer
    manifest: Mapped[dict] = mapped_column(JSONish, default=dict)
    entrypoint: Mapped[str] = mapped_column(String(400), default="")
    source_path: Mapped[str] = mapped_column(String(1000), default="")
    checksum: Mapped[str] = mapped_column(String(64), default="")
    # Capability grants the plugin requested and an admin approved. Anything not
    # granted is unavailable inside the sandbox.
    granted_permissions: Mapped[list] = mapped_column(JSONish, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    trusted: Mapped[bool] = mapped_column(Boolean, default=False)
    install_error: Mapped[str] = mapped_column(Text, default="")
    load_count: Mapped[int] = mapped_column(Integer, default=0)


class Notification(Base, IdMixin, TimestampMixin):
    __tablename__ = "notifications"

    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(48), default="info")
    title: Mapped[str] = mapped_column(String(300), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(500), default="")
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    severity: Mapped[str] = mapped_column(String(16), default="info")
