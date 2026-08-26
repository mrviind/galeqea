"""External system connections and the plugin registry."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Integer, String, Text
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
