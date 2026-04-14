"""Tests for the GoogleProvider — mock the google-generativeai SDK."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_orchestrator.core.provider import (
    Message,
    Role,
    ToolCall,
    ToolDefinition,
)
from agent_orchestrator.providers.google import GoogleProvider


def _make_text_response(text: str, input_tokens: int = 10, output_tokens: int = 5):
    part = SimpleNamespace(text=text, function_call=None)
    content = SimpleNamespace(parts=[part])
    candidate = SimpleNamespace(content=content)
    usage = SimpleNamespace(prompt_token_count=input_tokens, candidates_token_count=output_tokens)
    return SimpleNamespace(candidates=[candidate], usage_metadata=usage)


def _make_tool_response(name: str, args: dict):
    fc = SimpleNamespace(name=name, args=args)
    part = SimpleNamespace(text=None, function_call=fc)
    content = SimpleNamespace(parts=[part])
    candidate = SimpleNamespace(content=content)
    usage = SimpleNamespace(prompt_token_count=8, candidates_token_count=4)
    return SimpleNamespace(candidates=[candidate], usage_metadata=usage)


@pytest.fixture
def fake_genai(monkeypatch):
    """Install a fake ``google.generativeai`` module in sys.modules."""
    fake_model = MagicMock()
    module = types.ModuleType("google.generativeai")
    module.configure = MagicMock()
    module.GenerativeModel = MagicMock(return_value=fake_model)

    google_pkg = types.ModuleType("google")
    google_pkg.generativeai = module

    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.generativeai", module)
    return module, fake_model


def test_capabilities_and_costs():
    provider = GoogleProvider(model="gemini-2.0-flash", api_key="test")
    caps = provider.capabilities
    assert caps.max_context == 1_000_000
    assert caps.supports_tools is True
    assert caps.supports_streaming is True
    assert provider.input_cost_per_million == 0.075
    assert provider.output_cost_per_million == 0.30
    assert provider.model_id == "gemini-2.0-flash"


def test_unknown_model_falls_back():
    provider = GoogleProvider(model="gemini-custom-xyz", api_key="test")
    # unknown model still returns capabilities (default row)
    assert provider.capabilities.max_context > 0


@pytest.mark.asyncio
async def test_complete_text(fake_genai):
    module, fake_model = fake_genai
    fake_model.generate_content_async = AsyncMock(
        return_value=_make_text_response("hello world", 10, 5)
    )

    provider = GoogleProvider(model="gemini-2.0-flash", api_key="test")
    result = await provider.complete(
        [Message(role=Role.USER, content="hi")],
        system="be nice",
        max_tokens=128,
        temperature=0.2,
    )

    assert result.content == "hello world"
    assert result.tool_calls == []
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 5
    assert result.usage.cost_usd > 0
    assert result.stop_reason == "end_turn"

    module.GenerativeModel.assert_called_once()
    kwargs = module.GenerativeModel.call_args.kwargs
    assert kwargs["model_name"] == "gemini-2.0-flash"
    assert kwargs["system_instruction"] == "be nice"

    call_kwargs = fake_model.generate_content_async.call_args.kwargs
    assert call_kwargs["generation_config"]["max_output_tokens"] == 128
    assert call_kwargs["generation_config"]["temperature"] == 0.2


@pytest.mark.asyncio
async def test_complete_with_tool_call(fake_genai):
    _, fake_model = fake_genai
    fake_model.generate_content_async = AsyncMock(
        return_value=_make_tool_response("search", {"q": "cats"})
    )

    provider = GoogleProvider(api_key="test")
    tools = [
        ToolDefinition(
            name="search",
            description="search the web",
            parameters={"type": "object", "properties": {"q": {"type": "string"}}},
        )
    ]
    result = await provider.complete([Message(role=Role.USER, content="find cats")], tools=tools)

    assert result.content == ""
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "search"
    assert result.tool_calls[0].arguments == {"q": "cats"}
    assert result.stop_reason == "tool_use"


@pytest.mark.asyncio
async def test_stream(fake_genai):
    _, fake_model = fake_genai

    async def _iter():
        yield _make_text_response("hel", 0, 0)
        yield _make_text_response("lo", 0, 0)

    fake_model.generate_content_async = AsyncMock(return_value=_iter())

    provider = GoogleProvider(api_key="test")
    chunks = []
    async for ch in provider.stream([Message(role=Role.USER, content="hi")]):
        chunks.append(ch)

    texts = [c.content for c in chunks if c.content]
    assert "".join(texts) == "hello"
    assert chunks[-1].is_final is True


def test_convert_messages_system_dropped():
    provider = GoogleProvider(api_key="test")
    out = provider._convert_messages(
        [
            Message(role=Role.SYSTEM, content="ignored"),
            Message(role=Role.USER, content="hi"),
            Message(role=Role.ASSISTANT, content="hello"),
        ]
    )
    assert len(out) == 2
    assert out[0]["role"] == "user"
    assert out[1]["role"] == "model"
    assert out[0]["parts"][0]["text"] == "hi"


def test_convert_messages_tool_call_and_result():
    provider = GoogleProvider(api_key="test")
    out = provider._convert_messages(
        [
            Message(
                role=Role.ASSISTANT,
                content="",
                tool_calls=[ToolCall(id="t1", name="search", arguments={"q": "x"})],
            ),
            Message(role=Role.TOOL, content="result text", tool_call_id="search"),
        ]
    )
    assert out[0]["role"] == "model"
    assert out[0]["parts"][0]["function_call"]["name"] == "search"
    assert out[1]["role"] == "user"
    assert out[1]["parts"][0]["function_response"]["name"] == "search"


def test_convert_tool_schema():
    provider = GoogleProvider(api_key="test")
    tool = ToolDefinition(
        name="lookup",
        description="look stuff up",
        parameters={"type": "object", "properties": {}},
    )
    out = provider._convert_tool(tool)
    decl = out["function_declarations"][0]
    assert decl["name"] == "lookup"
    assert decl["description"] == "look stuff up"
    assert decl["parameters"] == {"type": "object", "properties": {}}


def test_get_genai_missing_sdk(monkeypatch):
    """If google-generativeai is not installed, raise a clear ImportError."""
    # Make sure no stub is in sys.modules, then force import to fail.
    monkeypatch.delitem(sys.modules, "google.generativeai", raising=False)

    real_import = (
        __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__
    )

    def fake_import(name, *args, **kwargs):
        if name == "google.generativeai" or name.startswith("google.generativeai"):
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    provider = GoogleProvider(api_key="test")
    with pytest.raises(ImportError, match="google-generativeai"):
        provider._get_genai()
