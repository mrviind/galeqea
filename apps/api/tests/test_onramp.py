"""The first-run on-ramp: enter a URL in the chat, test it now.

These lock the conversational contract — ask for the URL when it's missing, use
the answer, and never mistake an ordinary command for a URL — without driving a
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
# URL recognition — the gate that decides on-ramp vs ordinary command.
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
    "test my site",                     # intent, but no URL — must ask, not guess
])
def test_ordinary_commands_are_not_seen_as_urls(text):
    assert onramp.find_url(text) is None


def test_bare_host_gets_a_scheme_loopback_is_http():
    assert onramp.normalize_url("example.com") == "https://example.com"
    assert onramp.normalize_url("localhost:8765") == "http://localhost:8765"
    assert onramp.normalize_url("127.0.0.1:3000") == "http://127.0.0.1:3000"
    assert onramp.normalize_url("https://x.io") == "https://x.io"


# --------------------------------------------------------------------------- #
# The pending-prompt slot — the "chat asks, next message answers" mechanism.
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


def _mock_smoke(monkeypatch, captured):
    async def fake(db, *, project_id, url, triggered_by=None, timeout=90.0):
        captured["url"] = url
        return {"ok": True, "status": "passed", "target": onramp.normalize_url(url),
                "console_errors": [], "network_failures": [], "summary": f"{url} is up.",
                "run_id": "r1", "run_number": 1}
    monkeypatch.setattr(onramp, "run_smoke", fake)


def test_missing_url_asks_and_the_next_message_answers(db, project, humans, monkeypatch):
    captured: dict = {}
    _mock_smoke(monkeypatch, captured)
    orch = Orchestrator(db, project_id=project.id)
    session = _session(db, project)

    reply = asyncio.run(orch.handle(session=session, user=humans["author"], text="I want to test my website"))
    assert reply.path == "onramp"
    assert "which url" in reply.text.lower()
    assert prompt_slots.pending_prompt(session)["slot"] == "smoke_url"
    assert "url" not in captured, "must not run anything until it has a URL"

    reply2 = asyncio.run(orch.handle(session=session, user=humans["author"], text="http://127.0.0.1:8765"))
    assert captured["url"] == "http://127.0.0.1:8765"
    assert prompt_slots.pending_prompt(session) is None, "the slot is consumed"
    assert "up" in reply2.text.lower()


def test_a_url_in_one_shot_runs_immediately(db, project, humans, monkeypatch):
    captured: dict = {}
    _mock_smoke(monkeypatch, captured)
    orch = Orchestrator(db, project_id=project.id)
    session = _session(db, project)
    asyncio.run(orch.handle(session=session, user=humans["author"], text="test https://example.com"))
    assert captured["url"] == "https://example.com"


def test_a_non_url_answer_keeps_the_slot_open(db, project, humans, monkeypatch):
    captured: dict = {}
    _mock_smoke(monkeypatch, captured)
    orch = Orchestrator(db, project_id=project.id)
    session = _session(db, project)
    asyncio.run(orch.handle(session=session, user=humans["author"], text="test my app"))
    reply = asyncio.run(orch.handle(session=session, user=humans["author"], text="the checkout one"))
    assert prompt_slots.pending_prompt(session) is not None, "still waiting for a URL"
    assert "url" in reply.text.lower()
    assert "url" not in captured


def test_cancel_clears_the_prompt(db, project, humans, monkeypatch):
    captured: dict = {}
    _mock_smoke(monkeypatch, captured)
    orch = Orchestrator(db, project_id=project.id)
    session = _session(db, project)
    asyncio.run(orch.handle(session=session, user=humans["author"], text="test my site"))
    reply = asyncio.run(orch.handle(session=session, user=humans["author"], text="cancel"))
    assert prompt_slots.pending_prompt(session) is None
    assert "url" not in captured
    assert "no problem" in reply.text.lower()


def test_an_ordinary_command_does_not_trigger_the_onramp(db, project, humans, monkeypatch):
    captured: dict = {}
    _mock_smoke(monkeypatch, captured)
    orch = Orchestrator(db, project_id=project.id)
    session = _session(db, project)
    # A No-AI command that must reach the router/computed path, not the on-ramp.
    asyncio.run(orch.handle(session=session, user=humans["author"], text="what's not tested?"))
    assert "url" not in captured
    assert prompt_slots.pending_prompt(session) is None
