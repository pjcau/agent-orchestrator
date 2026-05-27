"""OpenRouter provider — access 200+ models via a single API.

OpenRouter uses an OpenAI-compatible API at https://openrouter.ai/api/v1.
Includes automatic retry with fallback on 429 rate limits and 402 credit errors.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re

from ..core.cache_context import current_cache_context
from ..core.provider import Completion, Message, ModelCapabilities, ToolDefinition
from .openai import OpenAIProvider


def _sanitize_log(value: str) -> str:
    """Sanitize user-controlled values for safe logging (prevent log injection)."""
    return value.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


logger = logging.getLogger(__name__)


class OpenRouterProvider(OpenAIProvider):
    """Access any model via OpenRouter (OpenAI-compatible)."""

    # Each entry carries a `tier` field:
    #   - "premium": the three top-shelf models pinned by the project
    #     (qwen3.6-plus, qwen3-235b-a22b-thinking-2507, deepseek-v4-pro).
    #   - "paid":    everything else still on the menu — paid endpoints with
    #                non-zero per-token cost.
    # Free (`:free`) endpoints used to sit alongside paid ones; they were
    # removed because they double-list the same vendor at lower quality and
    # made the model picker noisy.
    MODELS = {
        # --- Paid Premium tier ---
        "qwen/qwen3.6-plus": {
            "tier": "premium",
            "input_cost": 0.325,
            "output_cost": 1.95,
            "context": 1_000_000,
            "max_output": 32_768,
            "coding": 0.92,
            "reasoning": 0.90,
        },
        "qwen/qwen3-235b-a22b-thinking-2507": {
            "tier": "premium",
            # 235B-parameter MoE — listed by the user as a premium reasoning
            # tier. OpenRouter exposes this slug at $0 (zero-priced research
            # endpoint); keep the pricing accurate.
            "input_cost": 0.0,
            "output_cost": 0.0,
            "context": 131_072,
            "max_output": 16_384,
            "coding": 0.85,
            "reasoning": 0.88,
        },
        "deepseek/deepseek-v4-pro": {
            "tier": "premium",
            "input_cost": 0.435,
            "output_cost": 0.87,
            "context": 1_050_000,
            "max_output": 32_768,
            "coding": 0.91,
            "reasoning": 0.90,
        },
        "qwen/qwen3.5-397b-a17b": {
            "tier": "premium",
            "input_cost": 0.39,
            "output_cost": 2.34,
            "context": 262_144,
            "max_output": 32_768,
            "coding": 0.87,
            "reasoning": 0.86,
        },
        # --- Paid tier ---
        "qwen/qwen3-coder-next": {
            "tier": "paid",
            "input_cost": 0.12,
            "output_cost": 0.75,
            "context": 262_144,
            "max_output": 32_768,
            "coding": 0.90,
            "reasoning": 0.82,
        },
        "qwen/qwen3.5-flash-02-23": {
            "tier": "paid",
            "input_cost": 0.06,
            "output_cost": 0.30,
            "context": 262_144,
            "max_output": 32_768,
            "coding": 0.85,
            "reasoning": 0.80,
        },
        "qwen/qwen3.6-flash": {
            "tier": "paid",
            "input_cost": 0.1875,
            "output_cost": 1.125,
            "context": 1_000_000,
            "max_output": 32_768,
            "coding": 0.87,
            "reasoning": 0.83,
        },
        "inclusionai/ling-2.6-flash": {
            "tier": "paid",
            "input_cost": 0.01,
            "output_cost": 0.03,
            "context": 262_144,
            "max_output": 32_768,
            "coding": 0.78,
            "reasoning": 0.72,
        },
        "tencent/hy3-preview": {
            "tier": "paid",
            "input_cost": 0.066,
            "output_cost": 0.26,
            "context": 262_144,
            "max_output": 32_768,
            "coding": 0.80,
            "reasoning": 0.75,
        },
        # DeepSeek V4 Flash — efficiency-optimized MoE. Default model for
        # the healthcare agents (see .claude/agents/healthcare/_safety.md).
        "deepseek/deepseek-v4-flash": {
            "tier": "paid",
            "input_cost": 0.112,
            "output_cost": 0.224,
            "context": 1_050_000,
            "max_output": 16_384,
            "coding": 0.85,
            "reasoning": 0.82,
        },
    }

    def __init__(self, model: str = "deepseek/deepseek-v4-flash", api_key: str | None = None):
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
            max_output_tokens=info.get("max_output", 4096),
        )

    @property
    def input_cost_per_million(self) -> float:
        info = self.MODELS.get(self._model, list(self.MODELS.values())[0])
        return info["input_cost"]

    @property
    def output_cost_per_million(self) -> float:
        info = self.MODELS.get(self._model, list(self.MODELS.values())[0])
        return info["output_cost"]

    # Fallback order: premium first, then standard paid. No :free models —
    # see the MODELS docstring above for rationale.
    FALLBACK_ORDER = [
        # premium
        "deepseek/deepseek-v4-pro",
        "qwen/qwen3.6-plus",
        "qwen/qwen3-235b-a22b-thinking-2507",
        "qwen/qwen3.5-397b-a17b",
        # paid
        "deepseek/deepseek-v4-flash",
        "qwen/qwen3-coder-next",
        "qwen/qwen3.6-flash",
        "qwen/qwen3.5-flash-02-23",
        "tencent/hy3-preview",
        "inclusionai/ling-2.6-flash",
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

        Fallback order: the chosen model first, then the highest-quality
        sibling in FALLBACK_ORDER (premium → paid). Since the project no
        longer enumerates `:free` endpoints, the previous bias toward free
        siblings is gone — at most one retry on a real failure.
        """
        # Try: chosen model, then the first sibling from FALLBACK_ORDER —
        # max 2 attempts so an unrecoverable upstream failure doesn't fan out.
        models_to_try = [self._model]
        for candidate in self.FALLBACK_ORDER:
            if candidate != self._model:
                models_to_try.append(candidate)
                break

        last_error = None
        effective_max_tokens = max_tokens
        self.last_fallback_log = []

        logger.debug(
            "Fallback chain: trying %d models starting with %r",
            len(models_to_try),
            _sanitize_log(models_to_try[0]),
        )
        for i, model in enumerate(models_to_try):
            original_model = self._model
            self._model = model
            logger.debug(
                "Trying model %d/%d: %r (max_tokens=%d)",
                i + 1,
                len(models_to_try),
                _sanitize_log(model),
                effective_max_tokens,
            )
            try:
                result = await super().complete(
                    messages=messages,
                    tools=tools,
                    system=system,
                    max_tokens=effective_max_tokens,
                    temperature=temperature,
                )
                if model != original_model:
                    logger.info(
                        "Fallback: %r unavailable, used %r",
                        _sanitize_log(original_model),
                        _sanitize_log(model),
                    )
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
                            "Credit limit on %r: reducing max_tokens to %d",
                            _sanitize_log(model),
                            effective_max_tokens,
                        )
                        self.last_fallback_log.append(
                            {
                                "model": model,
                                "status": "402",
                                "detail": f"reduced to {affordable} tok",
                            }
                        )
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
                            self.last_fallback_log.append(
                                {
                                    "model": model,
                                    "status": "ok",
                                    "detail": f"max_tokens={effective_max_tokens}",
                                }
                            )
                            return result
                        except Exception as retry_exc:
                            self._model = original_model
                            self.last_fallback_log.append(
                                {
                                    "model": model,
                                    "status": "402",
                                    "detail": f"retry failed: {str(retry_exc)[:80]}",
                                }
                            )
                    else:
                        self.last_fallback_log.append(
                            {"model": model, "status": "402", "detail": f"affordable={affordable}"}
                        )
                    # Fall through to try cheaper models
                    logger.warning(
                        "Insufficient credits for %r, trying cheaper model...",
                        _sanitize_log(model),
                    )
                    last_error = exc
                    await asyncio.sleep(0.3)
                    continue

                # 404: data policy or model not found — skip this model
                if (
                    "404" in err_str
                    or "data policy" in err_str.lower()
                    or "No endpoints" in err_str
                ):
                    logger.warning(
                        "Model %r blocked (data policy/404), trying next...",
                        _sanitize_log(model),
                    )
                    self.last_fallback_log.append(
                        {"model": model, "status": "404", "detail": "data policy/not found"}
                    )
                    last_error = exc
                    continue

                # 400: provider error (e.g. model doesn't support system prompt)
                if "400" in err_str and "Provider returned error" in err_str:
                    logger.warning(
                        "Model %r returned provider error, trying next...",
                        _sanitize_log(model),
                    )
                    self.last_fallback_log.append(
                        {"model": model, "status": "400", "detail": "provider error"}
                    )
                    last_error = exc
                    continue

                # 429: rate limited — try next model
                if "429" in err_str or "rate" in err_str.lower():
                    logger.warning(
                        "Rate limited on %r, trying next...",
                        _sanitize_log(model),
                    )
                    self.last_fallback_log.append(
                        {"model": model, "status": "429", "detail": "rate limited"}
                    )
                    last_error = exc
                    await asyncio.sleep(0.5)
                    continue

                self.last_fallback_log.append(
                    {"model": model, "status": "error", "detail": err_str[:80]}
                )
                raise
        raise last_error  # type: ignore[misc]

    @staticmethod
    def _parse_affordable_tokens(error_msg: str) -> int | None:
        """Extract 'can only afford N' tokens from a 402 error message."""
        match = re.search(r"can only afford (\d+)", error_msg)
        return int(match.group(1)) if match else None

    def _convert_messages(self, messages: list[Message], system: str | None) -> list[dict]:
        """Override to inject Anthropic-style `cache_control` markers when a
        cacheable prefix has been set on the request via the
        :mod:`core.cache_context` ContextVar.

        OpenRouter forwards ``cache_control: {type: "ephemeral"}`` to providers
        that support it (Anthropic native, Tencent Hy3 via OpenRouter's own
        caching layer, plus a growing list of routed providers). Providers
        that do not support it simply ignore the field — there is no
        request-shape regression.

        The cacheable block is rendered as part of the ``system`` payload so
        the user → assistant message alternation stays untouched. The system
        text is split into two content blocks:

          1. The original system prompt (un-marked, recomputed per turn).
          2. The CLI's ``cache_context`` (marked ephemeral) — for an
             `ago chat` session this is the @file / @dir expansion.

        When no cache context is set, behaviour is identical to the
        OpenAI-compatible base class — the system stays a plain string.
        """
        cache = current_cache_context()
        if not cache:
            return super()._convert_messages(messages, system)

        # Build the base result first, then patch the system message into a
        # two-block list with cache_control on the @file portion.
        result = super()._convert_messages(messages, system)
        cache_block: dict = {
            "type": "text",
            "text": cache,
            "cache_control": {"type": "ephemeral"},
        }
        if result and result[0].get("role") == "system":
            existing = result[0].get("content")
            if isinstance(existing, str):
                result[0]["content"] = [
                    {"type": "text", "text": existing},
                    cache_block,
                ]
            elif isinstance(existing, list):
                existing.append(cache_block)
            else:
                result[0]["content"] = [cache_block]
        else:
            result.insert(
                0,
                {"role": "system", "content": [cache_block]},
            )
        return result
