"""The enqueueable tasks (WO#4 P2-1).

Each is an async function taking only JSON-serialisable kwargs and opening its own DB
session, so the identical function runs in-process (SQLite) or inside a Procrastinate
worker (Postgres). Registered by name in the queue registry.
"""

from __future__ import annotations

import asyncio

from .base import task


@task("run_execute")
async def run_execute(*, run_id: str, project_id: str) -> None:
    """Execute one test run to completion (the run's whole lifecycle)."""
    from ..services.runs import _dispatch
    await _dispatch(run_id, project_id)


@task("retention_sweep")
async def retention_sweep(**_kw) -> None:
    """Delete artifacts past each project's retention window."""
    from ..services.retention import sweep_now
    await asyncio.to_thread(sweep_now)


@task("deliver_webhook")
async def deliver_webhook(*, endpoint_id: str, event_name: str, body: str) -> None:
    """Deliver one event to one webhook endpoint, with Standard-Webhooks signing + retries."""
    from ..services.webhooks import deliver_to_endpoint
    await deliver_to_endpoint(endpoint_id, event_name, body)


@task("render_share_pdf")
async def render_share_pdf(*, token: str, run_id: str, stakeholder: bool = False) -> None:
    """Render the PDF for a published share, out of the request path."""
    from ..services.share import finalize_pdf
    await finalize_pdf(token, run_id, stakeholder=stakeholder)
