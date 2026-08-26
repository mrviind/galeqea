"""review_test, analyze_change_impact and propose_plan."""

from __future__ import annotations

from galeqea.ai.tools import ToolContext, registry
from galeqea.ai.toolset import tool_catalog  # noqa: F401
from galeqea.intelligence.selection import select_for_change
from galeqea.mcp.qe_tools import analyze_change_impact, propose_plan, review_test


def _proposal(steps, refs=("REQ-1",)):
    return {"title": "T", "requirement_refs": list(refs), "steps": steps}


# --------------------------------------------------------------------------- #
# review_test
# --------------------------------------------------------------------------- #
def test_a_test_with_no_assertion_is_blocked():
    """The most important check: acting proves the flow runs, not that it is right."""
    r = review_test({"proposal": _proposal([
        {"action": "goto", "intent": "open"},
        {"action": "click", "intent": "click Save", "target": {"ladder": [{"kind": "role", "role": "button", "name": "Save"}]}},
    ])}, None)
    assert r["verdict"] == "blocked"
    assert any(f["kind"] == "no_assertion" and f["severity"] == "critical" for f in r["findings"])


def test_an_assertion_with_no_requirement_is_flagged_untraceable():
    r = review_test({"proposal": _proposal([
        {"action": "expect_text", "intent": "shows", "expected": "Done", "value": {"text": "Done"}},
    ], refs=[])}, None)
    assert any(f["kind"] == "untraceable" for f in r["findings"])


def test_a_guessed_css_locator_is_flagged():
    r = review_test({"proposal": _proposal([
        {"action": "click", "intent": "click", "target": {"ladder": [{"kind": "css", "value": ".btn"}]}},
        {"action": "expect_text", "intent": "done", "expected": "Done", "value": {"text": "Done"}},
    ])}, None)
    assert any(f["kind"] == "fragile_locator" and f["step"] == 0 for f in r["findings"])


def test_a_role_locator_is_not_flagged_as_fragile():
    r = review_test({"proposal": _proposal([
        {"action": "click", "intent": "click", "target": {"ladder": [
            {"kind": "role", "role": "button", "name": "Save"}, {"kind": "css", "value": ".btn"}]}},
        {"action": "expect_text", "intent": "done", "expected": "Done", "value": {"text": "Done"}},
    ])}, None)
    assert not any(f["kind"] == "fragile_locator" for f in r["findings"])


def test_a_todo_step_is_flagged():
    r = review_test({"proposal": _proposal([
        {"action": "note", "intent": "TODO: fill this in", "expected": ""},
        {"action": "expect_text", "intent": "x", "expected": "X", "value": {"text": "X"}},
    ])}, None)
    assert any(f["kind"] == "unresolved_step" for f in r["findings"])


def test_a_sound_test_passes_review():
    r = review_test({"proposal": _proposal([
        {"action": "click", "intent": "pay", "target": {"ladder": [{"kind": "role", "role": "button", "name": "Pay"}]}},
        {"action": "expect_visible", "intent": "confirmed", "expected": "the banner shows",
         "target": {"ladder": [{"kind": "text", "value": "Order confirmed"}]}},
    ])}, None)
    assert r["verdict"] == "sound"
    assert r["findings"] == []


def test_review_reads_a_stored_test(db, project):
    from galeqea.models import StepAction, TestCase, TestStep, TestStatus

    case = TestCase(project_id=project.id, key="TST-T-1", title="Stored", status=TestStatus.APPROVED,
                    requirement_refs=[])
    db.add(case)
    db.flush()
    db.add(TestStep(test_case_id=case.id, index=0, action=StepAction.CLICK, intent="click"))
    db.flush()
    ctx = ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")
    r = review_test({"test_id_or_key": "TST-T-1"}, ctx)
    assert r["target"] == "TST-T-1"
    assert r["verdict"] == "blocked"  # no assertion


