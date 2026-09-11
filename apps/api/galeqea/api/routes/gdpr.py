"""GDPR data-subject endpoints: export (Article 15) and erase (Article 17).

Admin-only, and every call is on the audit ledger. Erasure anonymises personal
data while keeping the hash-chained ledger verifiable.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.security import authorize
from ...db import get_db
from ...models import Role, User
from ...services import gdpr

router = APIRouter(prefix="/api/gdpr", tags=["gdpr"])


def _resolve(db: Session, identifier: str) -> User:
    user = db.get(User, identifier)
    if user is None:
        user = db.execute(
            select(User).where(func.lower(User.email) == identifier.strip().lower())
        ).scalar_one_or_none()
    if user is None:
        raise HTTPException(404, "no such user")
    return user


@router.get("/users/{identifier}/export")
def export_user(
    identifier: str,
    db: Session = Depends(get_db),
    actor: User = Depends(authorize(role=Role.ADMIN)),
):
    """Everything GaleQEA holds about a user (by id or email), as JSON."""
    return gdpr.export(db, _resolve(db, identifier))


@router.post("/users/{identifier}/erase")
def erase_user(
    identifier: str,
    db: Session = Depends(get_db),
    actor: User = Depends(authorize(role=Role.ADMIN)),
):
    """Anonymise a user's personal data (right to erasure). Audited."""
    try:
        result = gdpr.erase(db, _resolve(db, identifier), by=actor)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return result
