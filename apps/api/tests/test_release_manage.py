"""WO#5 5-E: archive/delete, cycle roll-up + cycle.finished, and the release
MCP tools (list/create/archive/delete gated; sign-off human-only)."""

from __future__ import annotations

import asyncio

import pytest

import galeqea.ai.toolset  # noqa: F401  (registers the release tools + appliers)
from galeqea.ai.tools import ToolContext, registry
from galeqea.core import approvals
from galeqea.models import Milestone, Run, RunTest, TestCase, TestCategory, TestStatus
from galeqea.models.base import utcnow
from galeqea.services import release


def _case(db, project, key):
    tc = TestCase(project_id=project.id, key=key, title=key, status=TestStatus.APPROVED,
                  category=TestCategory.AUTOMATED, version=1)
    db.add(tc); db.flush()
    return tc


# --------------------------------------------------------------------------- #
# Archive / delete + milestone_for
# --------------------------------------------------------------------------- #
def test_archive_hides_from_lookup_and_frees_the_version(db, project):
    m = release.create_milestone(db, project, name="R", version="1.4")
    db.commit()
    assert release.milestone_for(db, project.id, "1.4") is not None

    release.archive_milestone(db, m)
    db.commit()
    # milestone_for excludes archived, so the version is free to reuse
    assert release.milestone_for(db, project.id, "1.4") is None
    m2 = release.create_milestone(db, project, name="R", version="1.4")
    db.commit()
    assert m2.id != m.id
    assert release.milestone_for(db, project.id, "1.4").id == m2.id


def test_delete_removes_the_milestone(db, project):
    m = release.create_milestone(db, project, name="R", version="9.9")
    db.commit()
    release.delete_milestone(db, m)
    db.commit()
    assert db.get(Milestone, m.id) is None


# --------------------------------------------------------------------------- #
# GO against a NO-GO readiness is an override that requires a note
# --------------------------------------------------------------------------- #
def test_go_against_no_go_requires_a_note(db, project, humans):
    m = release.create_milestone(db, project, name="R", version="3.1")
    release.set_exit_criteria(db, m, [{"metric": "pass_rate", "op": ">=", "value": 0.95}])
    db.commit()
    assert release.evaluate_readiness(db, m)["verdict"] == "no_go"

    with pytest.raises(ValueError, match="override"):
        release.sign_off(db, m, decider=humans["approver"], decision="go", note="")

    release.sign_off(db, m, decider=humans["approver"], decision="go", note="hotfix, risk accepted")
    db.commit()
    assert db.get(Milestone, m.id).signoff["override"] is True


# --------------------------------------------------------------------------- #
# Cycle roll-up + cycle.finished
# --------------------------------------------------------------------------- #
def test_roll_up_marks_cycle_complete_only_when_all_cases_have_results(db, project):
    a = _case(db, project, "C-1")
    b = _case(db, project, "C-2")
    db.commit()
    m = release.create_milestone(db, project, name="R", version="1.5")
    plan, _ = release.create_plan(db, project, milestone=m, name="Reg",
                                  selection={"query": {}}, configurations=[{"browser": "chromium"}])
    cycle = release.start_cycles(db, plan)[0]
    run = Run(project_id=project.id, number=1, status="passed", cycle_id=cycle.id, milestone_id=m.id)
    db.add(run); db.flush()

    # Only one of two pinned cases executed → not finished yet.
    db.add(RunTest(run_id=run.id, test_case_id=a.id, test_key=a.key, status="passed",
                   finished_at=utcnow(), duration_ms=100))
    db.flush()
    assert release.roll_up_cycle(db, cycle) is False
    assert cycle.counters["executed"] == 1 and cycle.status == "active"

    # Second case executes → cycle transitions to COMPLETE exactly once.
    db.add(RunTest(run_id=run.id, test_case_id=b.id, test_key=b.key, status="failed",
                   finished_at=utcnow(), duration_ms=100))
    db.flush()
    assert release.roll_up_cycle(db, cycle) is True
    assert cycle.status == "complete"
    assert cycle.counters == {"planned": 2, "executed": 2, "passed": 1,
                              "failed": 1, "blocked": 0, "skipped": 0}
    # Idempotent: a later roll-up does not re-fire the transition.
    assert release.roll_up_cycle(db, cycle) is False