def test_review_projects_onto_the_rca_pane():
    r = review_test({"proposal": _proposal([{"action": "click", "intent": "x",
                     "target": {"ladder": [{"kind": "role", "role": "button", "name": "X"}]}}])}, None)
    assert r["_ui"]["pane"] == "rca"
    assert "verdict" in r["_ui"]["review"]


# --------------------------------------------------------------------------- #
# analyze_change_impact — and the crash it uncovered
# --------------------------------------------------------------------------- #
def test_an_empty_suite_does_not_divide_by_zero(db, project):
    """A fresh project has no approved tests; impact analysis must still answer."""
    result = select_for_change(db, project.id, changed_paths=["src/x.ts"])
    assert result["selected"] == []
    assert "No approved tests" in result["coverage_note"]


def test_impact_reports_selection_and_omission(db, project):
    ctx = ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")
    result = analyze_change_impact({"changed_paths": ["src/checkout/pay.ts"]}, ctx)
    assert result["ok"]
    assert "selected_count" in result and "omitted_count" in result
    assert result["signal"] in {"weak", "strong"}


def test_impact_needs_a_path():
    from galeqea.ai.tools import ToolContext as TC
    r = analyze_change_impact({"changed_paths": []}, TC(db=None, project_id="p", user=None, actor_kind="agent"))
    assert r["ok"] is False


# --------------------------------------------------------------------------- #
# propose_plan
# --------------------------------------------------------------------------- #
def test_effect_comes_from_the_registry_not_the_models_claim():
    """A step calling a gated tool is marked 'needs approval' even if the model
    described it as read-only."""
    plan = propose_plan({"goal": "g", "steps": [
        {"tool": "query_requirements", "why": "read"},
        {"tool": "create_test", "why": "file"},
    ]}, None)
    effects = {s["tool"]: s["effect"] for s in plan["steps"]}
    assert effects["query_requirements"] == "read-only"
    assert effects["create_test"] == "needs approval"
    assert plan["writes_state"] is True


def test_unknown_tools_are_flagged():
    plan = propose_plan({"goal": "g", "steps": [{"tool": "made_up", "why": "?"}]}, None)
    assert plan["unknown_tools"] == ["made_up"]
    assert "not a registered tool" in plan["guidance"]


def test_a_read_only_plan_says_it_can_run_without_side_effects():
    plan = propose_plan({"goal": "g", "steps": [
        {"tool": "query_requirements", "why": "read"},
        {"tool": "list_tests", "why": "read"},
    ]}, None)
    assert plan["writes_state"] is False
    assert "only reads" in plan["guidance"]


def test_a_plan_needs_a_goal_and_steps():
    assert propose_plan({"goal": "", "steps": []}, None)["ok"] is False


def test_the_three_new_tools_carry_full_metadata():
    for name in ("review_test", "analyze_change_impact", "propose_plan"):
        tool = registry.get(name)
        assert tool is not None
        assert tool.title
        assert len([s for s in tool.description.split(". ") if len(s) > 12]) >= 3
        assert tool.read_only, f"{name} must be read-only — none of these write directly"


# --------------------------------------------------------------------------- #
# propose_plan must carry executable arguments (found by running the plan gate)
# --------------------------------------------------------------------------- #
def test_plan_steps_carry_executable_arguments_not_just_a_summary():
    """A stored plan runs its steps verbatim on confirmation, so each step needs
    the real argument object, not only a human-readable summary."""
    plan = propose_plan({"goal": "g", "steps": [
        {"tool": "query_requirements", "why": "read", "arguments": {"feature": "checkout"}},
    ]}, None)
    step = plan["steps"][0]
    assert step["arguments"] == {"feature": "checkout"}
    assert step["executable"] is True


def test_a_read_only_step_is_executable_even_with_no_arguments():
    plan = propose_plan({"goal": "g", "steps": [
        {"tool": "list_tests", "why": "survey"},
    ]}, None)
    assert plan["steps"][0]["executable"] is True
