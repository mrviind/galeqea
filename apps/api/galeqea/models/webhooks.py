"""Outbound webhooks: GaleQEA telling other systems what happened.

An endpoint subscribes to a set of events (``run.finished``, ``run.failed``,
``heal.proposed``, ``approval.requested``); when one fires, its JSON report
summary is POSTed to the endpoint's URL, signed with HMAC-SHA256 over the raw
body so the receiver can verify it. Every attempt is recorded in
``WebhookDelivery``, a visible log, because a webhook that fails silently is
worse than none.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import IdMixin, JSONish, TimestampMixin, UTCDateTime

#: The events an endpoint may subscribe to.
WEBHOOK_EVENTS = ("run.finished", "run.failed", "heal.proposed", "approval.requested",
                  "cycle.finished", "milestone.signed_off")


class WebhookEndpoint(Base, IdMixin, TimestampMixin):
    __tablename__ = "webhook_endpoints"

    project_id: Mapped[str] = mapped_column(String(40), index=True)
    url: Mapped[str] = mapped_column(String(2000))
    #: Shared secret for the HMAC-SHA256 signature. Never returned by the API.
    secret: Mapped[str] = mapped_column(String(200), default="")
    events: Mapped[list] = mapped_column(JSONish, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(String(300), default="")
    created_by: Mapped[str | None] = mapped_column(String(40), nullable=True)


class WebhookDelivery(Base, IdMixin, TimestampMixin):
    __tablename__ = "webhook_deliveries"

    endpoint_id: Mapped[str] = mapped_column(String(40), index=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    event: Mapped[str] = mapped_column(String(48), index=True)
    #: delivered | failed
    status: Mapped[str] = mapped_column(String(16), default="failed", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    request_body: Mapped[str] = mapped_column(Text, default="")
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
