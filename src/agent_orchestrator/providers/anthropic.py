"""Anthropic (Claude) provider implementation."""

from __future__ import annotations

import os
from typing import AsyncIterator

from ..core.provider import (
    Completion,
    Message,
    ModelCapabilities,
    Provider,
    Role,
    StreamChunk,
    ToolCall,
    ToolDefinition,
    Usage,
)


class AnthropicProvider(Provider):
    """Claude models via the Anthropic API."""

    MODELS = {
        "claude-opus-4-6": {
            "input_cost": 15.0,
            "output_cost": 75.0,
            "context": 200_000,
            "coding": 0.95,
            "reasoning": 0.95,
        },
        "claude-sonnet-4-6": {
            "input_cost": 3.0,
            "output_cost": 15.0,
            "context": 200_000,
            "coding": 0.90,
            "reasoning": 0.85,
        },
        "claude-haiku-4-5-20251001": {
            "input_cost": 0.80,
            "output_cost": 4.0,
            "context": 200_000,
            "coding": 0.70,
            "reasoning": 0.65,
        },
    }

    def __init__(self, model: str = "claude-sonnet-4-6", api_key: str | None = None):
        self._model = model
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._client = None  # lazy init

    async def _get_client(self):
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError:
                raise ImportError("pip install anthropic")
            self._client = AsyncAnthropic(api_key=self._api_key)
        return self._client

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion:
        client = await self._get_client()

        # Convert messages to Anthropic format
        api_messages = self._convert_messages(messages)
        kwargs: dict = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        response = await client.messages.create(**kwargs)

        # Parse response
        content = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=block.input)
                )

        usage = Usage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost_usd=self.estimate_cost(
                response.usage.input_tokens, response.usage.output_tokens
            ),
        )

        return Completion(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=response.stop_reason or "end_turn",
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamChunk]:
        client = await self._get_client()
        api_messages = self._convert_messages(messages)
        kwargs: dict = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": max_tokens,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        async with client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if hasattr(event, "delta") and hasattr(event.delta, "text"):
                    yield StreamChunk(content=event.delta.text)
            yield StreamChunk(is_final=True)

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def capabilities(self) -> ModelCapabilities:
        info = self.MODELS.get(self._model, self.MODELS["claude-sonnet-4-6"])
        return ModelCapabilities(
            max_context=info["context"],
            supports_tools=True,
            supports_vision=True,
            supports_streaming=True,
            coding_quality=info["coding"],
            reasoning_quality=info["reasoning"],
        )

    @property
    def input_cost_per_million(self) -> float:
        return self.MODELS.get(self._model, self.MODELS["claude-sonnet-4-6"])["input_cost"]

    @property
    def output_cost_per_million(self) -> float:
        return self.MODELS.get(self._model, self.MODELS["claude-sonnet-4-6"])["output_cost"]

    def _convert_messages(self, messages: list[Message]) -> list[dict]:
        result = []
        for msg in messages:
            if msg.role == Role.SYSTEM:
                continue  # handled separately
            entry: dict = {"role": msg.role.value, "content": msg.content}
            if msg.tool_calls:
                entry["content"] = [{"type": "text", "text": msg.content}] if msg.content else []
                for tc in msg.tool_calls:
                    entry["content"].append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
            if msg.tool_call_id:
                entry = {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id,
                            "content": msg.content,
                        }
                    ],
                }
            result.append(entry)
        return result

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in tools
        ]
