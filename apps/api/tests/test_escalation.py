"""escalate_to_human — the agent's responsible hand-off."""

from __future__ import annotations

from galeqea.ai.tools import ToolContext, registry
from galeqea.ai.toolset import tool_catalog  # noqa: F401
from galeqea.mcp.qe_tools import escalate_to_human
from galeqea.models import Notification


def test_it_records_a_notification_for_the_user(db, project):
    ctx = ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")
    before = db.query(Notification).count()
    result = escalate_to_human({
        "blocker": "REQ-014 does not state the maximum card length.",
        "question": "Which card schemes must checkout accept, and the length of each?",
        "severity": "blocker",
    }, ctx)
    db.flush()
    assert result["escalated"] is True
    assert db.query(Notification).count() == before + 1
    note = db.query(Notification).order_by(Notification.created_at.desc()).first()
    assert note.kind == "escalation"
    assert "Which card schemes" in note.title


def test_options_are_recorded_for_a_choice(db, project):
    ctx = ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")
    result = escalate_to_human({
        "blocker": "No test-id on the Confirm button.",
        "question": "Record the flow, or will you paste the selector?",
        "options": ["Record it now", "I'll paste the selector"],
        "severity": "question",
    }, ctx)
    db.flush()
    assert result["options"] == ["Record it now", "I'll paste the selector"]
    note = db.query(Notification).order_by(Notification.created_at.desc()).first()
    assert "Record it now" in note.body


def test_both_a_blocker_and_a_question_are_required():
    assert escalate_to_human({"blocker": "x", "question": ""}, None)["ok"] is False
    assert escalate_to_human({"blocker": "", "question": "y"}, None)["ok"] is False


def test_the_guidance_forbids_answering_your_own_question():
    result = escalate_to_human({"blocker": "b", "question": "q"}, None)
    assert "invent an answer" in result["guidance"]


def test_a_blocker_projects_as_blocked_a_question_as_needs_work():
    blocked = escalate_to_human({"blocker": "b", "question": "q", "severity": "blocker"}, None)
    assert blocked["_ui"]["review"]["verdict"] == "blocked"
    question = escalate_to_human({"blocker": "b", "question": "q", "severity": "question"}, None)
    assert question["_ui"]["review"]["verdict"] == "needs_work"


def test_it_is_registered_read_only_with_no_side_effect_gate():
    tool = registry.get("escalate_to_human")
    assert tool is not None
    assert tool.read_only  # writes a notification, but that is not a gated state change
    assert tool.approval_action is None
