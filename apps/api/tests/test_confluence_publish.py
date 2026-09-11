"""WO#6 6-D. Publish a release report to Confluence: storage-format body, find-or-
create, update-in-place (version+1) on re-publish, label, gate, and parity.
Confluence via recorded HTTP fixtures (httpx MockTransport).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import select

import galeqea.ai.toolset  # noqa: F401  (registers publish_release_report + applier)
from galeqea.ai.tools import ToolContext, registry
from galeqea.core import approvals
from galeqea.core.vault import seal
from galeqea.integrations import confluence as confluence_be
from galeqea.models import IntegrationConnection, ReportPage, VaultSecret
from galeqea.reports import release_report
from galeqea.services import confluence_publish, release


def _connect(db, project, *, space="QA"):
    s = VaultSecret(project_id=project.id, name="confluence.api_token", kind="confluence")
    db.add(s)
    db.flush()
    s.ciphertext = seal("tok", aad=f"{project.id}:confluence.api_token")
    db.add(IntegrationConnection(
        project_id=project.id, provider="confluence", enabled=True,
        config={"base_url": "https://acme.atlassian.net", "email": "qa@acme.io",
                "space_key": space, "parent_id": "999"},
        secret_refs={"api_token": s.id}))
    db.commit()


def _milestone(db, project, version="1.4"):
    m = release.create_milestone(db, project, name="R", version=version)
    release.set_exit_criteria(db, m, [{"metric": "pass_rate", "op": ">=", "value": 0.95}])
    db.commit()
    return m


class _Confluence:
    def __init__(self, *, existing_page=None):
        self.existing = existing_page       # {"id","version"} or None
        self.calls = []
        self.put_versions = []

    def handler(self, request):
        path = request.url.path
        self.calls.append((request.method, path))
        if path.endswith("/api/v2/spaces"):
            return httpx.Response(200, json={"results": [{"id": "100", "key": "QA", "name": "QA"}]})
        if path.endswith("/api/v2/pages") and request.method == "GET":
            results = ([{"id": self.existing["id"], "version": {"number": self.existing["version"]}}]
                       if self.existing else [])
            return httpx.Response(200, json={"results": results})
        if path.endswith("/api/v2/pages") and request.method == "POST":
            return httpx.Response(200, json={"id": "P1", "version": {"number": 1},
                                             "_links": {"webui": "/spaces/QA/pages/P1"}})
        if "/api/v2/pages/" in path and request.method == "PUT":
            import json as _j
            v = _j.loads(request.content.decode())["version"]["number"]
            self.put_versions.append(v)
            return httpx.Response(200, json={"id": path.rsplit("/", 1)[-1],
                                             "version": {"number": v},
                                             "_links": {"webui": "/spaces/QA/pages/P1"}})
        if "/api/v2/pages/" in path and request.method == "GET":
            return httpx.Response(200, json={"version": {"number": self.existing["version"]
                                                         if self.existing else 1}})
        if "/label" in path:
            return httpx.Response(200, json={})
        return httpx.Response(404, json={})


@pytest.fixture()
def confluence_of(monkeypatch):
    def _install(fake):
        monkeypatch.setattr(confluence_be, "http_client",
                            lambda *a, **k: httpx.Client(transport=httpx.MockTransport(fake.handler)))
        return fake
    return _install


# --------------------------------------------------------------------------- #
def test_storage_body_has_status_macro_and_tables(db, project):
    m = _milestone(db, project)
    body = release_report.to_confluence_storage(db, m)
    assert "<h1>Release 1.4" in body
    assert 'ac:name="status"' in body           # Go/No-Go status macro
    assert "<table>" in body and "Exit criteria" in body


def test_first_publish_creates_page_and_records_it(db, project, confluence_of, humans):
    _connect(db, project)
    m = _milestone(db, project)
    fake = confluence_of(_Confluence(existing_page=None))

    out = confluence_publish.publish_release_report(db, project, version="1.4",
                                                    actor=humans["author"])
    db.commit()
    assert out["page_id"] == "P1" and out["page_version"] == 1
    assert any(m_ == "POST" and p.endswith("/api/v2/pages") for (m_, p) in fake.calls)
    row = db.execute(select(ReportPage).where(ReportPage.project_id == project.id)).scalars().first()
    assert row and row.page_id == "P1" and row.url.endswith("/spaces/QA/pages/P1")


def test_republish_updates_in_place_version_plus_one(db, project, confluence_of, humans):
    _connect(db, project)
    m = _milestone(db, project)
    # a page already exists at version 3
    fake = confluence_of(_Confluence(existing_page={"id": "P1", "version": 3}))
    # seed the ReportPage so the service knows it
    db.add(ReportPage(project_id=project.id, milestone_id=m.id, space_key="QA",
                      page_id="P1", version=3, title="GaleQEA · Release 1.4"))
    db.commit()

    out = confluence_publish.publish_release_report(db, project, version="1.4",
                                                    actor=humans["author"])
    db.commit()
    assert out["page_version"] == 4                 # 3 → 4, in place
    assert fake.put_versions == [4]                 # a PUT, not a second POST
    assert not any(m_ == "POST" and p.endswith("/api/v2/pages") for (m_, p) in fake.calls)


def test_publish_tool_is_gated_then_applies(db, project, confluence_of, humans):
    _connect(db, project)
    _milestone(db, project)
    confluence_of(_Confluence(existing_page=None))
    ctx = ToolContext(db=db, project_id=project.id, user=humans["agent"], actor_kind="agent")
    res = asyncio.run(registry.invoke("publish_release_report",
                                      {"version": "1.4", "space_key": "QA"}, ctx))
    assert res["status"] == "awaiting_approval"
    approvals.approve(db, res["approval_id"], humans["approver"])
    db.commit()
    assert db.execute(select(ReportPage).where(ReportPage.project_id == project.id)).scalars().first()


def test_chat_publish_files_approval(db, project, humans):
    from galeqea.services import release_chat
    _milestone(db, project)
    text, blocks = release_chat.try_handle(
        db, project, "publish release report 1.4 to confluence space QA", humans["author"])
    assert blocks and blocks[0]["action"] == "report.publish"
    assert "approval" in text.lower()


def test_http_publish_returns_pending(db, project):
    from fastapi.testclient import TestClient

    from galeqea.main import app
    m = _milestone(db, project)
    client = TestClient(app)
    r = client.post(f"/api/projects/{project.id}/milestones/{m.id}/publish",
                    json={"space_key": "QA"})
    assert r.status_code == 202 and r.json()["status"] == "awaiting_approval"
