"""Local model provider (Ollama, vLLM, or any OpenAI-compatible endpoint)."""

from __future__ import annotations

from ..core.provider import ModelCapabilities
from .openai import OpenAIProvider


class LocalProvider(OpenAIProvider):
    """Local models via Ollama or vLLM (OpenAI-compatible API)."""

    def __init__(
        self,
        model: str = "llama3.3:70b",
        base_url: str = "http://localhost:11434/v1",
        context_size: int = 128_000,
    ):
        super().__init__(model=model, api_key="ollama")
        self._base_url = base_url
        self._context_size = context_size

    async def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise ImportError("pip install openai")
            self._client = AsyncOpenAI(
                api_key="ollama",
                base_url=self._base_url,
            )
        return self._client

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            max_context=self._context_size,
            supports_tools=True,
            supports_vision=False,
            supports_streaming=True,
            coding_quality=0.75,
            reasoning_quality=0.70,
        )

    @property
    def input_cost_per_million(self) -> float:
        return 0.0  # local = free (hardware cost is sunk)

    @property
    def output_cost_per_million(self) -> float:
        return 0.0
