"""Tests for the first-class scripted MockProvider."""

from __future__ import annotations

import pytest

from agent_orchestrator.core.provider import Completion, Message, Role, ToolCall, Usage
from agent_orchestrator.providers import MockProvider


class TestMockProvider:
    @pytest.mark.asyncio
    async def test_default_script_returns_ok(self):
        provider = MockProvider()
        completion = await provider.complete([Message(Role.USER, "hi")])
        assert completion.content == "ok"
        assert completion.usage.cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_script_replays_in_order_then_holds_last(self):
        provider = MockProvider(["one", "two"])
        assert (await provider.complete([])).content == "one"
        assert (await provider.complete([])).content == "two"
        assert (await provider.complete([])).content == "two"
        assert provider.call_count == 3

    @pytest.mark.asyncio
    async def test_completion_entries_pass_through(self):
        scripted = Completion(
            content="",
            tool_calls=[ToolCall(id="1", name="search", arguments={"q": "x"})],
            usage=Usage(10, 5, 0.0),
            stop_reason="tool_use",
        )
        provider = MockProvider([scripted])
        completion = await provider.complete([])
        assert completion.tool_calls[0].name == "search"
        assert completion.stop_reason == "tool_use"

    @pytest.mark.asyncio
    async def test_calls_are_captured_for_assertions(self):
        provider = MockProvider(["ok"])
        await provider.complete([Message(Role.USER, "question")], system="be brief", max_tokens=99)
        assert len(provider.calls) == 1
        assert provider.calls[0]["system"] == "be brief"
        assert provider.calls[0]["max_tokens"] == 99
        assert provider.calls[0]["messages"][0].content == "question"

    @pytest.mark.asyncio
    async def test_stream_yields_content_and_final(self):
        provider = MockProvider(["hello world"])
        chunks = [c async for c in provider.stream([Message(Role.USER, "hi")])]
        assert chunks[-1].is_final
        text = "".join(c.content for c in chunks)
        assert "hello" in text and "world" in text

    @pytest.mark.asyncio
    async def test_stream_yields_scripted_tool_calls(self):
        scripted = Completion(
            content="calling",
            tool_calls=[ToolCall(id="1", name="grep", arguments={})],
        )
        provider = MockProvider([scripted])
        chunks = [c async for c in provider.stream([])]
        tool_calls = [c.tool_call for c in chunks if c.tool_call is not None]
        assert len(tool_calls) == 1
        assert tool_calls[0].name == "grep"

    def test_empty_script_rejected(self):
        with pytest.raises(ValueError):
            MockProvider([])

    def test_zero_cost_and_metadata(self):
        provider = MockProvider(model_id="mock-test", max_context=1234)
        assert provider.model_id == "mock-test"
        assert provider.capabilities.max_context == 1234
        assert provider.input_cost_per_million == 0.0
        assert provider.output_cost_per_million == 0.0
        assert provider.estimate_cost(1000, 1000) == 0.0
