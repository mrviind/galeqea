"""judge_test_against_criteria — the coverage review_test cannot see.

review_test asks "is this test structurally sound?". This asks "does it verify
what the requirement demands?". A test can pass the first and fail the second —
that is the whole point of having both.
"""

from __future__ import annotations

from galeqea.ai.tools import ToolContext, registry
from galeqea.ai.toolset import tool_catalog  # noqa: F401
from galeqea.mcp.qe_tools import judge_test_against_criteria

THREE_CRITERIA = [
    "A card passing the Luhn check is accepted and the order is confirmed",
    "A declined card shows an actionable error and commits nothing",
    "The order total is recalculated when an item is removed",
]


def _proposal(steps, refs=("REQ-014",)):
    return {"title": "Checkout", "requirement_refs": list(refs), "steps": steps}


# --------------------------------------------------------------------------- #
# The core adversarial case
# --------------------------------------------------------------------------- #
def test_a_structurally_sound_test_that_misses_a_criterion_is_caught():
    """This is the failure mode: the test asserts things, its locators are fine,
    but one criterion has no assertion at all."""
    proposal = _proposal([
        {"action": "click", "intent": "click Confirm payment"},
        {"action": "expect_visible", "intent": "order confirmation appears",
         "expected": "the order confirmed banner is shown"},
        {"action": "expect_text", "intent": "decline shows an error",
         "expected": "an actionable error message about the declined card"},
    ])
    result = judge_test_against_criteria({"proposal": proposal, "criteria": THREE_CRITERIA}, None)
    assert result["verdict"] == "uncovered"
    assert result["covered_count"] == 2
    missed = [c for c in result["coverage"] if not c["covered"]]
    assert len(missed) == 1
    assert "recalculated" in missed[0]["criterion"]


def test_a_test_covering_all_criteria_is_marked_covered():
    proposal = _proposal([
        {"action": "expect_visible", "intent": "luhn card accepted, order confirmed",
         "expected": "the order is confirmed"},
        {"action": "expect_text", "intent": "declined card error, nothing committed",
         "expected": "an actionable error, no order created"},
        {"action": "expect_text", "intent": "order total recalculated after removing item",
         "expected": "the total updates"},
    ])
    result = judge_test_against_criteria({"proposal": proposal, "criteria": THREE_CRITERIA}, None)
    assert result["verdict"] == "covered"
    assert result["uncovered_count"] == 0


def test_a_test_with_no_assertions_covers_nothing():
    result = judge_test_against_criteria(
        {"proposal": _proposal([{"action": "click", "intent": "click"}]), "criteria": ["A card is accepted"]},
        None,
    )
    assert result["verdict"] == "uncovered"
    assert "asserts nothing" in result["guidance"]


# --------------------------------------------------------------------------- #
# Matching is conservative — it does not claim coverage it cannot see
# --------------------------------------------------------------------------- #
def test_a_single_shared_stopword_is_not_coverage():
    """An assertion that merely shares "the user" with a criterion is not
    coverage. Two meaningful words are required."""
    result = judge_test_against_criteria({
        "proposal": _proposal([{"action": "expect_visible", "intent": "the user is happy",
                                "expected": "the page shows"}]),
        "criteria": ["The refund is processed within five days"],
    }, None)
    assert result["verdict"] == "uncovered"


def test_stemming_matches_tense_variants():
    """'accepted' in the criterion should match 'accepts' in the assertion."""
    result = judge_test_against_criteria({
        "proposal": _proposal([{"action": "expect_text", "intent": "the system accepts the valid coupon code",
                                "expected": "coupon applied"}]),
        "criteria": ["A valid coupon is accepted and applied"],
    }, None)
    assert result["coverage"][0]["covered"] is True


def test_numbers_are_kept_as_distinguishing_keywords():
    result = judge_test_against_criteria({
        "proposal": _proposal([{"action": "expect_text", "intent": "password of 8 characters is accepted",
                                "expected": "accepted"}]),
        "criteria": ["A password of 8 characters is accepted"],
    }, None)
    assert result["coverage"][0]["covered"] is True


# --------------------------------------------------------------------------- #
# Sourcing criteria
# --------------------------------------------------------------------------- #
def test_criteria_are_read_from_an_ingested_requirement(db, project):
    from galeqea.models import DocKind, RequirementDoc, RequirementItem

    doc = RequirementDoc(project_id=project.id, title="PRD", kind=DocKind.REQUIREMENT)
    db.add(doc)
    db.flush()
    db.add(RequirementItem(doc_id=doc.id, project_id=project.id, ref="REQ-500",
                           title="Login", text="x",
                           acceptance_criteria=["A valid password signs the user in"]))
    db.flush()
    ctx = ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")
    result = judge_test_against_criteria({
        "proposal": _proposal([{"action": "expect_url", "intent": "valid password signs in to dashboard",
                                "expected": "url is /dashboard"}], refs=[]),
        "requirement_ref": "REQ-500",
    }, ctx)
    assert result["criteria_count"] == 1
    assert result["coverage"][0]["covered"] is True


def test_a_requirement_without_criteria_is_refused(db, project):
    from galeqea.models import DocKind, RequirementDoc, RequirementItem

    doc = RequirementDoc(project_id=project.id, title="P2", kind=DocKind.REQUIREMENT)
    db.add(doc)
    db.flush()
    db.add(RequirementItem(doc_id=doc.id, project_id=project.id, ref="REQ-501", title="Bare", acceptance_criteria=[]))
    db.flush()
    ctx = ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")
    result = judge_test_against_criteria(
        {"proposal": _proposal([{"action": "expect_text", "intent": "x", "expected": "y"}], refs=[]),
         "requirement_ref": "REQ-501"}, ctx)
    assert result["ok"] is False
    assert "no acceptance criteria" in result["error"]


def test_it_projects_uncovered_criteria_onto_the_rca_pane():
    result = judge_test_against_criteria(
        {"proposal": _proposal([{"action": "click", "intent": "click"}]), "criteria": ["X happens"]}, None)
    assert result["_ui"]["pane"] == "rca"
    assert result["_ui"]["review"]["verdict"] == "needs_work"


def test_it_is_registered_read_only():
    tool = registry.get("judge_test_against_criteria")
    assert tool is not None and tool.read_only and tool.approval_action is None
