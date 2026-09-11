"""Share: publish a release report as durable, shareable artifacts.

A share bundles the report in every format a consumer might want (Markdown for an
AI, HTML for a browser, JSON for a tool, JUnit for CI) behind one token, written
through the storage interface (local now, S3 later). A share can be public with an
expiry, and a stakeholder variant redacts names, links and screenshots. Nothing is
a dead end: the formats are always produced; the team integrations (Slack / Jira /
Confluence / Xray) are offered but gated behind an approval.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets

from ..models import Run
from ..models.base import utcnow
from .storage import get_storage

log = logging.getLogger("galeqea.share")

#: filename → (renderer key, content type)
_FORMATS = {
    "md": ("markdown", "text/markdown; charset=utf-8"),
    "html": ("html", "text/html; charset=utf-8"),
    "json": ("json", "application/json"),
    "junit": ("junit", "application/xml"),
    "pdf": ("pdf", "application/pdf"),
}
_FILENAME = {"md": "report.md", "html": "report.html", "json": "report.json",
             "junit": "report.junit.xml", "pdf": "report.pdf"}
_BY_FILENAME = {v: k for k, v in _FILENAME.items()}


def _render(db, project, journey, run, fmt: str, *, stakeholder: bool):
    from ..reports import report_v2
    from ..reports.runs import run_report_junit

    report = report_v2.build(db, project, run, journey, stakeholder=stakeholder)
    if fmt == "md":
        return report_v2.to_markdown(report).encode()
    if fmt == "html":
        return report_v2.to_html(report).encode()
    if fmt == "json":
        return json.dumps(report, indent=2).encode()
    if fmt == "junit":
        # The v2 report already carries results/summary/run (stakeholder-redacted
        # when asked), so JUnit renders from the same one document.
        return run_report_junit(report).encode()
    raise ValueError(f"unknown format {fmt!r}")


async def _render_pdf(db, project, journey, run, *, stakeholder: bool) -> tuple[bytes | None, str | None]:
    """Render the stakeholder HTML to a PDF with the runner's Chromium. A share
    never *fails* for want of a PDF, but the reason is returned and never swallowed,
    so the response and the card can say ready / failed(<reason>)."""
    from ..engine.supervisor import RunSupervisor
    html = _render(db, project, journey, run, "html", stakeholder=stakeholder).decode()
    try:
        return await RunSupervisor().render_pdf(html)
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"[:200]


async def publish(db, project, journey, run: Run, *, formats: list[str] | None = None,
                  public: bool = True, expires_days: int = 30, stakeholder: bool = False,
                  actor: str | None = None) -> dict:
    """Render the report in each format, store it, and return the share. PDF is
    rendered through the runner; stakeholder shares include it by default."""
    default = ["md", "html", "json", "junit", "pdf"]
    requested = [f for f in (formats or default) if f in _FORMATS]
    token = secrets.token_urlsafe(9)
    storage = get_storage()
    urls: dict[str, str] = {}
    pdf_status = "ready" if "pdf" in requested else "not_requested"
    for fmt in requested:
        _renderer, ctype = _FORMATS[fmt]
        if fmt == "pdf":
            # On the durable Postgres queue, render the PDF in a worker so a slow
            # Chromium spawn never blocks this request; the share's PDF appears
            # shortly (P2-1). In-process (SQLite) keeps rendering inline, so the
            # zero-config contract is unchanged.
            from ..jobs import get_queue
            queue = get_queue()
            if queue.kind == "procrastinate":
                await queue.enqueue("render_share_pdf", token=token, run_id=run.id,
                                    stakeholder=stakeholder)
                pdf_status = "generating"
                continue
            data, reason = await _render_pdf(db, project, journey, run, stakeholder=stakeholder)
            if not data:
                # Never silent: the reason is logged and reported, the PDF omitted.
                pdf_status = f"failed ({reason})"
                log.warning("share %s: PDF render failed for run %s: %s", token, run.id, reason)
                continue
        else:
            data = _render(db, project, journey, run, fmt, stakeholder=stakeholder)
        key = f"shares/{token}/{_FILENAME[fmt]}"
        await asyncio.to_thread(storage.put, key, data, content_type=ctype)
        urls[fmt] = storage.url(key)
        log.info("share %s: stored %s for run %s", token, fmt, run.id)
    stored_formats = list(urls)
    expires_at = (utcnow().timestamp() + expires_days * 86400) if expires_days else None
    meta = {
        "token": token, "run_id": run.id, "run_number": run.number,
        "project_id": project.id, "public": public, "stakeholder": stakeholder,
        "created_at": utcnow().isoformat(), "expires_at": expires_at,
        "formats": stored_formats, "pdf_status": pdf_status, "created_by": actor,
    }
    await asyncio.to_thread(storage.put, f"shares/{token}/meta.json",
                            json.dumps(meta).encode(), content_type="application/json")
    return {"token": token, "public": public, "stakeholder": stakeholder,
            "expires_at": expires_at, "formats": stored_formats, "urls": urls,
            "pdf_status": pdf_status,
            "share_url": storage.url(f"shares/{token}/{_FILENAME['html']}")}


async def finalize_pdf(token: str, run_id: str, *, stakeholder: bool = False) -> None:
    """Render a published share's PDF and attach it: the ``render_share_pdf`` job.
    Renders through the runner, stores it, and flips the share's ``pdf_status`` in
    its meta from ``generating`` to ``ready`` (or ``failed (...)``)."""
    from sqlalchemy import select

    from ..db import session_scope
    from ..models import Journey, Project, Run

    storage = get_storage()
    with session_scope() as db:
        run = db.get(Run, run_id)
        if run is None:
            return
        project = db.get(Project, run.project_id)
        journey = db.execute(
            select(Journey).where(Journey.run_id == run_id)).scalars().first()
        data, reason = await _render_pdf(db, project, journey, run, stakeholder=stakeholder)

    try:
        meta = json.loads(storage.get(f"shares/{token}/meta.json"))
    except Exception:  # noqa: BLE001 (the share may have expired/been removed)
        return
    if data:
        storage.put(f"shares/{token}/{_FILENAME['pdf']}", data, content_type=_FORMATS['pdf'][1])
        meta["pdf_status"] = "ready"
        if "pdf" not in meta.get("formats", []):
            meta["formats"] = [*meta.get("formats", []), "pdf"]
    else:
        meta["pdf_status"] = f"failed ({reason})"
    await asyncio.to_thread(storage.put, f"shares/{token}/meta.json",
                            json.dumps(meta).encode(), content_type="application/json")


def fetch(token: str, filename: str, *, now_ts: float | None = None) -> tuple[bytes, str]:
    """Return (bytes, content_type) for a public, unexpired share artifact.

    Raises ``PermissionError`` if the share is private, ``FileNotFoundError`` if it
    is missing or expired."""
    storage = get_storage()
    meta_key = f"shares/{token}/meta.json"
    if not storage.exists(meta_key):
        raise FileNotFoundError("no such share")
    meta = json.loads(storage.get(meta_key))
    if not meta.get("public"):
        raise PermissionError("this share is private")
    exp = meta.get("expires_at")
    if exp and (now_ts or utcnow().timestamp()) > exp:
        raise FileNotFoundError("this share has expired")
    fmt = _BY_FILENAME.get(filename)
    if fmt is None or fmt not in meta.get("formats", []):
        raise FileNotFoundError("no such format in this share")
    key = f"shares/{token}/{filename}"
    if not storage.exists(key):
        raise FileNotFoundError("artifact missing")
    return storage.get(key), _FORMATS[fmt][1]


def revoke(token: str) -> None:
    get_storage().delete(f"shares/{token}")
