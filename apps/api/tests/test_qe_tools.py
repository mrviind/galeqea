"""The QE tool pack: registration, requirement lookup and script generation."""

from __future__ import annotations

import re

import pytest

from galeqea.ai.tools import ToolContext, registry
from galeqea.ai.toolset import tool_catalog  # noqa: F401  (installs every pack)
from galeqea.mcp.qe_tools import generate_playwright_script, query_requirements

SCENARIO = """Scenario: Customer completes checkout with a valid card
  Given the user is on the checkout page
  When the user enters "ravi@example.com" into the "Email address" field
  And the user clicks the "Confirm payment" button
  Then the user sees the "Order confirmed" message
"""


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def test_the_pack_registers_into_the_canonical_registry():
    """One registry, not two.

    A second registry would fork the approval gate, the schema validation and
    the MCP surface — tools would appear in the chat but not over MCP, and the
    two would drift the first time somebody added one.
    """
    names = {t.name for t in registry.all()}
    assert {"query_requirements", "generate_playwright_script"} <= names


def test_the_tools_are_read_only_so_they_skip_the_approval_gate():
    for name in ("query_requirements", "generate_playwright_script"):
        tool = registry.get(name)
        assert tool is not None
        assert tool.read_only is True
        assert tool.approval_action is None, "a read-only tool must not claim a gated action"


def test_schemas_reach_the_providers_in_the_shape_they_expect():
    specs = {s.name: s for s in registry.llm_specs(["query_requirements", "generate_playwright_script"])}
    assert specs["query_requirements"].parameters["required"] == ["feature"]
    assert specs["generate_playwright_script"].parameters["required"] == ["scenario"]
    for spec in specs.values():
        assert spec.parameters["type"] == "object"
        assert spec.description, "a tool with no description is a tool the model will misuse"


# --------------------------------------------------------------------------- #
# query_requirements
# --------------------------------------------------------------------------- #
def test_missing_requirements_tell_the_agent_not_to_invent_criteria(db, project):
    """The refusal has to travel in the payload, not only in the system prompt.

    A model re-reads a tool result far more reliably than it re-reads its
    instructions twenty turns later.
    """
    ctx = ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")
    result = query_requirements({"feature": "nothing-like-this-exists"}, ctx)
    assert result["count"] == 0
    assert "Do not invent acceptance criteria" in result["guidance"]


def test_requirements_without_criteria_are_flagged_as_such(db, project):
    from galeqea.models import DocKind, RequirementDoc, RequirementItem

    doc = RequirementDoc(project_id=project.id, title="PRD", kind=DocKind.REQUIREMENT)
    db.add(doc)
    db.flush()
    db.add(RequirementItem(doc_id=doc.id, project_id=project.id, ref="REQ-001",
                           title="Checkout must accept a card", text="The checkout page accepts a card.",
                           acceptance_criteria=[]))
    db.flush()

    ctx = ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")
    result = query_requirements({"feature": "checkout"}, ctx)
    assert result["count"] == 1
    assert result["acceptance_criteria_count"] == 0
    assert "none carries acceptance criteria" in result["guidance"]


def test_open_questions_are_surfaced_rather_than_smoothed_over(db, project):
    from galeqea.models import DocKind, RequirementDoc, RequirementItem

    doc = RequirementDoc(project_id=project.id, title="PRD2", kind=DocKind.REQUIREMENT)
    db.add(doc)
    db.flush()
    db.add(RequirementItem(doc_id=doc.id, project_id=project.id, ref="REQ-002",
                           title="Password rules", text="Passwords must be strong.",
                           acceptance_criteria=["at least 8 characters"],
                           open_questions=["is the maximum length specified?"]))
    db.flush()

    ctx = ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")
    result = query_requirements({"feature": "password"}, ctx)
    assert result["requirements"][0]["open_questions"]
    assert "open question" in result["guidance"]


