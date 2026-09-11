"""Authentication: email + password sign-in for the shared (multi-user) deployment.

A successful login sets an HttpOnly session cookie (the JWT) and a readable CSRF
cookie; mutating requests then carry the CSRF token back in a header (double-submit,
enforced in ``main``). API-token (bearer) callers use none of this. Single-user
desktop installs never hit these routes; there is no login screen there.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...config import settings
from ...core.ratelimit import limiter
from ...core.security import (
    clear_login_failures,
    clear_session_cookies,
    current_user,
    hash_password,
    is_locked,
    needs_rehash,
    record_login_failure,
    set_session_cookies,
    verify_password,
)
from ...db import get_db
from ...models import User
from ...models.base import utcnow

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


def _user_dict(user: User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role}


@router.get("/config")
def auth_config():
    """Public: lets the SPA render the right login options before anyone is signed
    in. The SSO button appears only once OIDC is configured (P1-6)."""
    return {
        "password_login": True,
        "oidc_enabled": settings.oidc_enabled,
        "single_user_mode": settings.single_user_mode,
    }


@router.get("/oidc/login")
async def oidc_login(request: Request):
    """Kick off the SSO flow by redirecting the browser to the identity provider."""
    if not settings.oidc_enabled:
        raise HTTPException(404, "SSO is not configured")
    from ...core import oidc

    client = oidc.get_client()
    return await client.authorize_redirect(request, oidc.redirect_uri(request))


@router.get("/oidc/callback", name="oidc_callback")
async def oidc_callback(request: Request, db: Session = Depends(get_db)):
    """The identity provider returns here: exchange the code, JIT the user, and set
    the same session cookie the password flow uses."""
    if not settings.oidc_enabled:
        raise HTTPException(404, "SSO is not configured")
    from ...core import oidc

    client = oidc.get_client()
    try:
        token = await client.authorize_access_token(request)
    except Exception as exc:  # noqa: BLE001 - surface any exchange/verification failure
        raise HTTPException(400, f"SSO sign-in failed: {exc}") from exc
    claims = dict(token.get("userinfo") or {})
    if not claims:
        claims = dict(await client.userinfo(token=token))
    try:
        user = oidc.jit_user(db, claims)
    except ValueError as exc:
        raise HTTPException(403, str(exc)) from exc
    db.commit()
    # Land back on the SPA, now carrying a GaleQEA session.
    resp = RedirectResponse(url="/", status_code=303)
    set_session_cookies(resp, user, request)
    return resp


@router.post("/login")
@limiter.limit("10/minute")
def login(request: Request, response: Response, payload: LoginIn, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    key = f"login:{email}"
    if is_locked(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many failed attempts, this account is locked for a while",
        )

    user = db.execute(
        select(User).where(func.lower(User.email) == email)
    ).scalar_one_or_none()
    # A machine principal (the agent) can never sign in. Constant work either way:
    # verify against a real-looking hash even for an unknown email so a timing side
    # channel can't distinguish "no such user" from "wrong password".
    stored = user.password_hash if (user and user.is_active and not user.is_machine) else ""
    if not verify_password(payload.password, stored) or not user:
        record_login_failure(key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid email or password"
        )

    clear_login_failures(key)
    # Transparently upgrade a legacy (PBKDF2) hash to argon2 on a good login.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
    user.last_seen_at = utcnow()
    csrf = set_session_cookies(response, user, request)
    return {"user": _user_dict(user), "csrf_token": csrf}


@router.post("/logout")
def logout(response: Response):
    clear_session_cookies(response)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return _user_dict(user)
