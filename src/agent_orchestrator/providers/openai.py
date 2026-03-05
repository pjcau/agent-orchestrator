"""OpenAI (GPT) provider implementation."""

from __future__ import annotations

import json
import os
from typing import AsyncIterator

from ..core.provider import (
    Completion,
    Message,
    ModelCapabilities,
    Provider,
    StreamChunk,
    ToolCall,
    ToolDefinition,
    Usage,
)


class OpenAIProvider(Provider):
    """GPT models via the OpenAI API."""

    MODELS = {
        "gpt-4o": {
            "input_cost": 2.50,
            "output_cost": 10.0,
            "context": 128_000,
            "coding": 0.85,
            "reasoning": 0.80,
        },
        "gpt-4o-mini": {
            "input_cost": 0.15,
            "output_cost": 0.60,
            "context": 128_000,
            "coding": 0.65,
            "reasoning": 0.55,
        },
        "o3": {
            "input_cost": 10.0,
            "output_cost": 40.0,
            "context": 200_000,
            "coding": 0.90,
            "reasoning": 0.95,
        },
    }

    def __init__(self, model: str = "gpt-4o", api_key: str | None = None):
        self._model = model
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._client = None

    async def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise ImportError("pip install openai")
            self._client = AsyncOpenAI(api_key=self._api_key)
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

        api_messages = self._convert_messages(messages, system)
        kwargs: dict = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        response = await client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        tool_calls = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=json.loads(tc.function.arguments),
                    )
                )

        usage = Usage(
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
            cost_usd=self.estimate_cost(
                response.usage.prompt_tokens if response.usage else 0,
                response.usage.completion_tokens if response.usage else 0,
            ),
        )

        return Completion(
            content=choice.message.content or "",
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=choice.finish_reason or "stop",
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamChunk]:
        client = await self._get_client()
        api_messages = self._convert_messages(messages, system)
        kwargs: dict = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        stream = await client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield StreamChunk(content=chunk.choices[0].delta.content)
        yield StreamChunk(is_final=True)

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def capabilities(self) -> ModelCapabilities:
        info = self.MODELS.get(self._model, self.MODELS["gpt-4o"])
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
        return self.MODELS.get(self._model, self.MODELS["gpt-4o"])["input_cost"]

    @property
    def output_cost_per_million(self) -> float:
        return self.MODELS.get(self._model, self.MODELS["gpt-4o"])["output_cost"]

    def _convert_messages(self, messages: list[Message], system: str | None) -> list[dict]:
        result = []
        if system:
            result.append({"role": "system", "content": system})
        for msg in messages:
            if msg.tool_call_id:
                result.append(
                    {
                        "role": "tool",
                        "content": msg.content,
                        "tool_call_id": msg.tool_call_id,
                    }
                )
            elif msg.tool_calls:
                entry: dict = {
                    "role": "assistant",
                    "content": msg.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
                result.append(entry)
            else:
                result.append({"role": msg.role.value, "content": msg.content})
        return result

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]
