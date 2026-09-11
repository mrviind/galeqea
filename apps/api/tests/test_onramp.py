"""The first-run on-ramp: enter a URL in the chat, test it now.

These lock the conversational contract: ask for the URL when it's missing, use
the answer, and never mistake an ordinary command for a URL, without driving a
real browser (that path is exercised live and by the e2e job). The one thing
mocked is ``run_smoke``: the browser is not the unit under test here, the
conversation is.
"""

from __future__ import annotations

import asyncio

import pytest

from galeqea.ai import prompt_slots
from galeqea.ai.orchestrator import Orchestrator
from galeqea.models import ChatSession
from galeqea.services import onramp


# --------------------------------------------------------------------------- #
# URL recognition: the gate that decides on-ramp vs ordinary command.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,expected", [
    ("test https://example.com", "https://example.com"),
    ("check example.com/checkout please", "example.com/checkout"),
    ("smoke test localhost:8765", "localhost:8765"),
    ("go to 127.0.0.1:3000", "127.0.0.1:3000"),
])
def test_finds_a_url_when_one_is_present(text, expected):
    assert onramp.find_url(text) == expected


@pytest.mark.parametrize("text", [
    "run the smoke tests on staging",   # the existing command must NOT be hijacked
    "why did the last run fail?",
    "what's not tested?",
    "is v3.0 released?",                 # a version number is not a host
    "test my site",                     # intent, but no URL: must ask, not guess
])
def test_ordinary_commands_are_not_seen_as_urls(text):
    assert onramp.find_url(text) is None


def test_bare_host_gets_a_scheme_loopback_is_http():
    assert onramp.normalize_url("example.com") == "https://example.com"
    assert onramp.normalize_url("localhost:8765") == "http://localhost:8765"
    assert onramp.normalize_url("127.0.0.1:3000") == "http://127.0.0.1:3000"
    assert onramp.normalize_url("https://x.io") == "https://x.io"


# --------------------------------------------------------------------------- #
# The pending-prompt slot: the "chat asks, next message answers" mechanism.
# --------------------------------------------------------------------------- #
def test_slot_round_trip(db, project):
    session = ChatSession(project_id=project.id, title="t", context={})
    db.add(session); db.flush()

    assert prompt_slots.pending_prompt(session) is None
    prompt_slots.set_prompt(session, slot="smoke_url", question="Which URL?")
    assert prompt_slots.pending_prompt(session)["slot"] == "smoke_url"
    prompt_slots.clear_prompt(session)
    assert prompt_slots.pending_prompt(session) is None


def test_cancel_words_are_recognised():
    assert prompt_slots.is_cancel("cancel")
    assert prompt_slots.is_cancel("never mind")
    assert not prompt_slots.is_cancel("https://example.com")


# --------------------------------------------------------------------------- #
# The built-in smoke probe.
# --------------------------------------------------------------------------- #
def test_smoke_probe_is_created_once_and_reused(db, project):
    first = onramp.ensure_smoke_test(db, project)
    again = onramp.ensure_smoke_test(db, project)
    assert first.id == again.id, "the probe is get-or-create, not create-every-time"
    assert first.status == "approved" and first.category == "automated"
    assert first.provenance.get("origin") == "builtin_smoke"
    assert [s.action for s in sorted(first.steps, key=lambda s: s.index)] == ["goto", "expect_visible"]


def test_setting_the_target_makes_it_the_default_environment(db, project):
    url = onramp.set_target(db, project, "example.com")
    assert url == "https://example.com"
    assert project.environments[onramp.TARGET_ENV] == "https://example.com"
    assert project.default_environment == onramp.TARGET_ENV


# --------------------------------------------------------------------------- #
# The conversation, end to end through the orchestrator (browser mocked).
# --------------------------------------------------------------------------- #
def _session(db, project):
    session = ChatSession(project_id=project.id, title="t", context={})
    db.add(session); db.flush()
    return session


def _mock_website(monkeypatch, captured):
    from galeqea.services import website_test as wt

    def fake_discover(url, limit=8, timeout=15.0):
        captured["discovered"] = onramp.normalize_url(url)
        return {"ok": True, "base": onramp.normalize_url(url),
                "pages": [onramp.normalize_url(url), onramp.normalize_url(url) + "/about"],
                "forms": 1, "status": 200}

    async def fake_run(db, *, project_id, plan, triggered_by=None, timeout=240.0):
        captured["ran"] = plan["target"]
        return {"ok": True, "status": "passed", "target": plan["target"],
                "passed": len(plan["pages"]), "failed": 0, "pages": [],
                "run_id": "r1", "run_number": 1, "summary": "all passed."}

    async def fake_run_keys(db, *, project_id, keys, target, triggered_by=None, timeout=240.0):
        captured["ran"] = target
        captured["ran_keys"] = list(keys)
        return {"ok": True, "status": "passed", "target": target,
                "passed": len(keys), "failed": 0, "pages": [],
                "run_id": "r1", "run_number": 1, "summary": "all passed."}

    monkeypatch.setattr(wt, "discover_pages", fake_discover)
    monkeypatch.setattr(wt, "run_website_test", fake_run)
    monkeypatch.setattr(wt, "run_approved_keys", fake_run_keys)


