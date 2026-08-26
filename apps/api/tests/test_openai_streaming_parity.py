"""OpenAI-compatible streaming: tool-call reassembly and is_error parity.

The real failure mode this exercises: providers stream a tool call in fragments —
the name in one chunk, the JSON arguments split across several, the id sometimes
only in the first fragment. A parser that assumes one chunk per call silently
loses arguments and calls the tool with `{}`. These fragment deliberately.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from galeqea.ai.providers.base import Message, Role
from galeqea.ai.providers.openai_compat import OpenAICompatibleProvider


class _FakeStreamResponse:
    """Minimal stand-in for httpx's streaming response context manager."""

    def __init__(self, lines: list[str]):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


def _provider_with_stream(lines: list[str]) -> OpenAICompatibleProvider:
    p = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    # Minimal fields the stream() method touches.
    p.model = "fake"
    p.base_url = "http://fake"
    p.flavor = "openai"
    p._client = SimpleNamespace(stream=lambda *a, **k: _FakeStreamResponse(lines))
    p._headers = lambda: {}
    p._endpoint = lambda path: f"http://fake{path}"
    p._tools_to_wire = lambda tools: []
    p._to_wire = lambda messages, system: []
    p._usage = OpenAICompatibleProvider._usage.__get__(p)
    return p


def _sse(obj) -> str:
    return "data: " + json.dumps(obj)


async def _collect(provider):
    text, tool_calls, usage = "", [], None
    async for delta in provider.stream([Message(role=Role.USER, content="x")]):
        if delta.text:
            text += delta.text
        if delta.tool_call:
            tool_calls.append(delta.tool_call)
        if delta.done:
            usage = delta.usage
    return text, tool_calls, usage


def test_arguments_fragmented_across_chunks_are_reassembled():
    """The name arrives once; the JSON arguments arrive in four pieces."""
    lines = [
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_abc", "function": {"name": "query_requirements"}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"fea'}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'ture": "che'}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'ckout"'}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '}'}}]}}]}),
        _sse({"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}),
        "data: [DONE]",
    ]
    import asyncio
    _, calls, usage = asyncio.run(_collect(_provider_with_stream(lines)))
    assert len(calls) == 1
    assert calls[0]["id"] == "call_abc"
    assert calls[0]["name"] == "query_requirements"
    assert calls[0]["arguments"] == {"feature": "checkout"}
    assert usage.output_tokens == 3


def test_two_parallel_tool_calls_are_kept_separate_by_index():
    lines = [
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c0", "function": {"name": "a", "arguments": '{"x":1}'}},
            {"index": 1, "id": "c1", "function": {"name": "b", "arguments": '{"y":'}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [{"index": 1, "function": {"arguments": '2}'}}]}}]}),
        "data: [DONE]",
    ]
    import asyncio
    _, calls, _ = asyncio.run(_collect(_provider_with_stream(lines)))
    by_name = {c["name"]: c for c in calls}
    assert by_name["a"]["arguments"] == {"x": 1}
    assert by_name["b"]["arguments"] == {"y": 2}
    assert by_name["a"]["id"] == "c0" and by_name["b"]["id"] == "c1"


def test_a_late_id_replaces_the_synthetic_fallback():
    """Some providers send the id a fragment after the first."""
    lines = [
        _sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "t"}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "real_id", "function": {"arguments": "{}"}}]}}]}),
        "data: [DONE]",
    ]
    import asyncio
    _, calls, _ = asyncio.run(_collect(_provider_with_stream(lines)))
    assert calls[0]["id"] == "real_id"


def test_text_preamble_then_a_tool_call_both_arrive():
    lines = [
        _sse({"choices": [{"delta": {"content": "Let me "}}]}),
        _sse({"choices": [{"delta": {"content": "check."}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c", "function": {"name": "t", "arguments": "{}"}}]}}]}),
        "data: [DONE]",
    ]
    import asyncio
    text, calls, _ = asyncio.run(_collect(_provider_with_stream(lines)))
    assert text == "Let me check."
    assert calls[0]["name"] == "t"


def test_a_fragment_that_never_names_a_tool_is_dropped():
    """A malformed stream must not emit a call with an empty name — it would 400."""
    lines = [
        _sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}}]}),
        "data: [DONE]",
    ]
    import asyncio
    _, calls, _ = asyncio.run(_collect(_provider_with_stream(lines)))
    assert calls == []


def test_malformed_argument_json_degrades_to_unparsed_not_a_crash():
    lines = [
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c", "function": {"name": "t", "arguments": "{not json"}}]}}]}),
        "data: [DONE]",
    ]
    import asyncio
    _, calls, _ = asyncio.run(_collect(_provider_with_stream(lines)))
    assert calls[0]["arguments"] == {"_unparsed": "{not json"}


# --------------------------------------------------------------------------- #
# is_error parity with the Anthropic adapter
# --------------------------------------------------------------------------- #
def test_a_failed_tool_result_is_marked_for_the_model():
    wire = OpenAICompatibleProvider._to_wire(
        [Message(role=Role.TOOL, tool_call_id="c", content='{"ok": false}', is_error=True)], "")
    assert wire[0]["content"].startswith("[tool error] ")


def test_a_successful_tool_result_is_not_marked():
    wire = OpenAICompatibleProvider._to_wire(
        [Message(role=Role.TOOL, tool_call_id="c", content='{"ok": true}')], "")
    assert wire[0]["content"] == '{"ok": true}'


def test_the_error_marker_is_not_doubled():
    wire = OpenAICompatibleProvider._to_wire(
        [Message(role=Role.TOOL, tool_call_id="c", content="[tool error] already", is_error=True)], "")
    assert wire[0]["content"].count("[tool error]") == 1
