"""P1-4: structured logging, the request-id middleware, and the optional SIEM
stdout stream over the existing hash-chained audit ledger."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from galeqea.config import settings
from galeqea.core import audit
from galeqea.main import app


def test_every_response_carries_a_request_id():
    c = TestClient(app)
    r = c.get("/api/health")
    assert r.headers.get("x-request-id")


def test_upstream_request_id_is_honoured():
    c = TestClient(app)
    r = c.get("/api/health", headers={"x-request-id": "trace-xyz"})
    assert r.headers["x-request-id"] == "trace-xyz"


def test_log_format_selection(monkeypatch):
    from galeqea.core import logsetup

    monkeypatch.setattr(settings, "log_format", "json")
    assert logsetup._use_json() is True
    monkeypatch.setattr(settings, "log_format", "console")
    assert logsetup._use_json() is False


def test_siem_stream_emits_one_json_line_per_audit_entry(db, project, monkeypatch, capsys):
    monkeypatch.setattr(settings, "audit_siem", True)
    capsys.readouterr()  # clear
    ev = audit.record(db, action="plan.approve", actor_kind="human",
                      actor_label="a@corp.example", project_id=project.id,
                      resource_type="journey", resource_id="j1")
    out = capsys.readouterr().out.strip().splitlines()
    line = next(x for x in out if '"event": "audit"' in x)
    parsed = json.loads(line)
    assert parsed["action"] == "plan.approve"
    assert parsed["actor_kind"] == "human"
    assert parsed["actor"] == "a@corp.example"
    assert parsed["entry_hash"] == ev.entry_hash


def test_siem_stream_is_silent_when_disabled(db, project, monkeypatch, capsys):
    monkeypatch.setattr(settings, "audit_siem", False)
    capsys.readouterr()
    audit.record(db, action="plan.approve", actor_kind="human", project_id=project.id)
    assert '"event": "audit"' not in capsys.readouterr().out
