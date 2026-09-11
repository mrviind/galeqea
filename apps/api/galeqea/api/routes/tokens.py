"""Scoped API tokens: a user mints least-privilege tokens for CI and the MCP/CLI
clients. The plaintext is shown exactly once, at creation; only its hash is stored.
A token can never exceed its owner's role: scopes narrow, the role still caps.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.security import current_user, issue_api_token
from ...db import get_db
from ...models import ApiToken, User

router = APIRouter(prefix="/api/tokens", tags=["tokens"])

#: The scopes a token may carry. `*` is every scope (a full-access token).
VALID_SCOPES = {
    "runs:write", "projects:read", "reports:read", "approvals:decide", "*",
}


class TokenIn(BaseModel):
    name: str
    scopes: list[str] = ["projects:read"]
    ttl_days: int | None = None


def _token_dict(t: ApiToken) -> dict:
    return {
        "id": t.id, "name": t.name, "prefix": t.prefix, "scopes": t.scopes,
        "expires_at": t.expires_at.isoformat() if t.expires_at else None,
        "revoked": t.revoked,
        "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


@router.get("")
def list_tokens(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(ApiToken).where(ApiToken.user_id == user.id)
        .order_by(ApiToken.created_at.desc())
    ).scalars()
    return [_token_dict(t) for t in rows]


@router.post("", status_code=201)
def create_token(
    payload: TokenIn, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    if not payload.name.strip():
        raise HTTPException(400, "a token needs a name")
    bad = sorted(s for s in payload.scopes if s not in VALID_SCOPES)
    if bad:
        raise HTTPException(400, f"unknown scope(s): {', '.join(bad)}")
    record, raw = issue_api_token(
        db, user, name=payload.name.strip(), scopes=payload.scopes, ttl_days=payload.ttl_days
    )
    db.commit()
    db.refresh(record)
    # The plaintext is returned once and never stored, so copy it now.
    return {**_token_dict(record), "token": raw}


@router.delete("/{token_id}")
def revoke_token(
    token_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    record = db.get(ApiToken, token_id)
    if record is None or record.user_id != user.id:
        raise HTTPException(404, "token not found")
    record.revoked = True
    db.commit()
    return {"ok": True}
