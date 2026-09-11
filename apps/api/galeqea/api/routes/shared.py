"""Public share artifacts: the unauthenticated endpoint a shared report link hits.

A share is fetchable here only while it is public and unexpired; anything else is a
404 or 403. This is the one route that serves without a session, so it is
deliberately read-only and scoped to the ``shares/`` storage prefix."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from ...services import share

router = APIRouter(prefix="/api/shared", tags=["shared"])


@router.get("/shares/{token}/{filename}")
def get_share_artifact(token: str, filename: str):
    try:
        data, content_type = share.fetch(token, filename)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(content=data, media_type=content_type.split(";")[0],
                    headers={"Content-Type": content_type})
