"""Bring-your-own-key storage, and the classical test design techniques.

The BYOK tests exist because the first implementation was a facade: the key was
set on an in-memory settings object, so it survived until the next restart and
applied to every project at once.
"""

from __future__ import annotations

import pytest

from galeqea.ai import keys
from galeqea.intelligence.testdesign import analyse, summarise


# --------------------------------------------------------------------------- #
# Boundary value analysis
# --------------------------------------------------------------------------- #
def test_a_stated_range_produces_both_sides_of_both_boundaries():
    """Off-by-one is the defect this finds, so the neighbours matter most."""
    analysis = analyse("The password must be between 8 and 64 characters.")
    numeric = [v for v in analysis.variables if v.kind == "numeric"][0]
    assert (numeric.minimum, numeric.maximum) == (8.0, 64.0)

    boundary = {v.value: v.partition for v in analysis.values if v.technique == "boundary_value"}
    assert boundary["7"] == "invalid"
    assert boundary["8"] == "valid"
    assert boundary["9"] == "valid"
    assert boundary["63"] == "valid"
    assert boundary["64"] == "valid"
    assert boundary["65"] == "invalid"


def test_a_rejection_verb_inverts_the_threshold():
    """"reject an upload of more than 5 MB" states a maximum, not a minimum.

    Reading the comparative alone gets this backwards and then puts every
    boundary value on the wrong side of the limit.
    """
    analysis = analyse("The system shall reject an upload of more than 5 MB.")
    numeric = [v for v in analysis.variables if v.kind == "numeric"][0]
    assert numeric.maximum == 5.0
    assert numeric.minimum is None

    partitions = {v.value: v.partition for v in analysis.values}
    assert partitions["5"] == "valid"
    assert partitions["6"] == "invalid"


def test_a_double_negative_resolves_correctly():
    analysis = analyse("The system must not accept a password of fewer than 8 characters.")
    numeric = [v for v in analysis.variables if v.kind == "numeric"][0]
    assert numeric.minimum == 8.0


def test_boundary_values_are_computed_not_guessed():
    """Arithmetic is not delegated to a language model."""
    analysis = analyse("Upload at most 5 files.")
    values = {v.value for v in analysis.values if v.technique == "boundary_value"}
    assert {"4", "5", "6"} <= values


# --------------------------------------------------------------------------- #
# Equivalence partitioning
# --------------------------------------------------------------------------- #
def test_an_enumeration_yields_every_member_plus_one_outsider():
    analysis = analyse("The order status must be one of Draft, Submitted or Approved.")
    enum = [v for v in analysis.variables if v.kind == "enum"][0]
    assert enum.values == ["Draft", "Submitted", "Approved"]

    valid = {v.value for v in analysis.values if v.partition == "valid"}
    assert valid == {"Draft", "Submitted", "Approved"}
    assert any(v.partition == "invalid" for v in analysis.values)


def test_unstated_behaviour_is_marked_unspecified_not_invalid():
    """The requirement never said whether matching is case sensitive.

    Asserting a verdict the specification did not give bakes a guess into a
    test, and the guess then reads as agreed behaviour forever after.
    """
    analysis = analyse("The order status must be one of Draft, Submitted or Approved.")
    unspecified = [v for v in analysis.values if v.partition == "unspecified"]
    assert unspecified
    assert "case sensitive" in unspecified[0].expected


def test_a_format_constraint_yields_realistic_edge_cases():
    analysis = analyse("The user must provide a valid email address.")
    values = {v.value for v in analysis.values}
    assert "user@example.com" in values
    assert "user@" in values          # missing domain
    assert "@example.com" in values   # missing local part


# --------------------------------------------------------------------------- #
# Decision tables
# --------------------------------------------------------------------------- #
def test_combining_conditions_produces_a_decision_table():
    analysis = analyse(
        "If the user is signed in and has a saved card and the basket is not empty, "
        "the express checkout button is shown."
    )
    assert len(analysis.conditions) == 3
    assert len(analysis.decision_table) == 8          # 2^3
    all_true = [r for r in analysis.decision_table if all(r.conditions.values())]
    assert len(all_true) == 1