def test_missing_url_asks_and_the_next_message_answers(db, project, humans, monkeypatch):
    captured: dict = {}
    _mock_website(monkeypatch, captured)
    orch = Orchestrator(db, project_id=project.id)
    session = _session(db, project)

    reply = asyncio.run(orch.handle(session=session, user=humans["author"], text="I want to test my website"))
    assert reply.path == "onramp"
    assert "which url" in reply.text.lower()
    assert prompt_slots.pending_prompt(session)["slot"] == "smoke_url"
    assert "discovered" not in captured, "must not crawl until it has a URL"

    reply2 = asyncio.run(orch.handle(session=session, user=humans["author"], text="http://127.0.0.1:8765"))
    assert captured["discovered"] == "http://127.0.0.1:8765"
    assert prompt_slots.pending_prompt(session) is None, "the slot is consumed"
    # First encounter with a target is a real stop at Guardrails: crawled and the
    # plan stashed, but nothing run.
    assert reply2.blocks and reply2.blocks[0]["type"] == "guardrails_card"
    assert "ran" not in captured


def test_a_url_proposes_a_plan_then_approval_runs_it(db, project, humans, monkeypatch):
    from galeqea.services import website_test as wt

    captured: dict = {}
    _mock_website(monkeypatch, captured)
    orch = Orchestrator(db, project_id=project.id)
    session = _session(db, project)

    # 1. A URL is explored; the first encounter stops at Guardrails. The plan is
    # held on the journey but is NOT yet approvable, so "continue" advances (not runs).
    reply = asyncio.run(orch.handle(session=session, user=humans["author"], text="test https://example.com"))
    assert captured["discovered"] == "https://example.com"
    assert reply.blocks[0]["type"] == "guardrails_card"
    assert wt.pending_plan(session) is None
    assert "ran" not in captured

    # 2. Accepting the guardrails reveals the plan and makes it approvable, still nothing run.
    reply2 = asyncio.run(orch.handle(session=session, user=humans["author"], text="continue"))
    assert reply2.blocks[0]["type"] == "website_plan"
    assert wt.pending_plan(session) is not None
    assert "ran" not in captured

    # 3. Approving in the chat runs it and clears the plan.
    reply3 = asyncio.run(orch.handle(session=session, user=humans["author"], text="approve"))
    assert captured["ran"] == "https://example.com"
    assert wt.pending_plan(session) is None
    assert reply3.blocks[0]["type"] == "website_result"


def test_a_non_url_answer_keeps_the_slot_open(db, project, humans, monkeypatch):
    captured: dict = {}
    _mock_website(monkeypatch, captured)
    orch = Orchestrator(db, project_id=project.id)
    session = _session(db, project)
    asyncio.run(orch.handle(session=session, user=humans["author"], text="test my app"))
    reply = asyncio.run(orch.handle(session=session, user=humans["author"], text="the checkout one"))
    assert prompt_slots.pending_prompt(session) is not None, "still waiting for a URL"
    assert "url" in reply.text.lower()
    assert "discovered" not in captured


def test_cancel_clears_the_prompt(db, project, humans, monkeypatch):
    captured: dict = {}
    _mock_website(monkeypatch, captured)
    orch = Orchestrator(db, project_id=project.id)
    session = _session(db, project)
    asyncio.run(orch.handle(session=session, user=humans["author"], text="test my site"))
    reply = asyncio.run(orch.handle(session=session, user=humans["author"], text="cancel"))
    assert prompt_slots.pending_prompt(session) is None
    assert "discovered" not in captured
    assert "no problem" in reply.text.lower()


def test_an_ordinary_command_does_not_trigger_the_onramp(db, project, humans, monkeypatch):
    captured: dict = {}
    _mock_website(monkeypatch, captured)
    orch = Orchestrator(db, project_id=project.id)
    session = _session(db, project)
    # A No-AI command that must reach the router/computed path, not the on-ramp.
    asyncio.run(orch.handle(session=session, user=humans["author"], text="what's not tested?"))
    assert "url" not in captured
    assert prompt_slots.pending_prompt(session) is None