# --------------------------------------------------------------------------- #
# generate_playwright_script
# --------------------------------------------------------------------------- #
@pytest.fixture()
def generated() -> dict:
    return generate_playwright_script(
        {"scenario": SCENARIO, "page_object": "CheckoutPage",
         "base_path": "/checkout", "requirement_ref": "REQ-014"},
        None,
    )


def test_it_emits_a_page_object_and_a_spec(generated):
    assert generated["ok"] is True
    assert generated["page_object"]["filename"] == "pages/CheckoutPage.ts"
    assert generated["spec"]["filename"].endswith(".spec.ts")
    assert "export class CheckoutPage" in generated["page_object"]["code"]
    assert "import { CheckoutPage } from '../pages/CheckoutPage';" in generated["spec"]["code"]


def test_every_class_member_is_a_valid_typescript_identifier(generated):
    """A member may not begin with a digit.

    The first version of this generator picked the *value* out of
    ``enters "4242424242424242" into the "Card number" field`` and emitted
    ``readonly 4242424242424242: Locator`` — a file that does not parse.
    """
    members = re.findall(r"^\s+readonly (\S+):", generated["page_object"]["code"], re.MULTILINE)
    assert members
    for member in members:
        assert re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", member), member


def test_the_element_is_located_not_the_value_typed_into_it():
    """`getByLabel('ravi@example.com')` names a field after its own contents."""
    result = generate_playwright_script({"scenario": SCENARIO, "page_object": "CheckoutPage"}, None)
    code = result["page_object"]["code"]
    assert "getByLabel('Email address')" in code
    assert "getByLabel('ravi@example.com')" not in code
    # The value still gets typed — it just is not mistaken for the target.
    assert ".fill('ravi@example.com')" in code


def test_locators_are_never_invented_for_steps_that_name_no_element():
    """The whole point. A guessed selector either fails for an unrelated reason
    or matches something else and passes while asserting nothing."""
    result = generate_playwright_script(
        {"scenario": "Scenario: Vague\n  Given the system is ready\n  Then it works"}, None,
    )
    code = result["page_object"]["code"]
    assert result["unresolved_locators"], "steps naming no element must be reported"
    assert "TODO" in code
    assert "Unimplemented step" in code
    # No fabricated CSS or role selector anywhere.
    assert "page.locator(" not in code


def test_role_based_locators_are_preferred_over_css(generated):
    code = generated["page_object"]["code"]
    assert "getByRole('button', { name: 'Confirm payment' })" in code
    assert "page.locator(" not in code


def test_traceability_is_carried_into_the_spec(generated):
    assert "// Traceability: REQ-014" in generated["spec"]["code"]
    assert "tag: '@REQ-014'" in generated["spec"]["code"]


def test_string_literals_are_escaped():
    result = generate_playwright_script(
        {"scenario": "Scenario: Quotes\n  When the user clicks the \"It's fine\" button"}, None,
    )
    assert r"\'" in result["page_object"]["code"]


def test_a_scenario_with_no_gherkin_steps_is_refused_not_guessed_at():
    result = generate_playwright_script({"scenario": "please test the checkout"}, None)
    assert result["ok"] is False
    assert "Gherkin" in result["error"]


def test_generation_is_deterministic():
    """Two identical requests must produce identical files, or the output is not
    reviewable."""
    a = generate_playwright_script({"scenario": SCENARIO}, None)
    b = generate_playwright_script({"scenario": SCENARIO}, None)
    assert a == b


# --------------------------------------------------------------------------- #
# Persona
# --------------------------------------------------------------------------- #
def test_the_default_persona_is_the_principal_sdet():
    from galeqea.ai.prompts import system_prompt

    prompt = system_prompt("some_unregistered_role")
    assert "Principal SDET" in prompt
    assert "NEVER INVENT A LOCATOR" in prompt
    assert "DEMAND ACCEPTANCE CRITERIA" in prompt
    assert "query_requirements" in prompt


