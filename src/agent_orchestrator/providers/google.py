"""Google (Gemini) provider implementation."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, AsyncIterator

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


class GoogleProvider(Provider):
    """Gemini models via the Google AI API."""

    MODELS = {
        "gemini-2.5-pro": {
            "input_cost": 1.25,
            "output_cost": 5.0,
            "context": 2_000_000,
            "coding": 0.85,
            "reasoning": 0.85,
        },
        "gemini-2.0-flash": {
            "input_cost": 0.075,
            "output_cost": 0.30,
            "context": 1_000_000,
            "coding": 0.60,
            "reasoning": 0.55,
        },
    }

    def __init__(self, model: str = "gemini-2.0-flash", api_key: str | None = None):
        self._model = model
        self._api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
        self._genai = None

    def _get_genai(self):
        if self._genai is None:
            try:
                import google.generativeai as genai
            except ImportError as e:
                raise ImportError("pip install google-generativeai") from e
            if self._api_key:
                genai.configure(api_key=self._api_key)
            self._genai = genai
        return self._genai

    def _build_model(self, system: str | None, tools: list[ToolDefinition] | None):
        genai = self._get_genai()
        kwargs: dict[str, Any] = {"model_name": self._model}
        if system:
            kwargs["system_instruction"] = system
        if tools:
            kwargs["tools"] = [self._convert_tool(t) for t in tools]
        return genai.GenerativeModel(**kwargs)

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion:
        model = self._build_model(system, tools)
        contents = self._convert_messages(messages)

        generation_config = {
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }

        response = await model.generate_content_async(
            contents,
            generation_config=generation_config,
        )

        text, tool_calls = self._parse_response(response)

        usage_meta = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage_meta, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage_meta, "candidates_token_count", 0) or 0

        usage = Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=self.estimate_cost(input_tokens, output_tokens),
        )

        stop_reason = "tool_use" if tool_calls else "end_turn"
        return Completion(
            content=text,
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=stop_reason,
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamChunk]:
        model = self._build_model(system, tools)
        contents = self._convert_messages(messages)

        generation_config = {"max_output_tokens": max_tokens}

        response = await model.generate_content_async(
            contents,
            generation_config=generation_config,
            stream=True,
        )

        async for chunk in response:
            text, tool_calls = self._parse_response(chunk)
            if text:
                yield StreamChunk(content=text)
            for tc in tool_calls:
                yield StreamChunk(tool_call=tc)

        yield StreamChunk(is_final=True)

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def capabilities(self) -> ModelCapabilities:
        info = self.MODELS.get(self._model, self.MODELS["gemini-2.0-flash"])
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
        return self.MODELS.get(self._model, self.MODELS["gemini-2.0-flash"])["input_cost"]

    @property
    def output_cost_per_million(self) -> float:
        return self.MODELS.get(self._model, self.MODELS["gemini-2.0-flash"])["output_cost"]

    def _convert_messages(self, messages: list[Message]) -> list[dict]:
        """Convert generic Message list to Gemini content format.

        Gemini uses 'user' and 'model' roles; tool results are encoded as
        function_response parts inside user turns.
        """
        contents: list[dict] = []
        for msg in messages:
            if msg.role == Role.SYSTEM:
                continue  # set via system_instruction
            if msg.tool_call_id:
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "function_response": {
                                    "name": msg.tool_call_id,
                                    "response": {"content": msg.content},
                                }
                            }
                        ],
                    }
                )
                continue

            role = "model" if msg.role == Role.ASSISTANT else "user"
            parts: list[dict] = []
            if msg.content:
                parts.append({"text": msg.content})
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    parts.append({"function_call": {"name": tc.name, "args": tc.arguments}})
            if parts:
                contents.append({"role": role, "parts": parts})
        return contents

    def _convert_tool(self, tool: ToolDefinition) -> dict:
        return {
            "function_declarations": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                }
            ]
        }

    def _parse_response(self, response: Any) -> tuple[str, list[ToolCall]]:
        """Extract text and tool calls from a Gemini response (or chunk)."""
        text = ""
        tool_calls: list[ToolCall] = []

        candidates = getattr(response, "candidates", None) or []
        for cand in candidates:
            content = getattr(cand, "content", None)
            if content is None:
                continue
            for part in getattr(content, "parts", []) or []:
                part_text = getattr(part, "text", None)
                if part_text:
                    text += part_text
                fc = getattr(part, "function_call", None)
                if fc and getattr(fc, "name", None):
                    args = getattr(fc, "args", {}) or {}
                    if hasattr(args, "items"):
                        args = dict(args)
                    elif isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {"_raw": args}
                    tool_calls.append(ToolCall(id=str(uuid.uuid4()), name=fc.name, arguments=args))
        return text, tool_calls
