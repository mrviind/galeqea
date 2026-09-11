"""P1-8. GDPR export (Article 15) and erasure (Article 17). Erasure must scrub the
personal data while keeping the hash-chained audit ledger verifiable."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from galeqea.core import audit
from galeqea.models import ApiToken, AuditEvent, Role, User
from galeqea.models.base import new_id
from galeqea.services import gdpr


def _user(db, email=None, role=Role.AUTHOR):
    u = User(email=email or f"u-{new_id()[:8]}@corp.example", name="U", role=role,
             password_hash="x")
    db.add(u); db.commit()
    return u


def test_export_gathers_the_users_data(db):
    u = _user(db)
    db.add(ApiToken(user_id=u.id, name="ci", token_hash="h", prefix="trl_x", scopes=["runs:write"]))
    audit.record(db, action="demo.action", actor_id=u.id, actor_kind="human",
                 actor_label=u.email, resource_type="x", resource_id="1")
    db.commit()

    data = gdpr.export(db, u)
    assert data["user"]["email"] == u.email
    assert "password_hash" not in data["user"]
    assert data["api_tokens"] and data["api_tokens"][0]["name"] == "ci"
    assert any(e["action"] == "demo.action" for e in data["audit_events"])


def test_erase_anonymises_pii_and_keeps_the_ledger_verifiable(db):
    admin = _user(db, role=Role.ADMIN)
    victim = _user(db, email=f"pii-{new_id()[:8]}@corp.example")
    db.add(ApiToken(user_id=victim.id, name="t", token_hash="h2", prefix="trl_y", scopes=[]))
    audit.record(db, action="demo.action", actor_id=victim.id, actor_kind="human",
                 actor_label=victim.email, resource_type="x", resource_id="1")
    db.commit()
    original_email = victim.email

    assert audit.verify_chain(db).ok  # chain good before

    result = gdpr.erase(db, victim, by=admin)
    db.commit()

    db.refresh(victim)
    assert victim.email == f"erased-{victim.id}@deleted.invalid"
    assert victim.name == "" and victim.is_active is False and victim.password_hash == ""
    # tokens revoked
    assert all(t.revoked for t in db.execute(
        select(ApiToken).where(ApiToken.user_id == victim.id)).scalars())
    # the email is gone from the ledger's actor labels...
    labels = [e.actor_label for e in db.execute(
        select(AuditEvent).where(AuditEvent.actor_id == victim.id)).scalars()]
    assert original_email not in labels
    # ...and the hash chain still verifies + the erasure itself is recorded.
    assert audit.verify_chain(db).ok
    assert db.execute(select(AuditEvent).where(
        AuditEvent.action == "user.erased")).scalars().first() is not None
    assert result["tokens_revoked"] == 1


def test_cannot_erase_yourself_or_a_machine(db):
    admin = _user(db, role=Role.ADMIN)
    with pytest.raises(ValueError):
        gdpr.erase(db, admin, by=admin)  # self
    bot = User(email=f"bot-{new_id()[:8]}@x", name="bot", role=Role.AGENT,
               is_machine=True, password_hash="")
    db.add(bot); db.commit()
    with pytest.raises(ValueError):
        gdpr.erase(db, bot, by=admin)  # machine
