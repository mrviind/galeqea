"""WO#6 6-C: push results to Xray / Zephyr Scale / TestRail through one gated
`results.push`, idempotently. All three trackers via recorded HTTP fixtures.
"""

from __future__ import annotations

import asyncio

import httpx
from sqlalchemy import select

import galeqea.ai.toolset  # noqa: F401  (registers push_results + applier)
from galeqea.ai.tools import ToolContext, registry
from galeqea.core import approvals
from galeqea.core.vault import seal
from galeqea.integrations import testrail as testrail_be
from galeqea.integrations import xray as xray_be
from galeqea.integrations import zephyr as zephyr_be
from galeqea.models import (
    IntegrationConnection,
    Run,
    RunExport,
    RunTest,
    TestCase,
    TestStatus,
    VaultSecret,
)
from galeqea.services import results_push


def _secret(db, project, name, value):
    s = VaultSecret(project_id=project.id, name=name, kind="x")
    db.add(s)
    db.flush()
    s.ciphertext = seal(value, aad=f"{project.id}:{name}")
    return s.id


def _connect(db, project, provider, config, secrets):
    refs = {k: _secret(db, project, f"{provider}.{k}", v) for k, v in secrets.items()}
    db.add(IntegrationConnection(project_id=project.id, provider=provider, enabled=True,
                                 config=config, secret_refs=refs))
    db.commit()


def _run_with_results(db, project, *, xray_tag=True, testrail_tag=False):
    tags1 = (["xray:APP-77"] if xray_tag else []) + (["testrail:101"] if testrail_tag else [])
    a = TestCase(project_id=project.id, key="APP-T-1", title="pays", status=TestStatus.APPROVED,
                 category="automated", tags=tags1, requirement_refs=["APP-12"])
    b = TestCase(project_id=project.id, key="APP-T-2", title="cart", status=TestStatus.APPROVED,
                 category="automated", tags=(["testrail:102"] if testrail_tag else []))
    db.add_all([a, b])
    db.flush()
    run = Run(project_id=project.id, number=1, title="Smoke", status="failed",
              environment="staging", totals={"total": 2, "passed": 1, "failed": 1})
    db.add(run)
    db.flush()
    db.add_all([
        RunTest(run_id=run.id, test_case_id=a.id, test_key="APP-T-1", title="pays",
                status="passed", browser="chromium"),
        RunTest(run_id=run.id, test_case_id=b.id, test_key="APP-T-2", title="cart",
                status="failed", browser="chromium", error_message="boom"),
    ])
    db.commit()
    return run


def _patch(monkeypatch, module, handler):
    monkeypatch.setattr(module, "http_client",
                        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(handler)))


# --------------------------------------------------------------------------- #
# Xray
# --------------------------------------------------------------------------- #
class _Xray:
    def __init__(self):
        self.calls = []

    def handler(self, request):
        self.calls.append((request.method, request.url.path, request.content))
        if request.url.path.endswith("/authenticate"):
            return httpx.Response(200, text='"xtoken"')
        if request.url.path.endswith("/import/execution"):
            return httpx.Response(200, json={"key": "APP-900", "self": "http://x/APP-900"})
        return httpx.Response(404, json={})


def test_push_xray_records_export_with_definition(db, project, monkeypatch, humans):
    x = _Xray()
    _patch(monkeypatch, xray_be, x.handler)
    _connect(db, project, "xray", {"project_key": "APP"},
             {"client_id": "cid", "client_secret": "csec"})
    run = _run_with_results(db, project, xray_tag=True)

    out = results_push.push(db, project, run_id=run.id, provider="xray",
                            target="APP-10", actor=humans["author"])
    db.commit()
    assert out["exec_key"] == "APP-900" and out["cached"] is False
    # the import body carries a testKey for the tagged test and a testInfo.definition
    # for the untagged one
    body = [c for (m, p, c) in x.calls if p.endswith("/import/execution")][0].decode()
    assert '"APP-77"' in body and '"definition"' in body and '"APP-10"' in body
    assert db.execute(select(RunExport).where(RunExport.run_id == run.id)).scalars().first()


def test_push_is_idempotent(db, project, monkeypatch, humans):
    x = _Xray()
    _patch(monkeypatch, xray_be, x.handler)
    _connect(db, project, "xray", {"project_key": "APP"},
             {"client_id": "cid", "client_secret": "csec"})
    run = _run_with_results(db, project)
    results_push.push(db, project, run_id=run.id, provider="xray", target="APP-10",
                      actor=humans["author"])
    db.commit()
    imports = sum(1 for (m, p, _c) in x.calls if p.endswith("/import/execution"))

    second = results_push.push(db, project, run_id=run.id, provider="xray", target="APP-10",
                               actor=humans["author"])
    db.commit()
    assert second["cached"] is True
    # no new import call on the cached path
    assert sum(1 for (m, p, _c) in x.calls if p.endswith("/import/execution")) == imports


