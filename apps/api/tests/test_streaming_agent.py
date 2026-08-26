"""The agent streams text deltas to the bus and still assembles tool calls whole."""

from __future__ import annotations

import asyncio

from galeqea.ai.agent import Agent
from galeqea.ai.providers.base import Completion, Delta, LLMProvider, Usage
from galeqea.ai.tools import ToolContext, ToolRegistry
from galeqea.core.events import Ev, bus


class FakeStreamer(LLMProvider):
    """Yields a preamble, a tool call, then — on the next turn — a final answer."""

    name = "fake"
    supports_streaming = True

    def __init__(self):
        super().__init__(model="fake-1")
        self.turns = 0

    async def complete(self, messages, **_):
        raise AssertionError("complete() must not be used when streaming is available")

    async def stream(self, messages, **_):
        self.turns += 1
        if self.turns == 1:
            for word in ("Let ", "me ", "check."):
                yield Delta(text=word)
            yield Delta(tool_call={"id": "c1", "name": "ping", "arguments": {}})
            yield Delta(done=True, usage=Usage(input_tokens=5, output_tokens=3))
        else:
            for word in ("All ", "good."):
                yield Delta(text=word)
            yield Delta(done=True, usage=Usage(input_tokens=6, output_tokens=2))


def _registry() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.register("ping", description="Ping. Returns ok. Used only in this test to prove the loop.",
                  parameters={"properties": {}})
    def ping(args, ctx):
        return {"ok": True, "pong": True}

    return reg


def test_deltas_are_emitted_per_chunk_and_the_completion_is_whole(db, project):
    provider = FakeStreamer()
    agent = Agent(provider=provider, registry=_registry(), role="orchestrator",
                  system_prompt="test")
    ctx = ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")

    async def run():
        queue = await bus.subscribe(project.id, replay=0)
        result = await agent.run("go", ctx, history=[])
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        await bus.unsubscribe(project.id, queue)
        return result, events

    result, events = asyncio.run(run())

    deltas = [e for e in events if e.type == Ev.CHAT_DELTA]
    assert [d.payload["text"] for d in deltas] == ["Let ", "me ", "check.", "All ", "good."]
    # Turn index lets the browser restart the draft after a tool-calling turn.
    assert [d.payload["turn"] for d in deltas] == [0, 0, 0, 1, 1]

    assert provider.turns == 2, "the tool call was executed and the model asked again"
    assert result.text == "All good."
    assert [s["tool"] for s in result.steps] == ["ping"]


def test_a_provider_without_streaming_falls_back_to_complete(db, project):
    class Blocking(LLMProvider):
        name = "blocking"
        supports_streaming = False

        def __init__(self):
            super().__init__(model="b")

        async def complete(self, messages, **_):
            return Completion(text="done", usage=Usage())

        async def stream(self, messages, **_):
            raise AssertionError("must not stream")
            yield  # pragma: no cover

    agent = Agent(provider=Blocking(), registry=_registry(), role="orchestrator", system_prompt="t")
    ctx = ToolContext(db=db, project_id=project.id, user=None, actor_kind="agent")
    result = asyncio.run(agent.run("go", ctx, history=[]))
    assert result.text == "done"