def test_a_single_condition_needs_no_table():
    assert analyse("If the user is signed in, show the dashboard.").decision_table == []


def test_summary_names_the_techniques_used():
    text = summarise(analyse("The password must be between 8 and 64 characters."))
    assert "boundary value" in text
    assert "equivalence partition" in text


def test_a_requirement_with_no_stated_domain_says_so():
    """Silence is reported rather than filled in with invented limits."""
    analysis = analyse("The system should be easy to use.")
    assert analysis.values == []
    assert any("nothing to work from" in note for note in analysis.notes)


# --------------------------------------------------------------------------- #
# Bring your own key
# --------------------------------------------------------------------------- #
def test_a_stored_key_round_trips_and_is_never_returned_in_the_clear(db, project):
    credential = keys.store(
        db, provider="openai_compatible", api_key="sk-local-secret-value",
        scope=keys.GLOBAL_SCOPE, model="local-model", base_url="http://localhost:8799",
    )
    db.commit()

    assert "secret-value" not in str(credential.as_dict())
    assert credential.hint.endswith("alue")
    assert keys.resolve(db, provider="openai_compatible", project_id=None) == "sk-local-secret-value"


def test_a_project_key_overrides_the_global_one(db, project):
    keys.store(db, provider="openai_compatible", api_key="global-key",
               scope=keys.GLOBAL_SCOPE, model="global-model")
    keys.store(db, provider="openai_compatible", api_key="project-key",
               scope=project.id, model="project-model")
    db.commit()

    assert keys.resolve(db, provider="openai_compatible", project_id=project.id) == "project-key"
    assert keys.resolve(db, provider="openai_compatible", project_id=None) == "global-key"
    assert keys.config_for(db, provider="openai_compatible", project_id=project.id)["model"] == "project-model"


def test_revoking_a_project_key_falls_back_to_global(db, project):
    keys.store(db, provider="openai_compatible", api_key="global-key", scope=keys.GLOBAL_SCOPE)
    keys.store(db, provider="openai_compatible", api_key="project-key", scope=project.id)
    db.commit()

    assert keys.revoke(db, provider="openai_compatible", scope=project.id)
    db.commit()
    assert keys.resolve(db, provider="openai_compatible", project_id=project.id) == "global-key"


def test_a_missing_key_resolves_to_none_rather_than_raising(db, project):
    assert keys.resolve(db, provider="gemini", project_id=project.id) is None


def test_the_budget_blocks_before_the_request_not_after(db, project):
    """A budget you discover you have exceeded is a bill, not a budget."""
    from galeqea.models import UsageLedger

    keys.store(db, provider="openai_compatible", api_key="k",
               scope=keys.GLOBAL_SCOPE, monthly_budget_usd=2.0)
    db.commit()
    keys.check_budget(db, provider="openai_compatible", project_id=None)   # under

    db.add(UsageLedger(provider="openai_compatible", operation="agent_run", cost_usd=2.5))
    db.commit()

    with pytest.raises(keys.KeyError_, match="budget"):
        keys.check_budget(db, provider="openai_compatible", project_id=None)


def test_an_exhausted_budget_degrades_with_the_real_reason(db, project):
    """Not "no model configured" — that sends the user to the wrong setting."""
    from galeqea.ai.providers.registry import for_project
    from galeqea.config import AIMode, settings
    from galeqea.models import UsageLedger

    keys.store(db, provider="openai_compatible", api_key="k",
               scope=keys.GLOBAL_SCOPE, monthly_budget_usd=1.0)
    db.add(UsageLedger(provider="openai_compatible", operation="agent_run", cost_usd=9.0))
    db.commit()

    previous_mode, previous_provider = settings.ai_mode, settings.provider
    settings.ai_mode, settings.provider = AIMode.API_KEY, "openai_compatible"
    try:
        provider = for_project(db, None)
        assert "budget" in provider.reason
    finally:
        settings.ai_mode, settings.provider = previous_mode, previous_provider
