"""Authentication, token issuance and role checks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import timedelta

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import ApiToken, Role, User
from ..models.base import utcnow

ALGORITHM = "HS256"
TOKEN_PREFIX = "trl_"


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256. No external native dependency to install or break."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 390_000)
    return f"pbkdf2_sha256$390000${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iterations, salt_b64, hash_b64 = encoded.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt_b64), int(iterations)
        )
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# JWT sessions
# --------------------------------------------------------------------------- #
def create_access_token(user: User, *, extra_claims: dict | None = None) -> str:
    now = utcnow()
    payload = {
        "sub": user.id,
        "email": user.email,
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_ttl_minutes),
        "iss": "galeqea",
        **(extra_claims or {}),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM], issuer="galeqea")


# --------------------------------------------------------------------------- #
# Scoped API tokens (CI, MCP clients)
# --------------------------------------------------------------------------- #
def issue_api_token(
    db: Session, user: User, *, name: str, scopes: list[str], ttl_days: int | None = None
) -> tuple[ApiToken, str]:
    """Returns (record, plaintext). The plaintext is shown exactly once."""
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    record = ApiToken(
        user_id=user.id,
        name=name,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        prefix=raw[:12],
        scopes=scopes,
        expires_at=utcnow() + timedelta(days=ttl_days) if ttl_days else None,
    )
    db.add(record)
    db.flush()
    return record, raw


def resolve_api_token(db: Session, raw: str) -> tuple[User, ApiToken] | None:
    digest = hashlib.sha256(raw.encode()).hexdigest()
    rec = db.execute(
        select(ApiToken).where(ApiToken.token_hash == digest)
    ).scalar_one_or_none()
    if not rec or rec.revoked:
        return None
    if rec.expires_at and rec.expires_at < utcnow():
        return None
    user = db.get(User, rec.user_id)
    if not user or not user.is_active:
        return None
    rec.last_used_at = utcnow()
    return user, rec


# --------------------------------------------------------------------------- #
# FastAPI dependencies
# --------------------------------------------------------------------------- #
def _bootstrap_owner(db: Session) -> User:
    """Single-user desktop mode: an implicit local owner, no login screen."""
    user = db.execute(
        select(User).where(User.email == "local@galeqea.dev")
    ).scalar_one_or_none()
    if user is None:
        user = User(
            email="local@galeqea.dev",
            name="Local User",
            role=Role.OWNER,
            password_hash="",
        )
        db.add(user)
        db.commit()
    return user


async def current_user(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> User:
    if authorization:
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() == "bearer" and credential:
            if credential.startswith(TOKEN_PREFIX):
                resolved = resolve_api_token(db, credential)
                if resolved:
                    user, token = resolved
                    request.state.token_scopes = token.scopes
                    return user
            else:
                try:
                    claims = decode_access_token(credential)
                except jwt.PyJWTError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail=f"invalid session token: {exc}",
                    ) from exc
                user = db.get(User, claims["sub"])
                if user and user.is_active:
                    return user
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    if settings.single_user_mode:
        return _bootstrap_owner(db)

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")


def require_role(minimum: Role):
    async def dependency(user: User = Depends(current_user)) -> User:
        if not user.at_least(minimum):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"requires role '{minimum.value}' or above (you are '{user.role}')",
            )
        return user

    return dependency


def require_scope(scope: str):
    """Least-privilege check for API-token callers such as the remote MCP server."""

    async def dependency(request: Request, user: User = Depends(current_user)) -> User:
        scopes = getattr(request.state, "token_scopes", None)
        if scopes is None:
            return user  # session-authenticated humans are governed by role, not scope
        if scope not in scopes and "*" not in scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"token is missing required scope '{scope}'",
            )
        return user

    return dependency
