"""WO#6 6-A. Defects from failures: idempotent filing, evidence, recurrence,
status refresh, gate, and the readiness "open blockers" discount.

Jira is exercised through recorded HTTP fixtures (an httpx MockTransport), so no
live Atlassian tenant is needed and the request shapes are asserted directly.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import select

import galeqea.ai.toolset  # noqa: F401  (registers file_defect + its applier)
from galeqea.ai.tools import ToolContext, registry
from galeqea.core import approvals
from galeqea.core.vault import seal
from galeqea.integrations import git as git_tracker
from galeqea.integrations import jira as jira_tracker
from galeqea.models import (
    Artifact,
    DefectLink,
    DefectMap,
    IntegrationConnection,
    Run,
    RunStepRecord,
    RunTest,
    VaultSecret,
)
from galeqea.services import defects


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _connect_jira(db, project):
    secret = VaultSecret(project_id=project.id, name="jira.api_token", kind="jira")
    db.add(secret); db.flush()
    secret.ciphertext = seal("s3cr3t-token", aad=f"{project.id}:jira.api_token")
    conn = IntegrationConnection(
        project_id=project.id, provider="jira", name="Jira", enabled=True,
        config={"base_url": "https://acme.atlassian.net", "email": "qa@acme.io",
                "project_key": "APP"},
        secret_refs={"api_token": secret.id})
    db.add(conn); db.commit()
    return conn


class _Recorder:
    def __init__(self):
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, request.content))
        path = request.url.path
        if request.method == "POST" and path == "/rest/api/3/issue":
            return httpx.Response(201, json={"key": "APP-101", "id": "1001"})
        if path.endswith("/attachments"):
            return httpx.Response(200, json=[{"id": "att1"}])
        if path.endswith("/comment"):
            return httpx.Response(201, json={"id": "c1"})
        if request.method == "GET" and "/rest/api/3/issue/" in path:
            return httpx.Response(200, json={"fields": {"status": {
                "name": "Done", "statusCategory": {"key": "done"}}}})
        return httpx.Response(404, json={"errorMessages": ["unexpected"]})


@pytest.fixture()
def jira_mock(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(jira_tracker, "http_client",
                        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(rec.handler)))
    return rec


def _maps(db, project):
    return db.execute(select(DefectMap).where(DefectMap.project_id == project.id)).scalars().all()


def _links(db, project):
    return db.execute(select(DefectLink).where(DefectLink.project_id == project.id)).scalars().all()


def _failing_result(db, project, *, key="APP-T-1", sig="fp-abc", msg="Timeout waiting for #pay"):
    run = Run(project_id=project.id, number=1, status="failed", environment="staging",
              base_url="https://staging.acme.io", git_sha="abc1234def", browsers=["chromium"])
    db.add(run); db.flush()
    rt = RunTest(run_id=run.id, test_case_id="tc1", test_key=key, title="checkout pays",
                 status="failed", browser="chromium", error_type="TimeoutError",
                 error_message=msg, failure_signature=sig, duration_ms=1200)
    db.add(rt); db.flush()
    db.add(RunStepRecord(run_test_id=rt.id, index=0, action="click", intent="click Pay",
                         status="failed"))
    db.commit()
    return run, rt


# --------------------------------------------------------------------------- #
# Filing + dedupe
# --------------------------------------------------------------------------- #
def test_file_defect_creates_issue_maps_and_links(db, project, jira_mock, tmp_path, humans):
    _connect_jira(db, project)
    _run, rt = _failing_result(db, project)
    # one screenshot on disk → attached
    shot = tmp_path / "fail.png"; shot.write_bytes(b"\x89PNG\r\n\x1a\n")
    db.add(Artifact(run_id=rt.run_id, run_test_id=rt.id, kind="screenshot",
                    path=str(shot), meta={"backend": "local"}))
    db.commit()

    out = defects.file_defect(db, project, run_test_id=rt.id, actor=humans["approver"])
    db.commit()

    assert out["recurrence"] is False and out["key"] == "APP-101"
    assert len(_maps(db, project)) == 1
    links = _links(db, project)
    assert links and links[0].key == "APP-101"
    # the issue was created AND the screenshot attached
    paths = [p for (m, p, _b) in jira_mock.calls]
    assert "/rest/api/3/issue" in paths
    assert any(p.endswith("/attachments") for p in paths)


def test_recurrence_comments_and_bumps_not_duplicates(db, project, jira_mock, humans):
    _connect_jira(db, project)
    _run, rt = _failing_result(db, project)
    first = defects.file_defect(db, project, run_test_id=rt.id, actor=humans["approver"])
    db.commit()
    assert first["recurrence"] is False

    # a second failing result with the SAME fingerprint (a later run)
    run2 = Run(project_id=project.id, number=2, status="failed"); db.add(run2); db.flush()
    rt2 = RunTest(run_id=run2.id, test_case_id="tc1", test_key="APP-T-1", status="failed",
                  error_type="TimeoutError", error_message="Timeout waiting for #pay",
                  failure_signature="fp-abc")
    db.add(rt2); db.commit()

    second = defects.file_defect(db, project, run_test_id=rt2.id, actor=humans["approver"])
    db.commit()
    assert second["recurrence"] is True and second["reopened_count"] == 1
    assert second["key"] == "APP-101"
    # exactly one create, at least one comment
    creates = [1 for (m, p, _b) in jira_mock.calls if m == "POST" and p == "/rest/api/3/issue"]
    comments = [1 for (m, p, _b) in jira_mock.calls if p.endswith("/comment")]
    assert sum(creates) == 1 and sum(comments) >= 1
    assert len(_maps(db, project)) == 1  # one issue, not a duplicate


def test_refresh_status_marks_resolved_and_syncs_links(db, project, jira_mock, humans):
    _connect_jira(db, project)
    _run, rt = _failing_result(db, project)
    defects.file_defect(db, project, run_test_id=rt.id, actor=humans["approver"])
    db.commit()
    dm = _maps(db, project)[0]

    defects.refresh_status(db, project, dm)
    db.commit()
    assert dm.resolved is True and dm.status_cached == "Done"
    assert _links(db, project)[0].status_cached == "Done"


# --------------------------------------------------------------------------- #
# Readiness: a fixed (resolved) defect stops being an open blocker
# --------------------------------------------------------------------------- #
def test_open_blockers_discounts_resolved_defect(db, project, jira_mock, humans):
    from galeqea.services import release, release_metrics
    _connect_jira(db, project)
    _run, rt = _failing_result(db, project)

    m = release.create_milestone(db, project, name="R", version="1.0")
    plan, _ = release.create_plan(db, project, milestone=m, name="Reg",
                                  selection={"query": {}}, configurations=[{"browser": "chromium"}])
    cycle = release.start_cycles(db, plan)[0]
    # attach the failing run to the cycle so metrics see it
    rt.run.cycle_id = cycle.id; rt.run.milestone_id = m.id
    db.commit()

    before = release_metrics.compute(db, m)["open_blockers"]
    defects.file_defect(db, project, run_test_id=rt.id, actor=humans["approver"])
    dm = _maps(db, project)[0]
    defects.refresh_status(db, project, dm)  # → Done/resolved
    db.commit()
    after = release_metrics.compute(db, m)["open_blockers"]
    assert before >= 1 and after == before - 1


# --------------------------------------------------------------------------- #
# Gate + chat + HTTP parity
# --------------------------------------------------------------------------- #
def test_file_defect_tool_is_gated_then_applies(db, project, jira_mock, humans):
    _connect_jira(db, project)
    _run, rt = _failing_result(db, project)
    ctx = ToolContext(db=db, project_id=project.id, user=humans["agent"], actor_kind="agent")
    res = asyncio.run(registry.invoke("file_defect", {"run_test_id": rt.id}, ctx))
    assert res["status"] == "awaiting_approval"

    approvals.approve(db, res["approval_id"], humans["approver"])
    db.commit()
    assert len(_maps(db, project)) == 1


def test_defect_chat_verb_files_an_approval(db, project, jira_mock, humans):
    from galeqea.services import defect_chat
    _connect_jira(db, project)
    _run, rt = _failing_result(db, project)
    text, blocks = defect_chat.try_handle(db, project, "file a bug for run #1 APP-T-1",
                                          humans["author"])
    assert "approval" in text.lower()
    assert blocks and blocks[0]["action"] == "defect.create"
    # nothing filed yet for this project; it is only queued
    assert not _maps(db, project)


def test_http_propose_defect_returns_pending(db, project, jira_mock):
    from fastapi.testclient import TestClient

    from galeqea.main import app
    _connect_jira(db, project)
    _run, rt = _failing_result(db, project)
    client = TestClient(app)
    r = client.post(f"/api/projects/{project.id}/results/{rt.id}/defect")
    assert r.status_code == 202
    assert r.json()["status"] == "awaiting_approval" and r.json()["provider"] == "jira"


def test_http_propose_defect_without_tracker_is_409(db, project):
    from fastapi.testclient import TestClient

    from galeqea.main import app
    _run, rt = _failing_result(db, project)
    client = TestClient(app)
    r = client.post(f"/api/projects/{project.id}/results/{rt.id}/defect")
    assert r.status_code == 409


def test_github_tracker_body_is_markdown(db, project, monkeypatch, humans):
    """The secondary tracker path builds a Markdown issue (no ADF)."""
    rec = _Recorder()

    def gh_handler(request: httpx.Request) -> httpx.Response:
        rec.calls.append((request.method, str(request.url), request.content))
        if request.url.path.endswith("/issues"):
            return httpx.Response(201, json={"number": 7, "id": 700,
                                             "html_url": "https://github.com/x/y/issues/7"})
        return httpx.Response(404)

    monkeypatch.setattr(git_tracker, "http_client",
                        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(gh_handler)))
    secret = VaultSecret(project_id=project.id, name="github.token", kind="github")
    db.add(secret); db.flush()
    secret.ciphertext = seal("ghp_x", aad=f"{project.id}:github.token")
    db.add(IntegrationConnection(project_id=project.id, provider="github", enabled=True,
                                 config={"repo": "x/y"}, secret_refs={"token": secret.id}))
    _run, rt = _failing_result(db, project, key="APP-T-9", sig="fp-gh")
    db.commit()

    out = defects.file_defect(db, project, run_test_id=rt.id, actor=humans["approver"])
    db.commit()
    assert out["provider"] == "github" and out["key"] == "7"
    body = rec.calls[-1][2].decode()
    assert "What failed" in body and "```" in body  # markdown, not ADF json
