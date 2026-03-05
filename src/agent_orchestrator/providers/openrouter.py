"""OpenRouter provider — access 200+ models via a single API.

OpenRouter uses an OpenAI-compatible API at https://openrouter.ai/api/v1.
"""

from __future__ import annotations

import os

from ..core.provider import ModelCapabilities
from .openai import OpenAIProvider


class OpenRouterProvider(OpenAIProvider):
    """Access any model via OpenRouter (OpenAI-compatible)."""

    MODELS = {
        "anthropic/claude-sonnet-4": {
            "input_cost": 3.0,
            "output_cost": 15.0,
            "context": 200_000,
            "coding": 0.90,
            "reasoning": 0.85,
        },
        "anthropic/claude-haiku-4": {
            "input_cost": 0.80,
            "output_cost": 4.0,
            "context": 200_000,
            "coding": 0.70,
            "reasoning": 0.65,
        },
        "openai/gpt-4o": {
            "input_cost": 2.50,
            "output_cost": 10.0,
            "context": 128_000,
            "coding": 0.85,
            "reasoning": 0.80,
        },
        "openai/gpt-4o-mini": {
            "input_cost": 0.15,
            "output_cost": 0.60,
            "context": 128_000,
            "coding": 0.65,
            "reasoning": 0.55,
        },
        "google/gemini-2.5-flash-preview": {
            "input_cost": 0.15,
            "output_cost": 0.60,
            "context": 1_000_000,
            "coding": 0.80,
            "reasoning": 0.80,
        },
        "deepseek/deepseek-chat-v3": {
            "input_cost": 0.27,
            "output_cost": 1.10,
            "context": 128_000,
            "coding": 0.80,
            "reasoning": 0.75,
        },
        "meta-llama/llama-4-maverick": {
            "input_cost": 0.20,
            "output_cost": 0.60,
            "context": 1_000_000,
            "coding": 0.75,
            "reasoning": 0.70,
        },
        "qwen/qwen3-235b-a22b": {
            "input_cost": 0.20,
            "output_cost": 0.60,
            "context": 40_960,
            "coding": 0.82,
            "reasoning": 0.80,
        },
        "qwen/qwen3.5-plus-02-15": {
            "input_cost": 0.30,
            "output_cost": 1.20,
            "context": 131_072,
            "coding": 0.85,
            "reasoning": 0.85,
        },
    }

    def __init__(self, model: str = "anthropic/claude-sonnet-4", api_key: str | None = None):
        self._model = model
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self._client = None

    async def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise ImportError("pip install openai")
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url="https://openrouter.ai/api/v1",
            )
        return self._client

    @property
    def capabilities(self) -> ModelCapabilities:
        info = self.MODELS.get(self._model, list(self.MODELS.values())[0])
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
        info = self.MODELS.get(self._model, list(self.MODELS.values())[0])
        return info["input_cost"]

    @property
    def output_cost_per_million(self) -> float:
        info = self.MODELS.get(self._model, list(self.MODELS.values())[0])
        return info["output_cost"]
