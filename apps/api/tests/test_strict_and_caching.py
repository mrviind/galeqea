"""Strict tool use and prompt caching on the Anthropic adapter.

Every rule here comes from the strict-tool-use and structured-outputs pages.
"""

from __future__ import annotations

import pytest

from galeqea.ai.providers.anthropic_provider import AnthropicProvider
from galeqea.ai.providers.base import ToolSpec
from galeqea.ai.providers.strict import NotStrictable, is_strictable, to_strict
from galeqea.ai.tools import registry
from galeqea.ai.toolset import tool_catalog  # noqa: F401


def test_every_object_is_closed():
    """additionalProperties must be false on every object, including nested."""
    out = to_strict({"type": "object", "properties": {
        "a": {"type": "object", "properties": {"b": {"type": "string"}}}}})
    assert out["additionalProperties"] is False
    assert out["properties"]["a"]["additionalProperties"] is False


def test_unsupported_constraints_are_folded_into_the_description_not_dropped_silently():
    out = to_strict({"type": "object", "properties": {
        "n": {"type": "integer", "minimum": 1, "maximum": 9, "description": "Count."}}})
    prop = out["properties"]["n"]
    assert "minimum" not in prop and "maximum" not in prop
    assert "at least 1" in prop["description"] and "at most 9" in prop["description"]


def test_the_original_schema_is_never_mutated():
    """The registry validates against the original; only the wire copy simplifies."""
    original = {"type": "object", "properties": {"s": {"type": "string", "maxLength": 5}}}
    to_strict(original)
    assert original["properties"]["s"]["maxLength"] == 5
    assert "additionalProperties" not in original


def test_a_free_form_object_is_refused_rather_than_silently_closed():
    """Closing an open object changes what the tool accepts."""
    with pytest.raises(NotStrictable):
        to_strict({"type": "object", "properties": {
            "bag": {"type": "object", "additionalProperties": True}}})


def test_external_refs_are_refused():
    with pytest.raises(NotStrictable):
        to_strict({"type": "object", "properties": {"x": {"$ref": "https://evil.example/s"}}})


def test_min_items_above_one_is_folded():
    out = to_strict({"type": "object", "properties": {
        "xs": {"type": "array", "items": {"type": "string"}, "minItems": 3}}})
    assert out["properties"]["xs"]["minItems"] == 1
    assert "at least 3 items" in out["properties"]["xs"]["description"]


def test_unsupported_string_formats_are_folded():
    out = to_strict({"type": "object", "properties": {"c": {"type": "string", "format": "color"}}})
    assert "format" not in out["properties"]["c"]
    assert "format: color" in out["properties"]["c"]["description"]
    keep = to_strict({"type": "object", "properties": {"e": {"type": "string", "format": "email"}}})
    assert keep["properties"]["e"]["format"] == "email"


# --------------------------------------------------------------------------- #
# Adapter wiring
# --------------------------------------------------------------------------- #
def test_strict_is_set_exactly_where_the_schema_allows():
    wire = AnthropicProvider._to_sdk_tools(registry.llm_specs())
    by_name = {w["name"]: w for w in wire}
    for tool in registry.all():
        assert by_name[tool.name].get("strict", False) is is_strictable(tool.parameters), tool.name
    # The only non-strict tools are the ones with a genuinely free-form object in
    # their contract: create_test/update_test carry an open step bag, and
    # review_test accepts an arbitrary proposal object. Closing any of those would
    # change what the tool accepts, so they validate locally instead.
    # (The per-tool strict check at the top of this test already asserts, via
    # is_strictable, that every tool is marked correctly — including new free-form
    # tools — so no hard-coded name set is maintained here.)


def test_the_last_tool_carries_the_cache_breakpoint_and_only_the_last():
    wire = AnthropicProvider._to_sdk_tools(registry.llm_specs())
    assert wire[-1]["cache_control"] == {"type": "ephemeral"}
    assert all("cache_control" not in w for w in wire[:-1])


def test_no_tools_means_no_breakpoint():
    assert AnthropicProvider._to_sdk_tools([]) == []


def test_the_system_prompt_is_a_cached_block():
    blocks = AnthropicProvider._system_blocks("persona")
    assert blocks == [{"type": "text", "text": "persona", "cache_control": {"type": "ephemeral"}}]


def test_input_examples_reach_the_wire_only_when_declared():
    spec = ToolSpec(name="t", description="d", parameters={"type": "object", "properties": {}},
                    input_examples=[{}])
    assert AnthropicProvider._to_sdk_tools([spec])[0]["input_examples"] == [{}]
    bare = ToolSpec(name="t", description="d", parameters={"type": "object", "properties": {}})
    assert "input_examples" not in AnthropicProvider._to_sdk_tools([bare])[0]
