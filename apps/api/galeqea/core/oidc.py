"""OpenID Connect single sign-on (optional).

Any OIDC provider works: configure the issuer + client id/secret and GaleQEA
discovers the endpoints from ``/.well-known/openid-configuration``. On a successful
login the user is created just-in-time and their GaleQEA role is mapped from the
groups/roles claim; nothing is provisioned ahead of time.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import RANK, User
from ..models.base import utcnow

log = logging.getLogger("galeqea.oidc")
_oauth = None


def get_client():
    """The lazily-built Authlib OIDC client (registered from discovery)."""
    global _oauth
    if _oauth is None:
        from authlib.integrations.starlette_client import OAuth

        oauth = OAuth()
        oauth.register(
            name="oidc",
            server_metadata_url=(
                settings.auth_oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"),
            client_id=settings.auth_oidc_client_id,
            client_secret=settings.auth_oidc_client_secret,
            client_kwargs={"scope": settings.auth_oidc_scopes},
        )
        _oauth = oauth
    return _oauth.oidc


def reset_client() -> None:
    global _oauth
    _oauth = None


def role_for_claims(claims: dict) -> str:
    """Highest GaleQEA role among the user's mapped groups, else the default role."""
    groups = claims.get(settings.auth_oidc_groups_claim) or []
    if isinstance(groups, str):
        groups = [groups]
    mapped = [settings.auth_oidc_role_map[g] for g in groups
              if g in (settings.auth_oidc_role_map or {})]
    if not mapped:
        return settings.auth_oidc_default_role
    # A user in several groups gets the most-privileged mapped role.
    return max(mapped, key=lambda r: RANK.get(r, -1))


def jit_user(db: Session, claims: dict) -> User:
    """Create or update the signed-in user from the ID-token / userinfo claims."""
    email = (claims.get("email") or "").strip().lower()
    sub = claims.get("sub") or ""
    if not email and not sub:
        raise ValueError("the OIDC provider returned neither an email nor a subject")

    user = None
    if email:
        user = db.execute(
            select(User).where(func.lower(User.email) == email)
        ).scalar_one_or_none()
    role = role_for_claims(claims)
    name = claims.get("name") or claims.get("preferred_username") or email

    if user is None:
        user = User(email=email or f"{sub}@oidc.local", name=name, role=role,
                    is_active=True, password_hash="")
        db.add(user)
    else:
        # An SSO login is authoritative for the mapped role and display name; a
        # machine principal is never adoptable as a human SSO identity.
        if user.is_machine:
            raise ValueError("that identity is a machine principal")
        user.role = role
        if name:
            user.name = name
    user.last_seen_at = utcnow()
    db.flush()
    return user


def redirect_uri(request) -> str:
    """The absolute callback URL the IdP sends the browser back to."""
    base = settings.auth_oidc_redirect_base.rstrip("/") if settings.auth_oidc_redirect_base else ""
    if base:
        return f"{base}/api/auth/oidc/callback"
    return str(request.url_for("oidc_callback"))
