"""Access: credentials and captured sessions are sealed, reused, and forgettable,
and the plaintext never touches a persisted surface."""

from __future__ import annotations

import json

from galeqea.core import vault
from galeqea.services import access, journeys

_PW = "SuperSecretPassword!"


def _journey(db, project):
    j = journeys.start(db, project.id, "https://the-internet.herokuapp.com")
    j.discovery = {
        "base": "https://the-internet.herokuapp.com",
        "pages": ["https://the-internet.herokuapp.com/"],
        "findings": [{"kind": "auth_gated", "url": "https://the-internet.herokuapp.com/login"},
                     {"kind": "auth_gated", "url": "https://the-internet.herokuapp.com/basic_auth"}],
        "skipped": {"auth": 2},
    }
    db.flush()
    return j


def test_store_credentials_seals_and_masks(db, project):
    j = _journey(db, project)
    result = access.store_credentials(db, j, username="tomsmith", password=_PW,
                                      login_url="https://the-internet.herokuapp.com/login")
    # The return value carries only a masked hint, never the password.
    assert result["username_hint"] == "t***h"
    assert _PW not in json.dumps(result)
    # The persisted journey row holds no plaintext, only the sealed envelope.
    row = json.dumps(j.guardrails)
    assert _PW not in row and "tomsmith" not in row
    entry = j.guardrails["auth"]["credentials"][0]
    assert entry["creds_sealed"] and entry["kind"] == "form"
    # ...and it round-trips through the vault server-side.
    assert access._creds_of(entry, project.id) == {"username": "tomsmith", "password": _PW}


def test_detected_kinds_and_inferred_default(db, project):
    j = _journey(db, project)
    base = "https://the-internet.herokuapp.com"
    j.discovery = {**j.discovery, "findings": [
        {"kind": "auth_gated", "url": f"{base}/login", "auth_kind": "form"},
        {"kind": "auth_gated", "url": f"{base}/basic_auth", "auth_kind": "basic"},
        {"kind": "auth_gated", "url": f"{base}/digest_auth", "auth_kind": "digest"},
    ]}
    db.flush()
    assert access.detected_kinds(j) == ["form", "basic", "digest"]
    # store without an explicit kind → inferred from discovery (first detected).
    result = access.store_credentials(db, j, username="u", password="p")
    assert result["kind"] == "form"
    # A second, different-kind credential coexists: a list, not a single slot.
    access.store_credentials(db, j, username="admin", password="admin", kind="digest")
    assert access.covered_kinds(j) == {"form", "digest"}
    assert access.credential_for(j, "digest") is not None
    assert access.run_auth(j)["requirements"] == {"/login": "form", "/basic_auth": "basic",
                                                  "/digest_auth": "digest"}


def test_forget_wipes_credentials_and_session(db, project):
    j = _journey(db, project)
    access.store_credentials(db, j, username="tomsmith", password=_PW)
    assert access.has_credentials(j) is True
    assert access.forget(db, j) is True
    assert access.has_credentials(j) is False
    assert "auth" not in (j.guardrails or {})


def test_run_auth_returns_the_credential_bundle(db, project):
    j = _journey(db, project)
    access.store_credentials(db, j, username="admin", password="admin", kind="basic")
    ra = access.run_auth(j)
    assert ra["credentials"][0]["kind"] == "basic"
    assert "requirements" in ra
    assert access.credential_for(j, "basic") is not None
    # A form and a basic credential coexist: a list, one per kind/scope.
    access.store_credentials(db, j, username="tomsmith", password=_PW, kind="form")
    assert access.covered_kinds(j) == {"basic", "form"}
    assert len(access.run_auth(j)["credentials"]) == 2


def test_promote_gated_moves_findings_into_pages(db, project):
    j = _journey(db, project)
    promoted = access.promote_gated(db, j)
    assert set(promoted) == {"https://the-internet.herokuapp.com/login",
                             "https://the-internet.herokuapp.com/basic_auth"}
    assert j.discovery["skipped"]["auth"] == 0
    assert all(u in j.discovery["pages"] for u in promoted)
    assert not [f for f in j.discovery["findings"] if f.get("kind") == "auth_gated"]


def test_landing_page_is_added_but_never_off_site(db, project):
    j = _journey(db, project)
    same = access._add_landing_page(db, j, "https://the-internet.herokuapp.com/secure")
    assert same == "https://the-internet.herokuapp.com/secure"
    assert "https://the-internet.herokuapp.com/secure" in j.discovery["pages"]
    off = access._add_landing_page(db, j, "https://evil.example.com/secure")
    assert off is None  # a login must never drag the tested set off-site


def test_log_in_for_me_seals_the_resumed_session_onto_the_journey(db, project):
    """The headed handoff seals the captured session on the run (supervisor); the
    journey then adopts that sealed session for reuse; no plaintext in between."""
    from galeqea.models import Run

    j = _journey(db, project)
    sealed = vault.seal(json.dumps({"cookies": [{"name": "sess", "value": "SESS_VALUE_SECRET"}]}),
                        aad=f"auth:{project.id}")
    run = Run(project_id=project.id, number=1, title="Log-in-for-me",
              ci_metadata={"auth_sealed": sealed})
    db.add(run)
    db.flush()

    assert access.seal_handoff_session(db, j, run) is True
    entry = j.guardrails["auth"]["credentials"][0]
    assert entry["kind"] == "form" and entry["session_sealed"] == sealed
    assert access.covered_kinds(j) == {"form"}
    assert "SESS_VALUE_SECRET" not in json.dumps(j.guardrails)  # sealed, not stored raw
    # A run with nothing captured is a no-op, not a crash.
    assert access.seal_handoff_session(db, j, Run(project_id=project.id, number=2)) is False


def test_a_sealed_session_never_stores_cookie_values_in_the_clear(db, project):
    j = _journey(db, project)
    access.store_credentials(db, j, username="tomsmith", password=_PW, kind="form")
    bundle = dict(j.guardrails["auth"])
    bundle["credentials"][0]["session_sealed"] = vault.seal(
        json.dumps({"cookies": [{"name": "rack.session", "value": "TOP_SECRET_COOKIE"}]}),
        aad=f"auth:{project.id}")
    j.guardrails = {**j.guardrails, "auth": bundle}
    db.flush()
    assert "TOP_SECRET_COOKIE" not in json.dumps(j.guardrails)  # sealed, not plaintext