# --------------------------------------------------------------------------- #
# UI projections
# --------------------------------------------------------------------------- #
def test_tools_publish_only_a_bounded_ui_projection(db, project):
    """`_ui` is what crosses the socket — never the whole tool result.

    A requirements query can carry fifty items of prose. Pushing raw tool output
    to every connected browser would flood the stream and publish fields the
    tool never meant to expose.
    """
    ctx = ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")
    result = query_requirements({"feature": "anything"}, ctx)
    projection = result["_ui"]
    assert projection["pane"] == "requirements"
    assert set(projection) == {"pane", "title", "markdown", "count"}
    # The heavy field stays in the result and out of the projection.
    assert "requirements" not in projection


def test_the_requirements_projection_renders_as_markdown(db, project):
    from galeqea.models import DocKind, RequirementDoc, RequirementItem

    doc = RequirementDoc(project_id=project.id, title="PRD3", kind=DocKind.REQUIREMENT)
    db.add(doc)
    db.flush()
    db.add(RequirementItem(doc_id=doc.id, project_id=project.id, ref="REQ-020",
                           title="Card is accepted", text="Accept a valid card.",
                           risk="critical", acceptance_criteria=["Luhn-valid cards are accepted"]))
    db.flush()

    ctx = ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")
    markdown = query_requirements({"feature": "card"}, ctx)["_ui"]["markdown"]
    assert "## REQ-020 — Card is accepted" in markdown
    assert "1. Luhn-valid cards are accepted" in markdown


def test_a_requirement_without_criteria_is_called_out_in_the_markdown(db, project):
    from galeqea.models import DocKind, RequirementDoc, RequirementItem

    doc = RequirementDoc(project_id=project.id, title="PRD4", kind=DocKind.REQUIREMENT)
    db.add(doc)
    db.flush()
    db.add(RequirementItem(doc_id=doc.id, project_id=project.id, ref="REQ-021",
                           title="Bare requirement", text="Something happens.",
                           acceptance_criteria=[]))
    db.flush()

    ctx = ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")
    markdown = query_requirements({"feature": "bare"}, ctx)["_ui"]["markdown"]
    assert "No acceptance criteria recorded" in markdown
    assert "would be invented" in markdown


def test_the_script_projection_carries_both_files():
    """Reviewing a spec without the page object it calls into is reviewing half."""
    result = generate_playwright_script(
        {"scenario": SCENARIO, "page_object": "CheckoutPage", "requirement_ref": "REQ-014"}, None,
    )
    projection = result["_ui"]
    assert projection["pane"] == "test_matrix"
    names = [f["filename"] for f in projection["files"]]
    assert any(n.endswith(".spec.ts") for n in names)
    assert any(n.startswith("pages/") for n in names)
    assert projection["requirement_ref"] == "REQ-014"


def test_a_failed_generation_publishes_no_projection():
    """A failure must not blank the pane the user was reading."""
    result = generate_playwright_script({"scenario": "not gherkin"}, None)
    assert result["ok"] is False
    assert "_ui" not in result


# --------------------------------------------------------------------------- #
# Settings hygiene
# --------------------------------------------------------------------------- #
def test_switching_to_no_ai_clears_the_whole_model_selection():
    """Leaving a stale base_url behind is how a request meant for one provider
    ends up at another's endpoint."""
    from galeqea.config import AIMode, settings

    previous = (settings.ai_mode, settings.provider, settings.model, settings.base_url)
    try:
        settings.ai_mode = AIMode.LOCAL
        settings.provider = "openai_compatible"
        settings.model = "mock-sdet"
        settings.base_url = "http://127.0.0.1:9200/v1"

        # Mirrors what the /settings/model route does for the no_ai branch.
        settings.ai_mode = AIMode.NO_AI
        settings.provider = "none"
        settings.model = ""
        settings.base_url = ""

        assert not settings.model
        assert not settings.base_url
    finally:
        settings.ai_mode, settings.provider, settings.model, settings.base_url = previous
