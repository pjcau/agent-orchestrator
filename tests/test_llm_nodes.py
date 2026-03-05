"""Tests for LLM node factories."""

import pytest

from agent_orchestrator.core.graph import END, START, StateGraph
from agent_orchestrator.core.llm_nodes import llm_node, multi_provider_node, chat_node
from agent_orchestrator.core.provider import (
    Completion,
    ModelCapabilities,
    Provider,
    StreamChunk,
    Usage,
)
from agent_orchestrator.core.reducers import append_reducer


# --- Mock Provider ---


class MockProvider(Provider):
    """A mock provider for testing LLM nodes."""

    def __init__(self, response: str = "mock response", model: str = "mock-v1", fail: bool = False):
        self._response = response
        self._model = model
        self._fail = fail

    async def complete(self, messages, tools=None, system=None, max_tokens=4096, temperature=0.0):
        if self._fail:
            raise ConnectionError("Provider unavailable")
        prompt_text = messages[-1].content if messages else ""
        return Completion(
            content=f"{self._response}: {prompt_text}",
            usage=Usage(input_tokens=10, output_tokens=20, cost_usd=0.001),
        )

    async def stream(self, messages, tools=None, system=None, max_tokens=4096):
        yield StreamChunk(content=self._response, is_final=True)

    @property
    def model_id(self):
        return self._model

    @property
    def capabilities(self):
        return ModelCapabilities(max_context=100000)

    @property
    def input_cost_per_million(self):
        return 3.0

    @property
    def output_cost_per_million(self):
        return 15.0


class TestLLMNode:
    @pytest.mark.asyncio
    async def test_basic_llm_node(self):
        provider = MockProvider("analyzed")
        node = llm_node(
            provider=provider, system="You analyze.", prompt_key="input", output_key="analysis"
        )

        g = StateGraph()
        g.add_node("analyze", node)
        g.add_edge(START, "analyze")
        g.add_edge("analyze", END)

        result = await g.compile().invoke({"input": "test data"})
        assert result.success
        assert "analyzed" in result.state["analysis"]
        assert "test data" in result.state["analysis"]
        assert result.state["_usage"]["provider"] == "mock-v1"
        assert result.state["_usage"]["cost_usd"] == 0.001

    @pytest.mark.asyncio
    async def test_llm_node_with_template_string(self):
        provider = MockProvider("result")
        node = llm_node(
            provider=provider,
            system="Summarize.",
            prompt_template="Summarize this: {text} (max {max_words} words)",
            output_key="summary",
        )

        g = StateGraph()
        g.add_node("summarize", node)
        g.add_edge(START, "summarize")
        g.add_edge("summarize", END)

        result = await g.compile().invoke({"text": "long article", "max_words": 100})
        assert result.success
        assert "long article" in result.state["summary"]
        assert "100" in result.state["summary"]

    @pytest.mark.asyncio
    async def test_llm_node_with_callable_template(self):
        provider = MockProvider("done")

        def build_prompt(state):
            items = state.get("items", [])
            return f"Process {len(items)} items: {', '.join(items)}"

        node = llm_node(
            provider=provider,
            system="Processor.",
            prompt_template=build_prompt,
            output_key="result",
        )

        g = StateGraph()
        g.add_node("process", node)
        g.add_edge(START, "process")
        g.add_edge("process", END)

        result = await g.compile().invoke({"items": ["a", "b", "c"]})
        assert result.success
        assert "3 items" in result.state["result"]

    @pytest.mark.asyncio
    async def test_llm_node_no_usage_tracking(self):
        provider = MockProvider("resp")
        node = llm_node(
            provider=provider,
            system="Test.",
            output_key="out",
            track_usage=False,
        )

        g = StateGraph()
        g.add_node("n", node)
        g.add_edge(START, "n")
        g.add_edge("n", END)

        result = await g.compile().invoke({"input": "hi"})
        assert result.success
        assert "_usage" not in result.state


class TestMultiProviderNode:
    @pytest.mark.asyncio
    async def test_fallback_to_second_provider(self):
        failing = MockProvider(fail=True, model="primary")
        backup = MockProvider("backup response", model="backup")

        node = multi_provider_node(
            providers=[failing, backup],
            system="Analyze.",
            output_key="result",
        )

        g = StateGraph()
        g.add_node("analyze", node)
        g.add_edge(START, "analyze")
        g.add_edge("analyze", END)

        result = await g.compile().invoke({"input": "data"})
        assert result.success
        assert "backup response" in result.state["result"]
        assert result.state["_provider_used"] == "backup"

    @pytest.mark.asyncio
    async def test_all_providers_fail(self):
        fail1 = MockProvider(fail=True, model="p1")
        fail2 = MockProvider(fail=True, model="p2")

        node = multi_provider_node(
            providers=[fail1, fail2],
            system="Test.",
            output_key="result",
        )

        g = StateGraph()
        g.add_node("n", node)
        g.add_edge(START, "n")
        g.add_edge("n", END)

        result = await g.compile().invoke({"input": "data"})
        assert not result.success
        assert "All 2 providers failed" in result.error

    @pytest.mark.asyncio
    async def test_first_provider_succeeds(self):
        primary = MockProvider("fast response", model="primary")
        backup = MockProvider("slow response", model="backup")

        node = multi_provider_node(
            providers=[primary, backup],
            system="Test.",
            output_key="result",
        )

        g = StateGraph()
        g.add_node("n", node)
        g.add_edge(START, "n")
        g.add_edge("n", END)

        result = await g.compile().invoke({"input": "data"})
        assert result.success
        assert result.state["_provider_used"] == "primary"


class TestChatNode:
    @pytest.mark.asyncio
    async def test_chat_node_appends_response(self):
        provider = MockProvider("Hello back!")
        node = chat_node(provider=provider, system="You are helpful.")

        g = StateGraph(reducers={"messages": append_reducer})
        g.add_node("chat", node)
        g.add_edge(START, "chat")
        g.add_edge("chat", END)

        result = await g.compile().invoke({"messages": [{"role": "user", "content": "Hi"}]})
        assert result.success
        msgs = result.state["messages"]
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_chat_node_with_string_messages(self):
        provider = MockProvider("Got it")
        node = chat_node(provider=provider, system="Echo bot.")

        g = StateGraph(reducers={"messages": append_reducer})
        g.add_node("chat", node)
        g.add_edge(START, "chat")
        g.add_edge("chat", END)

        result = await g.compile().invoke({"messages": ["Hello"]})
        assert result.success
        assert len(result.state["messages"]) == 2
