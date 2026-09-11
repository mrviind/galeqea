"""WO#6 6-B. Jira → tests: JQL resolution, ADF→Markdown, story import (idempotent
+ stale detection), coverage write-back, the gate, and chat/HTTP parity.

Jira is exercised through recorded HTTP fixtures (httpx MockTransport), never a live
tenant. A separate GALEQEA_LIVE_JIRA opt-in (test_live_jira.py) hits the real site.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import select

import galeqea.ai.toolset  # noqa: F401  (registers import/writeback tools + appliers)
from galeqea.ai.tools import ToolContext, registry
from galeqea.core import approvals
from galeqea.core.vault import seal
from galeqea.integrations import jira as jira_tracker
from galeqea.models import (
    IntegrationConnection,
    JiraIssueMap,
    RequirementItem,
    TestCase,
    TestStatus,
    VaultSecret,
)
from galeqea.services import story_import


def _connect_jira(db, project, *, project_key="XSP"):
    secret = VaultSecret(project_id=project.id, name="jira.api_token", kind="jira")
    db.add(secret)
    db.flush()
    secret.ciphertext = seal("tok", aad=f"{project.id}:jira.api_token")
    db.add(IntegrationConnection(
        project_id=project.id, provider="jira", enabled=True,
        config={"base_url": "https://acme.atlassian.net", "email": "qa@acme.io",
                "project_key": project_key},
        secret_refs={"api_token": secret.id}))
    db.commit()


def _story(key, summary, desc_text, *, updated="2026-08-01T00:00:00.000+0000", pid="1"):
    return {"id": pid, "key": key, "fields": {
        "summary": summary, "priority": {"name": "High"}, "updated": updated,
        "description": {"type": "doc", "version": 1, "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": desc_text}]}]}}}


class _Jira:
    def __init__(self, issues, *, pages=None):
        self.issues = issues
        self.pages = pages          # list of (issues, nextPageToken) for pagination
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, request.content))
        path = request.url.path
        if path == "/rest/api/3/search/jql":
            if self.pages is not None:
                body = request.content.decode()
                token = None
                import json as _j
                token = _j.loads(body).get("nextPageToken")
                idx = 0 if token is None else int(token)
                issues, nxt = self.pages[idx]
                return httpx.Response(200, json={"issues": issues, "nextPageToken": nxt})
            return httpx.Response(200, json={"issues": self.issues, "nextPageToken": None})
        if path.endswith("/comment"):
            return httpx.Response(201, json={"id": "c1"})
        return httpx.Response(404, json={})


@pytest.fixture()
def jira_of(monkeypatch):
    def _install(fake: _Jira):
        monkeypatch.setattr(jira_tracker, "http_client",
                            lambda *a, **k: httpx.Client(transport=httpx.MockTransport(fake.handler)))
        return fake
    return _install


# --------------------------------------------------------------------------- #
# Pure units
# --------------------------------------------------------------------------- #
def test_resolve_jql_maps_selectors():
    r = story_import.resolve_jql
    assert r("open sprint", project_key="XSP") == 'project = "XSP" AND sprint in openSprints()'
    assert r("fixVersion 1.0", project_key="XSP") == 'project = "XSP" AND fixVersion = "1.0"'
    assert r("sprint Alpha", project_key="XSP") == 'project = "XSP" AND sprint = "Alpha"'
    assert r("jql:project = XSP AND status = Done", project_key="XSP") == \
        "project = XSP AND status = Done"
    assert 'issuetype in (Story, Bug, Task)' in r("", project_key="XSP")


def test_adf_to_markdown_renders_nodes():
    adf = {"type": "doc", "version": 1, "content": [
        {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Goal"}]},
        {"type": "paragraph", "content": [
            {"type": "text", "text": "See "},
            {"type": "text", "text": "docs", "marks": [{"type": "link", "attrs": {"href": "http://x"}}]}]},
        {"type": "bulletList", "content": [
            {"type": "listItem", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "first"}]}]}]},
        {"type": "codeBlock", "content": [{"type": "text", "text": "npm test"}]}]}
    md = jira_tracker.adf_to_markdown(adf)
    assert "## Goal" in md and "[docs](http://x)" in md and "- first" in md and "```" in md


# --------------------------------------------------------------------------- #
# Import + idempotence + stale
# --------------------------------------------------------------------------- #
def _refs(db, project):
    return {i.ref for i in db.execute(select(RequirementItem).where(
        RequirementItem.project_id == project.id)).scalars()}


def test_import_creates_requirements_and_maps(db, project, jira_of, humans):
    _connect_jira(db, project)
    jira_of(_Jira([_story("XSP-16", "Add to cart", "As a shopper I can add items"),
                   _story("XSP-17", "Checkout", "Pay with card", pid="2")]))
    out = story_import.import_stories(db, project, selector="open sprint", actor=humans["author"])
    db.commit()
    assert out["count"] == 2 and set(out["imported"]) == {"XSP-16", "XSP-17"}
    assert {"XSP-16", "XSP-17"} <= _refs(db, project)
    assert db.execute(select(JiraIssueMap).where(
        JiraIssueMap.project_id == project.id)).scalars().all()


def test_reimport_is_idempotent_and_flags_stale(db, project, jira_of, humans):
    _connect_jira(db, project)
    jira_of(_Jira([_story("XSP-16", "Add to cart", "original text")]))
    story_import.import_stories(db, project, selector="open sprint", actor=humans["author"])
    db.commit()
    # a test linked to the story
    tc = TestCase(project_id=project.id, key="XSP-T-1", title="cart", status=TestStatus.APPROVED,
                  requirement_refs=["XSP-16"])
    db.add(tc)
    db.commit()

    # re-import with a CHANGED description → update in place + mark stale
    jira_of(_Jira([_story("XSP-16", "Add to cart", "COMPLETELY different text now")]))
    out = story_import.import_stories(db, project, selector="open sprint", actor=humans["author"])
    db.commit()
    assert out["imported"] == [] and out["updated"] == ["XSP-16"]
    assert out["stale"] == ["XSP-16"]
    assert len([i for i in db.execute(select(RequirementItem).where(
        RequirementItem.project_id == project.id, RequirementItem.ref == "XSP-16")).scalars()]) == 1
    assert "stale-requirement" in db.get(TestCase, tc.id).tags


def test_search_jql_follows_next_page_token(db, project, jira_of, humans):
    _connect_jira(db, project)
    jira_of(_Jira([], pages=[
        ([_story("XSP-1", "a", "x", pid="1")], "1"),   # page 0 → token "1"
        ([_story("XSP-2", "b", "y", pid="2")], None),  # page 1 → done
    ]))
    out = story_import.import_stories(db, project, selector="open sprint", actor=humans["author"])
    db.commit()
    assert out["count"] == 2 and set(out["imported"]) == {"XSP-1", "XSP-2"}


# --------------------------------------------------------------------------- #
# Gate + write-back
# --------------------------------------------------------------------------- #
def test_import_tool_is_gated_then_applies(db, project, jira_of, humans):
    _connect_jira(db, project)
    jira_of(_Jira([_story("XSP-16", "Add to cart", "text")]))
    ctx = ToolContext(db=db, project_id=project.id, user=humans["agent"], actor_kind="agent")
    res = asyncio.run(registry.invoke("import_jira_stories", {"selector": "open sprint"}, ctx))
    assert res["status"] == "awaiting_approval"
    approvals.approve(db, res["approval_id"], humans["approver"])
    db.commit()
    assert "XSP-16" in _refs(db, project)


def test_writeback_is_gated_and_comments(db, project, jira_of, humans):
    _connect_jira(db, project)
    fake = jira_of(_Jira([_story("XSP-16", "Add to cart", "text")]))
    story_import.import_stories(db, project, selector="open sprint", actor=humans["author"])
    db.add(TestCase(project_id=project.id, key="XSP-T-9", title="cart", status=TestStatus.APPROVED,
                    requirement_refs=["XSP-16"]))
    db.commit()

    ctx = ToolContext(db=db, project_id=project.id, user=humans["agent"], actor_kind="agent")
    res = asyncio.run(registry.invoke("write_back_jira_coverage", {"ref": "XSP-16"}, ctx))
    assert res["status"] == "awaiting_approval"
    approvals.approve(db, res["approval_id"], humans["approver"])
    db.commit()
    assert any(p.endswith("/comment") for (_m, p, _b) in fake.calls)
    m = db.execute(select(JiraIssueMap).where(
        JiraIssueMap.project_id == project.id, JiraIssueMap.issue_key == "XSP-16")).scalars().first()
    assert m.coverage_written is True


# --------------------------------------------------------------------------- #
# Chat + HTTP
# --------------------------------------------------------------------------- #
def test_chat_connect_returns_secure_block_without_secret(db, project, humans):
    from galeqea.services import jira_chat
    text, blocks = jira_chat.try_handle(db, project, "connect jira https://acme.atlassian.net",
                                        humans["author"])
    assert blocks[0]["type"] == "integration_connect" and blocks[0]["provider"] == "jira"
    assert blocks[0]["base_url"] == "https://acme.atlassian.net"
    assert "token" not in text.lower() or "never" in text.lower()  # never asks for it in chat


def test_chat_import(db, project, jira_of, humans):
    from galeqea.services import jira_chat
    _connect_jira(db, project)
    jira_of(_Jira([_story("XSP-16", "Add to cart", "text")]))
    text, blocks = jira_chat.try_handle(db, project, "import stories from open sprint",
                                        humans["author"])
    assert blocks and blocks[0]["type"] == "jira_import" and blocks[0]["count"] == 1


def test_http_import(db, project, jira_of):
    from fastapi.testclient import TestClient

    from galeqea.main import app
    _connect_jira(db, project)
    jira_of(_Jira([_story("XSP-16", "Add to cart", "text")]))
    client = TestClient(app)
    r = client.post(f"/api/projects/{project.id}/integrations/jira/import",
                    json={"selector": "open sprint"})
    assert r.status_code == 200 and r.json()["count"] == 1
