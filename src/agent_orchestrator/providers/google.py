"""Google (Gemini) provider implementation."""

from __future__ import annotations

import os
from typing import AsyncIterator

from ..core.provider import (
    Completion,
    Message,
    ModelCapabilities,
    Provider,
    StreamChunk,
    ToolDefinition,
)


class GoogleProvider(Provider):
    """Gemini models via the Google AI API."""

    MODELS = {
        "gemini-2.0-pro": {
            "input_cost": 1.25,
            "output_cost": 5.0,
            "context": 2_000_000,
            "coding": 0.80,
            "reasoning": 0.80,
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
        self._client = None

    async def _get_client(self):
        if self._client is None:
            try:
                import google.generativeai as genai
            except ImportError:
                raise ImportError("pip install google-generativeai")
            genai.configure(api_key=self._api_key)
            self._client = genai.GenerativeModel(self._model)
        return self._client

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion:
        # Placeholder — full implementation requires google-generativeai async support
        raise NotImplementedError(
            "Google provider requires google-generativeai SDK. Full async implementation is TODO."
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamChunk]:
        raise NotImplementedError("Google streaming TODO")
        yield  # make it a generator

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