def test_cycle_finished_and_signed_off_map_to_webhook_events():
    from galeqea.core.events import Ev, Event
    from galeqea.services.webhooks import _event_names

    fin = Event(type=Ev.CYCLE_FINISHED, project_id="p", payload={})
    signed = Event(type=Ev.MILESTONE_SIGNED_OFF, project_id="p", payload={})
    assert _event_names(fin) == ["cycle.finished"]
    assert _event_names(signed) == ["milestone.signed_off"]

    from galeqea.models import WEBHOOK_EVENTS
    assert "cycle.finished" in WEBHOOK_EVENTS and "milestone.signed_off" in WEBHOOK_EVENTS


# --------------------------------------------------------------------------- #
# MCP tools
# --------------------------------------------------------------------------- #
def _ctx(db, project, user, kind="agent"):
    return ToolContext(db=db, project_id=project.id, user=user, actor_kind=kind)


def test_list_releases_reads_and_hides_archived(db, project, humans):
    live = release.create_milestone(db, project, name="R", version="1.0")
    arch = release.create_milestone(db, project, name="R", version="0.9")
    release.archive_milestone(db, arch)
    db.commit()

    out = asyncio.run(registry.invoke("list_releases", {}, _ctx(db, project, humans["author"])))
    versions = {r["version"] for r in out["releases"]}
    assert versions == {"1.0"} and out["count"] == 1

    out2 = asyncio.run(registry.invoke("list_releases", {"include_archived": True},
                                       _ctx(db, project, humans["author"])))
    assert {r["version"] for r in out2["releases"]} == {"1.0", "0.9"}
    assert live.version == "1.0"


def test_create_release_is_gated_then_applies(db, project, humans):
    ctx = _ctx(db, project, humans["agent"], kind="agent")
    result = asyncio.run(registry.invoke("create_release", {"version": "2.2"}, ctx))
    assert result["status"] == "awaiting_approval"

    approvals.approve(db, result["approval_id"], humans["approver"])
    db.commit()
    assert release.milestone_for(db, project.id, "2.2") is not None


def test_delete_release_is_gated(db, project, humans):
    release.create_milestone(db, project, name="R", version="4.0")
    db.commit()
    ctx = _ctx(db, project, humans["agent"], kind="agent")
    result = asyncio.run(registry.invoke("delete_release", {"version": "4.0"}, ctx))
    assert result["status"] == "awaiting_approval"

    approvals.approve(db, result["approval_id"], humans["approver"])
    db.commit()
    # every milestone for that version is gone
    from sqlalchemy import select
    assert db.execute(select(Milestone).where(
        Milestone.project_id == project.id, Milestone.version == "4.0")).scalars().first() is None


def test_sign_off_release_is_human_only(db, project, humans):
    release.create_milestone(db, project, name="R", version="5.0")
    db.commit()
    # An agent caller gets guidance, never a sign-off.
    agent_ctx = _ctx(db, project, humans["agent"], kind="agent")
    guided = asyncio.run(registry.invoke("sign_off_release",
                                         {"version": "5.0", "decision": "go"}, agent_ctx))
    assert guided.get("human_only") is True
    assert db.get(Milestone, release.milestone_for(db, project.id, "5.0").id).signoff in ({}, None)

    # A human_direct approver signs off for real. With no exit criteria the readiness
    # is NO-GO, so a GO is an override and carries a note.
    human_ctx = _ctx(db, project, humans["approver"], kind="human_direct")
    done = asyncio.run(registry.invoke(
        "sign_off_release",
        {"version": "5.0", "decision": "go", "note": "no gate defined; manual accept"}, human_ctx))
    assert done["signed_off"] is True and done["decision"] == "go"
    assert done["override"] is True


# --------------------------------------------------------------------------- #
# HTTP routes: idempotent create, archive/delete, include_archived
# --------------------------------------------------------------------------- #
def test_http_create_is_idempotent_and_archive_delete(db, project):
    from fastapi.testclient import TestClient

    from galeqea.main import app

    client = TestClient(app)
    base = f"/api/projects/{project.id}/milestones"

    first = client.post(base, json={"version": "7.7"})
    assert first.status_code == 201
    mid = first.json()["id"]

    # Same version again → 200 with the same milestone, not a 500 index collision.
    again = client.post(base, json={"version": "7.7"})
    assert again.status_code == 200
    assert again.json()["id"] == mid

    # Archive drops it from the default list; include_archived brings it back.
    assert client.post(f"{base}/{mid}/archive").status_code == 200
    listed = client.get(base).json()["milestones"]
    assert all(m["version"] != "7.7" for m in listed)
    with_arch = client.get(base, params={"include_archived": True}).json()["milestones"]
    assert any(m["version"] == "7.7" and m["status"] == "archived" for m in with_arch)

    # Delete removes it entirely.
    assert client.delete(f"{base}/{mid}").status_code == 200
    assert db.get(Milestone, mid) is None
