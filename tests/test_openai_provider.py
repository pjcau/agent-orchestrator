"""Tests for ``OpenAIProvider.complete`` response handling.

Focus: the provider must not crash with an opaque
``'NoneType' object is not subscriptable`` when an upstream (notably a preview
model proxied via OpenRouter) returns a 200 whose ``choices`` is null or empty.
It must raise a clear, catchable error so the OpenRouter fallback chain can try
another model. See ``providers/openai.py`` (the ``response.choices`` guard).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_orchestrator.core.provider import Message, Role
from agent_orchestrator.providers.openai import OpenAIProvider


def _provider_with_response(response) -> OpenAIProvider:
    """Build a provider whose client returns ``response`` from create()."""
    provider = OpenAIProvider(model="gpt-4o", api_key="test")
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=response)))
    )
    provider._client = client  # bypass real AsyncOpenAI construction
    return provider


def _ok_response(content: str):
    message = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
    return SimpleNamespace(choices=[choice], usage=usage)


_MSGS = [Message(role=Role.USER, content="hi")]


@pytest.mark.asyncio
async def test_none_choices_raises_clear_error_not_typeerror():
    # The exact production crash: choices is None (not []).
    response = SimpleNamespace(choices=None, usage=None)
    provider = _provider_with_response(response)
    with pytest.raises(RuntimeError) as exc:
        await provider.complete(_MSGS)
    msg = str(exc.value)
    assert "no choices" in msg
    assert "gpt-4o" in msg
    # Must NOT be the opaque TypeError that reached the user as turn_failed.
    assert "subscriptable" not in msg


@pytest.mark.asyncio
async def test_empty_choices_raises_clear_error():
    response = SimpleNamespace(choices=[], usage=None)
    provider = _provider_with_response(response)
    with pytest.raises(RuntimeError, match="no choices"):
        await provider.complete(_MSGS)


@pytest.mark.asyncio
async def test_upstream_error_is_surfaced_in_message():
    response = SimpleNamespace(choices=None, usage=None, error={"message": "moderated"})
    provider = _provider_with_response(response)
    with pytest.raises(RuntimeError, match="upstream error"):
        await provider.complete(_MSGS)


@pytest.mark.asyncio
async def test_valid_response_still_completes():
    # The guard must not regress the happy path.
    provider = _provider_with_response(_ok_response("hello there"))
    result = await provider.complete(_MSGS)
    assert result.content == "hello there"
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 5
