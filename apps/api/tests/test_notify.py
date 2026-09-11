"""WO#6 6-E Slack / Teams notifications: event→message formatting, subscriptions
(all vs failures-only), the bus sink, a test send, and the chat verbs.
"""

from __future__ import annotations

import asyncio

import httpx

from galeqea.core.events import Ev, Event
from galeqea.core.vault import seal
from galeqea.models import IntegrationConnection, Run, VaultSecret
from galeqea.services import notify


def _connect(db, project, provider, *, events=None, only_failures=False):
    s = VaultSecret(project_id=project.id, name=f"{provider}.webhook_url", kind=provider)
    db.add(s)
    db.flush()
    s.ciphertext = seal("https://hooks.example.com/xxx", aad=f"{project.id}:{provider}.webhook_url")
    cfg = {}
    if events is not None:
        cfg["events"] = events
    if only_failures:
        cfg["only_failures"] = True
    db.add(IntegrationConnection(project_id=project.id, provider=provider, enabled=True,
                                 config=cfg, secret_refs={"webhook_url": s.id}))
    db.commit()


def _failed_run(db, project):
    run = Run(project_id=project.id, number=7, title="Smoke", status="failed",
              environment="staging", totals={"passed": 3, "failed": 1})
    db.add(run)
    db.commit()
    return run


# --------------------------------------------------------------------------- #
def test_slack_body_shape():
    body = notify._slack_body("Run #7 failed", "3 passed · 1 failed", "/runs/abc")
    assert body["text"].startswith("Run #7 failed")
    assert body["blocks"][0]["type"] == "section"


def test_teams_body_is_a_message_card():
    body = notify._teams_body("Run #7 failed", "detail", "/runs/abc")
    assert body["@type"] == "MessageCard" and body["potentialAction"]


def test_subscribed_all_vs_failures_only(db, project):
    from sqlalchemy import select
    _connect(db, project, "slack")  # default events
    c = db.execute(select(IntegrationConnection).where(
        IntegrationConnection.project_id == project.id)).scalars().first()
    assert notify._subscribed(c, ["run.finished"]) is True   # default = all
    c.config = {"only_failures": True}
    assert notify._subscribed(c, ["run.finished"]) is False   # green run: quiet
    assert notify._subscribed(c, ["run.failed"]) is True      # red run: notify


def test_deliver_posts_to_connected_targets(db, project, monkeypatch):
    posted = []

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None):
            posted.append((url, json))
            return httpx.Response(200)

    # notify imports httpx lazily inside _post; patch the module attribute it uses
    import httpx as _httpx
    monkeypatch.setattr(_httpx, "AsyncClient", _Client)

    _connect(db, project, "slack")
    run = _failed_run(db, project)
    ev = Event(type=Ev.RUN_FINISHED, project_id=project.id, run_id=run.id,
               payload={"status": "failed"})
    asyncio.run(notify.deliver(ev))
    assert posted and "hooks.example.com" in posted[0][0]
    assert "blocks" in posted[0][1]  # slack shape


def test_failures_only_skips_a_passing_run(db, project, monkeypatch):
    posted = []

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None):
            posted.append(url)
            return httpx.Response(200)

    import httpx as _httpx
    monkeypatch.setattr(_httpx, "AsyncClient", _Client)

    _connect(db, project, "teams", only_failures=True)
    run = Run(project_id=project.id, number=8, title="green", status="passed",
              totals={"passed": 4, "failed": 0})
    db.add(run)
    db.commit()
    ev = Event(type=Ev.RUN_FINISHED, project_id=project.id, run_id=run.id,
               payload={"status": "passed"})  # → run.finished only
    asyncio.run(notify.deliver(ev))
    assert posted == []  # failures-only target stays quiet on green


def test_send_test_posts(db, project, monkeypatch):
    from galeqea.integrations import base as ibase
    calls = []
    monkeypatch.setattr(ibase, "http_client",
                        lambda *a, **k: httpx.Client(
                            transport=httpx.MockTransport(
                                lambda req: (calls.append(str(req.url)) or httpx.Response(200, json={})))))
    # notify.send_test uses http_client imported into notify's namespace
    monkeypatch.setattr(notify, "http_client",
                        lambda *a, **k: httpx.Client(
                            transport=httpx.MockTransport(
                                lambda req: (calls.append(str(req.url)) or httpx.Response(200, json={})))))
    _connect(db, project, "slack")
    out = notify.send_test(db, project_id=project.id, provider="slack")
    assert out["ok"] and calls


def test_chat_notify_on_failures(db, project, humans):
    from galeqea.services import notify_chat
    _connect(db, project, "slack")
    text, _blocks = notify_chat.try_handle(db, project, "notify slack on failures",
                                           humans["author"])
    assert "failed run" in text.lower()
    from sqlalchemy import select

    from galeqea.models import IntegrationConnection as IC
    c = db.execute(select(IC).where(IC.project_id == project.id,
                                    IC.provider == "slack")).scalars().first()
    assert c.config.get("only_failures") is True


def test_chat_connect_slack_returns_secure_block(db, project, humans):
    from galeqea.services import notify_chat
    text, blocks = notify_chat.try_handle(db, project, "connect slack", humans["author"])
    assert blocks[0]["type"] == "integration_connect" and blocks[0]["provider"] == "slack"
    assert "secure" in text.lower() or "never" in text.lower()
