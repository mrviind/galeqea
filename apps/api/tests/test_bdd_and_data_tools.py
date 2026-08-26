"""generate_bdd_scenarios and generate_test_data."""

from __future__ import annotations

from galeqea.ai.tools import ToolContext, registry
from galeqea.ai.toolset import tool_catalog  # noqa: F401
from galeqea.mcp.qe_tools import (
    _feature_from_title,
    _split_criterion,
    generate_bdd_scenarios,
    generate_test_data,
)


# --------------------------------------------------------------------------- #
# Criterion parsing — the failures that shipped in the first version
# --------------------------------------------------------------------------- #
def test_a_when_comma_criterion_splits_at_the_comma_not_the_first_space():
    when, then, derived = _split_criterion(
        "When the user submits a password between 8 and 64 characters, the account is created"
    )
    assert derived
    assert when == "the user submits a password between 8 and 64 characters"
    assert then == "the account is created"


def test_if_then_splits_on_then():
    when, then, _ = _split_criterion("If the card is declined then an error is shown")
    assert when == "the card is declined"
    assert then == "an error is shown"


def test_an_outcome_only_criterion_derives_an_action_from_its_subject():
    when, then, derived = _split_criterion("A card passing the Luhn check is accepted")
    assert derived
    assert when == "the user provides a card passing the Luhn check"
    assert then == "a card passing the Luhn check is accepted"


def test_an_unparseable_criterion_is_flagged_not_guessed():
    when, _, derived = _split_criterion("Robustness")
    assert not derived
    assert when.startswith("TODO")


def test_feature_name_is_the_subject_before_the_modal_verb():
    assert _feature_from_title("Checkout must accept a valid payment card") == "Checkout"
    assert _feature_from_title("Password rules") == "Password rules"


# --------------------------------------------------------------------------- #
# Scenario generation
# --------------------------------------------------------------------------- #
def test_one_scenario_per_criterion_plus_an_outline_for_a_stated_domain():
    result = generate_bdd_scenarios({
        "feature": "Password",
        "criteria": [
            "When the user submits a password between 8 and 64 characters, the account is created",
            "A password shorter than the minimum shows an error",
        ],
    }, None)
    assert result["ok"]
    kinds = [s["kind"] for s in result["scenarios"]]
    assert kinds.count("outline") == 1, "one merged Outline, not one per phrasing of the variable"
    outline = next(s for s in result["scenarios"] if s["kind"] == "outline")
    values = {e["value"] for e in outline["examples"]}
    assert {"7", "8", "64", "65"} <= values, "boundary values from the stated range"
    assert "boundary value" in outline["technique"]


def test_negatives_are_only_generated_where_a_criterion_has_an_inverse():
    """"The confirmation shows the order number" has no negative worth writing."""
    result = generate_bdd_scenarios({
        "feature": "Checkout",
        "criteria": ["A card passing the Luhn check is accepted",
                     "The confirmation shows the order number"],
    }, None)
    negatives = [s for s in result["scenarios"] if s["technique"] == "negative path"]
    assert len(negatives) == 1
    assert "Luhn" in negatives[0]["title"]
    assert "does not meet the requirement" in negatives[0]["steps"][1]


def test_the_negated_action_drops_the_qualifying_clause():
    result = generate_bdd_scenarios({
        "feature": "Password",
        "criteria": ["When the user submits a password between 8 and 64 characters, the account is created"],
    }, None)
    negative = next(s for s in result["scenarios"] if s["technique"] == "negative path")
    assert negative["steps"][1] == "When the user submits a password that does not meet the requirement"


def test_the_feature_file_is_valid_gherkin_with_traceability():
    code = generate_bdd_scenarios({"feature": "Sign in", "requirement_ref": "",
                                   "criteria": ["A valid password signs the user in"]}, None)["feature_file"]["code"]
    assert code.startswith("Feature: Sign in\n")
    assert "  Scenario: " in code
    assert "    Given " in code and "    When " in code and "    Then " in code


def test_no_criteria_is_refused_rather_than_invented():
    result = generate_bdd_scenarios({"feature": "X"}, None)
    assert result["ok"] is False
    assert "Do not invent" in result["error"]


def test_a_requirement_without_criteria_is_refused(db, project):
    from galeqea.models import DocKind, RequirementDoc, RequirementItem

    doc = RequirementDoc(project_id=project.id, title="D", kind=DocKind.REQUIREMENT)
    db.add(doc)
    db.flush()
    db.add(RequirementItem(doc_id=doc.id, project_id=project.id, ref="REQ-900",
                           title="Bare", text="x", acceptance_criteria=[]))
    db.flush()
    ctx = ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")
    result = generate_bdd_scenarios({"requirement_ref": "REQ-900"}, ctx)
    assert result["ok"] is False
    assert "no acceptance criteria" in result["error"]


def test_it_projects_the_feature_file_onto_the_test_matrix():
    result = generate_bdd_scenarios({"feature": "X", "criteria": ["The page loads"]}, None)
    assert result["_ui"]["pane"] == "test_matrix"
    assert result["_ui"]["files"][0]["language"] == "gherkin"


def test_generation_is_deterministic():
    args = {"feature": "X", "criteria": ["When A, B", "C is accepted"]}
    assert generate_bdd_scenarios(args, None) == generate_bdd_scenarios(args, None)


# --------------------------------------------------------------------------- #
# Test data
# --------------------------------------------------------------------------- #
def test_kinds_are_inferred_and_invalid_variants_carry_reasons():
    result = generate_test_data({"fields": ["customerEmail", "cardNumber"], "rows": 2}, None)
    assert result["ok"]
    kinds = {f["name"]: f["kind"] for f in result["fields"]}
    assert kinds == {"customerEmail": "email", "cardNumber": "credit_card"}
    assert len(result["records"]) == 2
    assert all(i["why"] for f in result["fields"] for i in f["invalid"])


def test_data_is_reproducible_for_a_seed():
    a = generate_test_data({"fields": ["postcode"], "seed": "fixed"}, None)
    b = generate_test_data({"fields": ["postcode"], "seed": "fixed"}, None)
    assert a["records"] == b["records"]


def test_generated_emails_can_never_reach_a_real_inbox():
    result = generate_test_data({"fields": ["email"], "rows": 20}, None)
    safe = ("example.com", "example.org", "example.net", "test.invalid", "qa.example")
    assert all(r["email"].endswith(safe) for r in result["records"])


def test_both_tools_are_strict_eligible():
    from galeqea.ai.providers.strict import is_strictable

    for name in ("generate_bdd_scenarios", "generate_test_data"):
        assert is_strictable(registry.get(name).parameters), name
