"""Conformance to Anthropic's tool-use guidance and the MCP tool specification.

These are not unit tests of behaviour; they are guardrails on the *contract*.
Each one encodes a published rule, cites it, and fails when a future tool
quietly breaks it — which is the failure mode that otherwise shows up months
later as "the model keeps picking the wrong tool" with no obvious cause.
"""

from __future__ import annotations

import json
import re

import pytest

from galeqea.ai.agent import MAX_TOOL_RESULT_CHARS, _tool_result_for_model
from galeqea.ai.providers.base import Message, Role
from galeqea.ai.tools import registry
from galeqea.ai.toolset import tool_catalog  # noqa: F401  (installs every pack)

ALL_TOOLS = sorted(registry.all(), key=lambda t: t.name)
SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _sentences(text: str) -> int:
    return len([s for s in SENTENCE.split(text.strip()) if len(s.strip()) > 12])


# --------------------------------------------------------------------------- #
# Anthropic: define tools
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_every_description_meets_the_documented_floor(tool):
    """"Aim for at least 3-4 sentences for each tool description, more if the
    tool is complex." Description quality is documented as "by far the most
    important factor in tool performance"."""
    assert _sentences(tool.description) >= 3, (
        f"{tool.name} has {_sentences(tool.description)} sentence(s): "
        f"{tool.description!r}"
    )


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_tool_names_match_the_api_pattern(tool):
    """`name` must match ^[a-zA-Z0-9_-]{1,64}$ or the request is rejected."""
    assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", tool.name)


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_every_parameter_is_documented(tool):
    """"What each parameter means and how it affects the tool's behaviour."

    An undescribed parameter is one the model fills in by guessing at its name.
    """
    for field, spec in (tool.parameters.get("properties") or {}).items():
        assert spec.get("description") or spec.get("enum"), (
            f"{tool.name}.{field} has neither a description nor an enum"
        )


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_schemas_are_well_formed_objects(tool):
    assert tool.parameters.get("type") == "object"
    assert isinstance(tool.parameters.get("properties"), dict)
    for required in tool.parameters.get("required", []):
        assert required in tool.parameters["properties"], (
            f"{tool.name} requires {required!r} but never declares it"
        )


# --------------------------------------------------------------------------- #
# Anthropic: handle tool calls
# --------------------------------------------------------------------------- #
def test_a_failed_tool_reaches_the_model_flagged_as_an_error():
    """"Set to true if the tool execution resulted in an error."

    Without the flag a failure is a successful call that happened to return the
    word "error", and the model has to infer failure from JSON it may not read
    carefully.
    """
    from galeqea.ai.providers.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider.__new__(AnthropicProvider)   # no network, no key
    wire = provider._to_sdk_messages(
        [Message(role=Role.TOOL, tool_call_id="toolu_1", name="x",
                 content='{"ok": false}', is_error=True)]
    )
    block = wire[0]["content"][0]
    assert block["type"] == "tool_result"
    assert block["is_error"] is True


def test_a_successful_tool_is_not_flagged():
    from galeqea.ai.providers.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider.__new__(AnthropicProvider)
    wire = provider._to_sdk_messages(
        [Message(role=Role.TOOL, tool_call_id="toolu_1", name="x", content='{"ok": true}')]
    )
    assert "is_error" not in wire[0]["content"][0]


# --------------------------------------------------------------------------- #
# Anthropic: response shaping
# --------------------------------------------------------------------------- #
def test_browser_only_payload_is_kept_out_of_the_model_context():
    """"Bloated responses waste context and make it harder for Claude to extract
    what matters."

    `_ui` exists to drive the workspace panes and duplicates data already in the
    result — for a generated script, a second full copy of both files.
    """
    result = {
        "ok": True,
        "spec": {"code": "const a = 1;"},
        "_ui": {"pane": "test_matrix", "files": [{"code": "const a = 1;"}] * 4},
    }
    encoded = _tool_result_for_model(result)
    assert "_ui" not in encoded
    assert "pane" not in encoded
    assert json.loads(encoded)["spec"]["code"] == "const a = 1;"


def test_an_oversized_result_is_replaced_not_sliced():
    """Slicing a JSON string at a fixed offset cuts mid-structure and hands the
    model malformed JSON to guess at."""
    payload = _tool_result_for_model(
        {"ok": True, "guidance": "narrow the query", "blob": "x" * (MAX_TOOL_RESULT_CHARS * 2)}
    )
    decoded = json.loads(payload)          # must still parse
    assert decoded["truncated"] is True
    assert decoded["guidance"] == "narrow the query", "the tool's next-step hint must survive"
    assert "narrower query" in decoded["note"], "the note must say how to recover"


def test_a_normal_result_passes_through_unchanged():
    payload = json.loads(_tool_result_for_model({"ok": True, "count": 2}))
    assert payload == {"ok": True, "count": 2}


# --------------------------------------------------------------------------- #
# MCP specification
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_mcp_definitions_carry_the_required_fields(tool):
    definition = tool.as_mcp()
    assert definition["name"] == tool.name
    assert definition["title"], "MCP `title` is what a consent prompt shows a human"
    assert definition["description"]
    assert definition["inputSchema"]["type"] == "object"


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_annotations_are_present_and_internally_consistent(tool):
    """Clients decide what to confirm from these, so they must not contradict
    the tool's own behaviour."""
    annotations = tool.as_mcp()["annotations"]
    assert set(annotations) == {
        "readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint",
    }
    if annotations["destructiveHint"]:
        assert not annotations["readOnlyHint"], (
            f"{tool.name} claims to be both read-only and destructive"
        )


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_state_changing_tools_require_confirmation(tool):
    """The gate and the annotation must agree. A tool that files an approval but
    advertises itself as read-only would be auto-invoked by a client that trusts
    the hint."""
    if tool.approval_action:
        assert tool.requires_confirmation
        assert not tool.read_only, f"{tool.name} is gated but advertises readOnlyHint"


def test_a_declared_output_schema_is_honoured_by_the_result():
    """"Servers MUST provide structured results that conform to this schema."""
    from galeqea.mcp.qe_tools import generate_playwright_script

    tool = registry.get("generate_playwright_script")
    assert tool.output_schema is not None

    result = generate_playwright_script(
        {"scenario": 'Scenario: S\n  When the user clicks the "Go" button\n'
                     '  Then the user sees the "Done" message'},
        None,
    )
    for field in tool.output_schema["required"]:
        assert field in result, f"declared as required but absent: {field}"
    for name in ("page_object", "spec"):
        for field in tool.output_schema["$defs"]["file"]["required"]:
            assert field in result[name]


def test_the_mcp_boundary_strips_ui_payload():
    """`_ui` is GaleQEA's own workspace concern and means nothing to an external
    client consuming this server."""
    from galeqea.mcp_server import server  # noqa: F401  (import proves it loads)

    source = (server.__file__ or "")
    assert source
    with open(source) as handle:
        body = handle.read()
    assert 'k != "_ui"' in body, "the MCP tool-call response must drop _ui"