# --------------------------------------------------------------------------- #
# Zephyr Scale + TestRail
# --------------------------------------------------------------------------- #
def test_push_zephyr_posts_junit(db, project, monkeypatch, humans):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"testCycle": {"key": "CYC-1", "self": "http://z/CYC-1"}})

    _patch(monkeypatch, zephyr_be, handler)
    _connect(db, project, "zephyr_scale",
             {"project_key": "APP", "base_url": "https://api.zephyrscale.smartbear.com/v2"},
             {"api_token": "ztok"})
    run = _run_with_results(db, project, xray_tag=False)
    out = results_push.push(db, project, run_id=run.id, provider="zephyr_scale",
                            target="Regression", actor=humans["author"])
    db.commit()
    assert out["exec_key"] == "CYC-1"
    assert any(p.endswith("/automations/executions/junit") for (_m, p) in calls)


def test_push_testrail_creates_run_and_results(db, project, monkeypatch, humans):
    calls = []

    def handler(request):
        calls.append((request.method, str(request.url)))
        if "add_run" in str(request.url):
            return httpx.Response(200, json={"id": 55})
        if "add_results_for_cases" in str(request.url):
            return httpx.Response(200, json=[{"id": 1}])
        return httpx.Response(404, json={})

    _patch(monkeypatch, testrail_be, handler)
    _connect(db, project, "testrail",
             {"base_url": "https://acme.testrail.io", "username": "qa@acme.io",
              "section_id": "1", "project_id": "9"},
             {"api_key": "trkey"})
    run = _run_with_results(db, project, xray_tag=False, testrail_tag=True)
    out = results_push.push(db, project, run_id=run.id, provider="testrail",
                            actor=humans["author"])
    db.commit()
    assert out["exec_key"] == "55" and out["pushed"] == 2
    assert any("add_run" in u for (_m, u) in calls)
    assert any("add_results_for_cases" in u for (_m, u) in calls)


# --------------------------------------------------------------------------- #
# Gate + chat + HTTP
# --------------------------------------------------------------------------- #
def test_push_results_tool_is_gated_then_applies(db, project, monkeypatch, humans):
    x = _Xray()
    _patch(monkeypatch, xray_be, x.handler)
    _connect(db, project, "xray", {"project_key": "APP"},
             {"client_id": "cid", "client_secret": "csec"})
    run = _run_with_results(db, project)
    ctx = ToolContext(db=db, project_id=project.id, user=humans["agent"], actor_kind="agent")
    res = asyncio.run(registry.invoke("push_results",
                                      {"run_id": run.id, "provider": "xray"}, ctx))
    assert res["status"] == "awaiting_approval"
    approvals.approve(db, res["approval_id"], humans["approver"])
    db.commit()
    assert db.execute(select(RunExport).where(RunExport.run_id == run.id)).scalars().first()


def test_chat_push_files_approval(db, project, monkeypatch, humans):
    from galeqea.services import results_chat
    _connect(db, project, "xray", {"project_key": "APP"},
             {"client_id": "cid", "client_secret": "csec"})
    run = _run_with_results(db, project)
    text, blocks = results_chat.try_handle(db, project, "push run #1 to xray plan APP-10",
                                           humans["author"])
    assert blocks and blocks[0]["action"] == "results.push"
    assert "approval" in text.lower()


def test_http_push_returns_pending(db, project, monkeypatch):
    from fastapi.testclient import TestClient

    from galeqea.main import app
    _connect(db, project, "xray", {"project_key": "APP"},
             {"client_id": "cid", "client_secret": "csec"})
    run = _run_with_results(db, project)
    client = TestClient(app)
    r = client.post(f"/api/projects/{project.id}/runs/{run.id}/push",
                    json={"provider": "xray", "target": "APP-10"})
    assert r.status_code == 202 and r.json()["status"] == "awaiting_approval"


def test_http_push_without_connection_is_409(db, project):
    from fastapi.testclient import TestClient

    from galeqea.main import app
    run = _run_with_results(db, project)
    client = TestClient(app)
    r = client.post(f"/api/projects/{project.id}/runs/{run.id}/push", json={"provider": "xray"})
    assert r.status_code == 409
