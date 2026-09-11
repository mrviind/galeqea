"""Authentication, token issuance and role checks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import timedelta

import jwt
from argon2 import PasswordHasher as Argon2Hasher
from argon2 import Type as Argon2Type
from fastapi import Cookie, Depends, Header, HTTPException, Request, status
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
# argon2id for new hashes (a memory-hard KDF, the current best practice). Older
# installs carry the previous custom PBKDF2 hashes; those still verify and are
# transparently upgraded to argon2 on the owner's next successful login.
_argon2 = Argon2Hasher(type=Argon2Type.ID)


def hash_password(password: str) -> str:
    """argon2id."""
    return _argon2.hash(password)


def _verify_legacy_pbkdf2(password: str, encoded: str) -> bool:
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


def verify_password(password: str, encoded: str) -> bool:
    if not encoded:
        return False
    if encoded.startswith("$argon2"):
        try:
            return _argon2.verify(encoded, password)
        except Exception:  # noqa: BLE001 - any argon2 error means "no match"
            return False
    return _verify_legacy_pbkdf2(password, encoded)


def needs_rehash(encoded: str) -> bool:
    """True when a stored hash should be re-computed with the current scheme: a
    legacy (non-argon2) hash, or argon2 parameters that have since been raised."""
    if not encoded.startswith("$argon2"):
        return True
    try:
        return _argon2.check_needs_rehash(encoded)
    except Exception:  # noqa: BLE001
        return True


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
# Browser sessions: an HttpOnly cookie carrying the JWT, plus a readable CSRF
# cookie for the double-submit check. API-token (bearer) callers use neither.
# --------------------------------------------------------------------------- #
SESSION_COOKIE = "galeqea_session"
CSRF_COOKIE = "galeqea_csrf"
CSRF_HEADER = "x-csrf-token"


def _cookie_secure(request: Request) -> bool:
    # Secure over HTTPS automatically; a plain-HTTP localhost dev session would
    # never receive a Secure cookie, so don't force it there. An operator behind a
    # TLS-terminating proxy can pin it on with GALEQEA_SESSION_COOKIE_SECURE=true.
    return request.url.scheme == "https" or bool(getattr(settings, "session_cookie_secure", False))


def set_session_cookies(response, user: User, request: Request) -> str:
    """Set the session (HttpOnly) and CSRF (readable) cookies. Returns the CSRF
    token so the caller can also hand it back in the login response body."""
    secure = _cookie_secure(request)
    max_age = settings.jwt_ttl_minutes * 60
    response.set_cookie(
        SESSION_COOKIE, create_access_token(user),
        max_age=max_age, httponly=True, secure=secure, samesite="lax", path="/",
    )
    csrf = secrets.token_urlsafe(32)
    response.set_cookie(
        CSRF_COOKIE, csrf,
        max_age=max_age, httponly=False, secure=secure, samesite="lax", path="/",
    )
    return csrf


def clear_session_cookies(response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


# --------------------------------------------------------------------------- #
# Login throttling: a per-identifier failure counter with a lockout window, so a
# password can't be brute-forced. In-memory: a single web process is the default;
# slowapi adds a per-IP request ceiling on top.
# --------------------------------------------------------------------------- #
_LOCKOUT_THRESHOLD = 5
_LOCKOUT_WINDOW_S = 15 * 60
_login_failures: dict[str, list[float]] = {}


def _now() -> float:
    import time
    return time.monotonic()


def is_locked(key: str) -> bool:
    hits = [t for t in _login_failures.get(key, []) if _now() - t < _LOCKOUT_WINDOW_S]
    _login_failures[key] = hits
    return len(hits) >= _LOCKOUT_THRESHOLD


def record_login_failure(key: str) -> int:
    hits = [t for t in _login_failures.get(key, []) if _now() - t < _LOCKOUT_WINDOW_S]
    hits.append(_now())
    _login_failures[key] = hits
    return max(0, _LOCKOUT_THRESHOLD - len(hits))


def clear_login_failures(key: str) -> None:
    _login_failures.pop(key, None)


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
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> User:
    # 1. Bearer credential (API token or a JWT passed explicitly).
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

    # 2. Single-user desktop install: an implicit local owner, no login screen.
    # This comes *before* the cookie so a stale session cookie can never lock a
    # desktop user out of their own machine.
    if settings.single_user_mode:
        return _bootstrap_owner(db)

    # 3. Browser session cookie (the multi-user login flow).
    if session:
        try:
            claims = decode_access_token(session)
        except jwt.PyJWTError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="session expired, sign in again",
            ) from exc
        user = db.get(User, claims["sub"])
        if user and user.is_active:
            return user
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session")

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


def authorize(*, role: Role | None = None, scope: str | None = None):
    """Combined route guard. ``role`` caps every caller (session or token) by the
    user's own rank; ``scope`` additionally narrows an API-token caller (a session
    human has no scopes and is governed by role alone). Requiring ``approver`` or
    above also structurally excludes the machine agent (rank -1)."""

    async def dependency(request: Request, user: User = Depends(current_user)) -> User:
        if role is not None and not user.at_least(role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"requires role '{role.value}' or above (you are '{user.role}')",
            )
        if scope is not None:
            scopes = getattr(request.state, "token_scopes", None)
            if scopes is not None and scope not in scopes and "*" not in scopes:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"token is missing required scope '{scope}'",
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
