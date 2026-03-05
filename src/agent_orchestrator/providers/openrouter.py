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
        # --- Free models (big brands) ---
        # Google
        "google/gemma-3-27b-it:free": {
            "input_cost": 0.0,
            "output_cost": 0.0,
            "context": 131_072,
            "coding": 0.72,
            "reasoning": 0.70,
        },
        "google/gemma-3-12b-it:free": {
            "input_cost": 0.0,
            "output_cost": 0.0,
            "context": 32_768,
            "coding": 0.65,
            "reasoning": 0.62,
        },
        # Meta
        "meta-llama/llama-3.3-70b-instruct:free": {
            "input_cost": 0.0,
            "output_cost": 0.0,
            "context": 128_000,
            "coding": 0.78,
            "reasoning": 0.75,
        },
        "meta-llama/llama-3.2-3b-instruct:free": {
            "input_cost": 0.0,
            "output_cost": 0.0,
            "context": 131_072,
            "coding": 0.50,
            "reasoning": 0.45,
        },
        # Qwen (Alibaba)
        "qwen/qwen3-coder:free": {
            "input_cost": 0.0,
            "output_cost": 0.0,
            "context": 262_000,
            "coding": 0.88,
            "reasoning": 0.80,
        },
        "qwen/qwen3-235b-a22b-thinking-2507": {
            "input_cost": 0.0,
            "output_cost": 0.0,
            "context": 131_072,
            "coding": 0.85,
            "reasoning": 0.88,
        },
        "qwen/qwen3-next-80b-a3b-instruct:free": {
            "input_cost": 0.0,
            "output_cost": 0.0,
            "context": 262_144,
            "coding": 0.80,
            "reasoning": 0.78,
        },
        "qwen/qwen3-4b:free": {
            "input_cost": 0.0,
            "output_cost": 0.0,
            "context": 40_960,
            "coding": 0.55,
            "reasoning": 0.50,
        },
        # OpenAI
        "openai/gpt-oss-120b:free": {
            "input_cost": 0.0,
            "output_cost": 0.0,
            "context": 131_072,
            "coding": 0.82,
            "reasoning": 0.80,
        },
        "openai/gpt-oss-20b:free": {
            "input_cost": 0.0,
            "output_cost": 0.0,
            "context": 131_072,
            "coding": 0.65,
            "reasoning": 0.60,
        },
        # Mistral
        "mistralai/mistral-small-3.1-24b-instruct:free": {
            "input_cost": 0.0,
            "output_cost": 0.0,
            "context": 128_000,
            "coding": 0.72,
            "reasoning": 0.70,
        },
        # NVIDIA
        "nvidia/nemotron-3-nano-30b-a3b:free": {
            "input_cost": 0.0,
            "output_cost": 0.0,
            "context": 256_000,
            "coding": 0.70,
            "reasoning": 0.68,
        },
        "nvidia/nemotron-nano-9b-v2:free": {
            "input_cost": 0.0,
            "output_cost": 0.0,
            "context": 128_000,
            "coding": 0.60,
            "reasoning": 0.58,
        },
        # Nous Research
        "nousresearch/hermes-3-llama-3.1-405b:free": {
            "input_cost": 0.0,
            "output_cost": 0.0,
            "context": 131_072,
            "coding": 0.80,
            "reasoning": 0.78,
        },
    }

    def __init__(self, model: str = "qwen/qwen3-coder:free", api_key: str | None = None):
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
