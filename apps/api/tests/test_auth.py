"""P1-3. The multi-user authentication surface: login/logout/me, session cookie +
CSRF, login lockout, the 401 gate, viewer read-only, and API-token scope gates."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from galeqea.config import settings
from galeqea.core import security
from galeqea.core.security import hash_password, issue_api_token
from galeqea.main import app
from galeqea.models import Role, User
from galeqea.models.base import new_id


@pytest.fixture()
def multiuser(monkeypatch):
    # A shared deployment: login required, and don't let slowapi's per-IP ceiling
    # (a separate concern from account lockout) make the suite flaky.
    from galeqea.core.ratelimit import limiter

    monkeypatch.setattr(settings, "single_user_mode", False)
    monkeypatch.setattr(limiter, "enabled", False)
    yield


def _mkuser(db, role=Role.AUTHOR, password="Correct-Horse-9", active=True, machine=False):
    email = f"u-{new_id()[:8]}@corp.example"
    u = User(email=email, name="U", role=role, is_active=active, is_machine=machine,
             password_hash=hash_password(password) if password else "")
    db.add(u); db.commit()
    return u, email, password


def _client():
    return TestClient(app)


# --- the 401 gate ---------------------------------------------------------- #

def test_protected_api_is_401_without_a_session(multiuser, db, project):
    r = _client().get(f"/api/projects/{project.id}/runs")
    assert r.status_code == 401


def test_public_endpoints_bypass_the_gate(multiuser):
    c = _client()
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/auth/config").status_code == 200


# --- login / me / logout --------------------------------------------------- #

def test_login_sets_cookies_and_me_returns_the_user(multiuser, db):
    user, email, pw = _mkuser(db)
    c = _client()
    r = c.post("/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["email"] == email
    assert r.json()["csrf_token"]
    # cookie flags: session is HttpOnly, csrf is readable, both SameSite=Lax
    set_cookie = " ".join(r.headers.get_list("set-cookie"))
    assert "galeqea_session=" in set_cookie and "HttpOnly" in set_cookie
    assert "galeqea_csrf=" in set_cookie and "samesite=lax" in set_cookie.lower()

    me = c.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["email"] == email

    out = c.post("/api/auth/logout", headers={"x-csrf-token": c.cookies.get("galeqea_csrf")})
    assert out.status_code == 200
    # After logout the session cookie is cleared → /me is 401 again.
    c.cookies.clear()
    assert c.get("/api/auth/me").status_code == 401


def test_login_rejects_a_bad_password_and_the_machine_agent(multiuser, db):
    user, email, pw = _mkuser(db)
    c = _client()
    assert c.post("/api/auth/login", json={"email": email, "password": "wrong"}).status_code == 401
    agent, aemail, apw = _mkuser(db, role=Role.AGENT, machine=True)
    assert c.post("/api/auth/login", json={"email": aemail, "password": apw}).status_code == 401


def test_account_locks_out_after_repeated_failures(multiuser, db):
    user, email, pw = _mkuser(db)
    security.clear_login_failures(f"login:{email}")
    c = _client()
    for _ in range(5):
        assert c.post("/api/auth/login", json={"email": email, "password": "nope"}).status_code == 401
    # 6th attempt is refused as locked, even with the CORRECT password.
    locked = c.post("/api/auth/login", json={"email": email, "password": pw})
    assert locked.status_code == 429
    security.clear_login_failures(f"login:{email}")


# --- CSRF ------------------------------------------------------------------ #

def test_cookie_mutation_needs_a_csrf_token(multiuser, db, project):
    user, email, pw = _mkuser(db, role=Role.AUTHOR)
    c = _client()
    c.post("/api/auth/login", json={"email": email, "password": pw})
    # A cookie-authenticated POST with no CSRF header is refused.
    r = c.post(f"/api/projects/{project.id}/runs", json={"selection": {}})
    assert r.status_code == 403 and "csrf" in r.json()["detail"].lower()
    # With the double-submit header it clears the CSRF gate (any later error is not 403/401).
    r2 = c.post(f"/api/projects/{project.id}/runs", json={"selection": {}},
                headers={"x-csrf-token": c.cookies.get("galeqea_csrf")})
    assert r2.status_code not in (401, 403)


# --- role gate ------------------------------------------------------------- #

def test_viewer_is_read_only(multiuser, db, project):
    viewer, email, pw = _mkuser(db, role=Role.VIEWER)
    c = _client()
    c.post("/api/auth/login", json={"email": email, "password": pw})
    r = c.post(f"/api/projects/{project.id}/runs", json={"selection": {}},
               headers={"x-csrf-token": c.cookies.get("galeqea_csrf")})
    assert r.status_code == 403


# --- API-token scope gate -------------------------------------------------- #

def test_api_token_needs_the_right_scope(multiuser, db, project):
    author, email, pw = _mkuser(db, role=Role.AUTHOR)
    _, weak = issue_api_token(db, author, name="weak", scopes=["projects:read"])
    _, strong = issue_api_token(db, author, name="strong", scopes=["runs:write"])
    db.commit()
    c = _client()
    # Bearer tokens are CSRF-exempt; the scope is what gates them.
    denied = c.post(f"/api/projects/{project.id}/runs", json={"selection": {}},
                    headers={"authorization": f"Bearer {weak}"})
    assert denied.status_code == 403 and "scope" in denied.json()["detail"].lower()
    allowed = c.post(f"/api/projects/{project.id}/runs", json={"selection": {}},
                     headers={"authorization": f"Bearer {strong}"})
    assert allowed.status_code not in (401, 403)


# --- single-user desktop bypass ------------------------------------------- #

def test_single_user_mode_needs_no_login(db, project, monkeypatch):
    monkeypatch.setattr(settings, "single_user_mode", True)
    r = _client().get(f"/api/projects/{project.id}/runs")
    assert r.status_code == 200
