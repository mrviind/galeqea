"""Context endpoints for external agents.

Two documents an AI reads before it does anything else:

* ``GET /api/ai/context``: an llms.txt-style map of the whole surface (what
  GaleQEA is, how to talk to it over HTTP / MCP / CLI, where the OpenAPI schema
  is, the report formats, the approval-gate rule). Unauthenticated-safe: it names
  capabilities, never data.
* ``GET /api/projects/{id}/ai/context``: one Markdown briefing on a project's
  live state and the tools available.

Both render from ``reports.context`` so the HTTP and MCP surfaces never drift.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Project
from ...reports.context import project_context_markdown, surface_context_markdown
from ..deps import get_project

router = APIRouter(tags=["ai"])


def _md(text: str) -> PlainTextResponse:
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")


@router.get("/api/ai/context")
def ai_context() -> PlainTextResponse:
    """The llms.txt-style surface map. Static, safe to serve without a project."""
    return _md(surface_context_markdown())


@router.get("/api/projects/{project_id}/ai/context")
def project_context(db: Session = Depends(get_db), project: Project = Depends(get_project)) -> PlainTextResponse:
    """A one-read Markdown briefing on this project's live state."""
    return _md(project_context_markdown(db, project))
