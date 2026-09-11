"""P1-6: OIDC role mapping and just-in-time user provisioning. The full browser
flow is verified against the Keycloak dev compose; here we cover the pure logic."""

from __future__ import annotations

from galeqea.config import settings
from galeqea.core import oidc
from galeqea.models import Role, User
from galeqea.models.base import new_id


def test_oidc_enabled_needs_issuer_and_client(monkeypatch):
    monkeypatch.setattr(settings, "auth_oidc_issuer", "")
    assert settings.oidc_enabled is False
    monkeypatch.setattr(settings, "auth_oidc_issuer", "https://idp.example/realms/x")
    monkeypatch.setattr(settings, "auth_oidc_client_id", "galeqea")
    monkeypatch.setattr(settings, "auth_oidc_client_secret", "s3cr3t")
    assert settings.oidc_enabled is True


def test_role_mapping_picks_the_highest_group(monkeypatch):
    monkeypatch.setattr(settings, "auth_oidc_groups_claim", "groups")
    monkeypatch.setattr(settings, "auth_oidc_role_map",
                        {"qa-viewers": "viewer", "qa-admins": "admin"})
    monkeypatch.setattr(settings, "auth_oidc_default_role", "author")

    assert oidc.role_for_claims({"groups": ["qa-viewers", "qa-admins"]}) == "admin"
    assert oidc.role_for_claims({"groups": "qa-viewers"}) == "viewer"
    assert oidc.role_for_claims({"groups": ["unmapped"]}) == "author"  # default
    assert oidc.role_for_claims({}) == "author"


def test_jit_creates_then_updates_the_user(db, monkeypatch):
    monkeypatch.setattr(settings, "auth_oidc_role_map", {"admins": "admin"})
    monkeypatch.setattr(settings, "auth_oidc_default_role", "author")
    email = f"sso-{new_id()[:8]}@corp.example"

    u1 = oidc.jit_user(db, {"email": email, "name": "Dana", "sub": "s1", "groups": ["admins"]})
    db.commit()
    assert u1.role == Role.ADMIN and u1.name == "Dana" and u1.password_hash == ""

    # A second login for the same email updates role + name, doesn't duplicate.
    u2 = oidc.jit_user(db, {"email": email.upper(), "name": "Dana R", "sub": "s1", "groups": []})
    db.commit()
    assert u2.id == u1.id
    assert u2.role == Role.AUTHOR  # groups changed → default role now
    assert u2.name == "Dana R"


def test_jit_refuses_a_machine_identity(db):
    email = f"bot-{new_id()[:8]}@corp.example"
    m = User(email=email, name="bot", role=Role.AGENT, is_machine=True, password_hash="")
    db.add(m); db.commit()
    import pytest
    with pytest.raises(ValueError):
        oidc.jit_user(db, {"email": email, "sub": "x"})
