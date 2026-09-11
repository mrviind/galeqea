"""WO#9-D: regenerate-with-instruction, edit few-shots, and edit-without-approve."""

from __future__ import annotations

import json

from galeqea.ai.providers.base import Completion
from galeqea.models import TestCase, TestStep
from galeqea.services import requirements as req


class _FakeProvider:
    model = "fake"

    def __init__(self, payload):
        self._payload = payload

    async def complete(self, *a, **k):
        return Completion(text=json.dumps(self._payload))


def _case(db, project):
    case = TestCase(project_id=project.id, key="DEMO-T-9001", title="verify login",
                    rationale="original rationale", status="proposed")
    db.add(case)
    db.flush()
    db.add(TestStep(test_case_id=case.id, index=0, action="click", intent="click submit"))
    db.commit()
    return case


async def test_regenerate_without_model_is_a_clear_noop(db, project):
    case = _case(db, project)
    result = await req.regenerate_case(db, project_id=project.id, case=case,
                                       instruction="make it stronger", provider=None)
    assert result["ok"] is False and "model" in result["note"].lower()


async def test_regenerate_applies_the_revision(db, project):
    case = _case(db, project)
    provider = _FakeProvider({
        "title": "verify login with MFA",
        "rationale": "stronger: covers the MFA path",
        "steps": [{"intent": "enter code", "expected": "dashboard shown"}],
    })
    result = await req.regenerate_case(db, project_id=project.id, case=case,
                                       instruction="add the MFA path", provider=provider)
    assert result["ok"] is True
    db.refresh(case)
    assert case.title == "verify login with MFA"
    assert case.provenance.get("regenerated") is True
    assert case.version == 2
    assert any(s.intent == "enter code" for s in case.steps)


def test_edit_example_records_and_recalls(db, project):
    req.record_edit_example(
        db, project_id=project.id,
        before={"key": "DEMO-T-1", "title": "check X", "rationale": "r1"},
        after={"title": "verify X thoroughly", "rationale": "r1"},
        comment="be specific about the assertion")
    db.commit()
    examples = req.edit_examples(db, project.id)
    assert examples and any("verify X thoroughly" in e for e in examples)


def test_edit_example_skips_a_noop(db, project):
    req.record_edit_example(
        db, project_id=project.id,
        before={"key": "K", "title": "same", "rationale": "same"},
        after={"title": "same", "rationale": "same"}, comment="")
    db.commit()
    assert req.edit_examples(db, project.id) == []


def _client():
    from fastapi.testclient import TestClient

    from galeqea.main import app
    return TestClient(app)


def test_save_edit_without_approving_keeps_status_and_records_example(db, project):
    case = _case(db, project)
    r = _client().post(
        f"/api/projects/{project.id}/tests/{case.id}/review",
        json={"decision": "save", "edits": {"title": "verify login (v2)"}})
    assert r.status_code == 200
    db.refresh(case)
    assert case.title == "verify login (v2)"
    assert case.status == "proposed"          # unchanged, a pure edit
    assert req.edit_examples(db, project.id)  # the edit became a few-shot


def test_regenerate_endpoint_without_model(db, project):
    case = _case(db, project)
    r = _client().post(
        f"/api/projects/{project.id}/tests/{case.id}/regenerate",
        json={"instruction": "make it stronger"})
    assert r.status_code == 200
    assert r.json()["ok"] is False
