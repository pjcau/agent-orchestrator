"""OpenRouter provider — access 200+ models via a single API.

OpenRouter uses an OpenAI-compatible API at https://openrouter.ai/api/v1.
Includes automatic retry with fallback on 429 rate limits and 402 credit errors.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re

from ..core.provider import Completion, Message, ModelCapabilities, ToolDefinition
from .openai import OpenAIProvider

logger = logging.getLogger(__name__)


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
        "qwen/qwen3-coder-next": {
            "input_cost": 0.12,
            "output_cost": 0.75,
            "context": 262_144,
            "coding": 0.90,
            "reasoning": 0.82,
        },
        "qwen/qwen3.5-flash-02-23": {
            "input_cost": 0.06,
            "output_cost": 0.30,
            "context": 262_144,
            "coding": 0.85,
            "reasoning": 0.80,
        },
        "qwen/qwen3.5-397b-a17b": {
            "input_cost": 0.39,
            "output_cost": 2.34,
            "context": 262_144,
            "coding": 0.87,
            "reasoning": 0.86,
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
        self.last_fallback_log: list[dict] = []

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

    # Fallback order: best free models sorted by coding quality
    FALLBACK_ORDER = [
        "qwen/qwen3-coder:free",
        "qwen/qwen3-coder-next",
        "qwen/qwen3.5-397b-a17b",
        "qwen/qwen3-235b-a22b-thinking-2507",
        "openai/gpt-oss-120b:free",
        "nousresearch/hermes-3-llama-3.1-405b:free",
        "qwen/qwen3-next-80b-a3b-instruct:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "mistralai/mistral-small-3.1-24b-instruct:free",
        "nvidia/nemotron-3-nano-30b-a3b:free",
    ]

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion:
        """Complete with automatic fallback on 429/402/404 errors.

        Fallback order: paid models first, then free as last resort when
        credits are exhausted on all paid options.
        """
        vendor = self._model.split("/")[0]  # e.g. "qwen" from "qwen/qwen3-coder-next"
        paid_models = [self._model]
        free_same_vendor: list[str] = []
        free_other: list[str] = []
        for m in self.FALLBACK_ORDER:
            if m == self._model:
                continue
            if ":free" in m:
                if m.split("/")[0] == vendor:
                    free_same_vendor.append(m)
                else:
                    free_other.append(m)
            else:
                paid_models.append(m)

        # Try: chosen model, then first free from same vendor — max 2 attempts
        models_to_try = [self._model]
        if free_same_vendor:
            models_to_try.append(free_same_vendor[0])

        last_error = None
        effective_max_tokens = max_tokens
        self.last_fallback_log = []

        logger.warning("Fallback chain: trying %d models starting with %s", len(models_to_try), models_to_try[0])
        for i, model in enumerate(models_to_try):
            original_model = self._model
            self._model = model
            logger.warning("Trying model %d/%d: %s (max_tokens=%d)", i + 1, len(models_to_try), model, effective_max_tokens)
            try:
                result = await super().complete(
                    messages=messages,
                    tools=tools,
                    system=system,
                    max_tokens=effective_max_tokens,
                    temperature=temperature,
                )
                if model != original_model:
                    logger.info("Fallback: %s unavailable, used %s", original_model, model)
                self.last_fallback_log.append({"model": model, "status": "ok"})
                return result
            except Exception as exc:
                self._model = original_model
                err_str = str(exc)

                # 402: insufficient credits — try with reduced max_tokens first
                if "402" in err_str or "credits" in err_str.lower():
                    affordable = self._parse_affordable_tokens(err_str)
                    if affordable and affordable >= 256 and effective_max_tokens > affordable:
                        effective_max_tokens = affordable
                        logger.warning(
                            "Credit limit on %s: reducing max_tokens to %d",
                            model, effective_max_tokens,
                        )
                        self.last_fallback_log.append({"model": model, "status": "402", "detail": f"reduced to {affordable} tok"})
                        # Retry same model with lower max_tokens
                        self._model = model
                        try:
                            result = await super().complete(
                                messages=messages,
                                tools=tools,
                                system=system,
                                max_tokens=effective_max_tokens,
                                temperature=temperature,
                            )
                            self.last_fallback_log.append({"model": model, "status": "ok", "detail": f"max_tokens={effective_max_tokens}"})
                            return result
                        except Exception as retry_exc:
                            self._model = original_model
                            self.last_fallback_log.append({"model": model, "status": "402", "detail": f"retry failed: {str(retry_exc)[:80]}"})
                    else:
                        self.last_fallback_log.append({"model": model, "status": "402", "detail": f"affordable={affordable}"})
                    # Fall through to try cheaper models
                    logger.warning("Insufficient credits for %s, trying cheaper model...", model)
                    last_error = exc
                    await asyncio.sleep(0.3)
                    continue

                # 404: data policy or model not found — skip this model
                if "404" in err_str or "data policy" in err_str.lower() or "No endpoints" in err_str:
                    logger.warning("Model %s blocked (data policy/404), trying next...", model)
                    self.last_fallback_log.append({"model": model, "status": "404", "detail": "data policy/not found"})
                    last_error = exc
                    continue

                # 400: provider error (e.g. model doesn't support system prompt)
                if "400" in err_str and "Provider returned error" in err_str:
                    logger.warning("Model %s returned provider error, trying next...", model)
                    self.last_fallback_log.append({"model": model, "status": "400", "detail": "provider error"})
                    last_error = exc
                    continue

                # 429: rate limited — try next model
                if "429" in err_str or "rate" in err_str.lower():
                    logger.warning("Rate limited on %s, trying next...", model)
                    self.last_fallback_log.append({"model": model, "status": "429", "detail": "rate limited"})
                    last_error = exc
                    await asyncio.sleep(0.5)
                    continue

                self.last_fallback_log.append({"model": model, "status": "error", "detail": err_str[:80]})
                raise
        raise last_error  # type: ignore[misc]

    @staticmethod
    def _parse_affordable_tokens(error_msg: str) -> int | None:
        """Extract 'can only afford N' tokens from a 402 error message."""
        match = re.search(r"can only afford (\d+)", error_msg)
        return int(match.group(1)) if match else None
