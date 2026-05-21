"""Tests for OpenRouterProvider model registry and capability lookups."""

from __future__ import annotations

from agent_orchestrator.providers.openrouter import OpenRouterProvider


def test_qwen36_plus_registered():
    """qwen/qwen3.6-plus must be a known model with real OpenRouter pricing."""
    info = OpenRouterProvider.MODELS["qwen/qwen3.6-plus"]
    assert info["input_cost"] == 0.325
    assert info["output_cost"] == 1.95
    assert info["context"] == 1_000_000
    assert info["max_output"] >= 8_192


def test_qwen36_plus_capabilities():
    provider = OpenRouterProvider(model="qwen/qwen3.6-plus", api_key="test")
    caps = provider.capabilities
    assert caps.max_context == 1_000_000
    assert caps.supports_tools is True
    assert caps.supports_streaming is True
    assert caps.coding_quality >= 0.85
    assert provider.input_cost_per_million == 0.325
    assert provider.output_cost_per_million == 1.95


def test_qwen36_plus_in_fallback_chain():
    """Paid frontier model should be listed in the fallback order."""
    assert "qwen/qwen3.6-plus" in OpenRouterProvider.FALLBACK_ORDER


def test_estimate_cost():
    provider = OpenRouterProvider(model="qwen/qwen3.6-plus", api_key="test")
    # 1M input + 1M output = $0.325 + $1.95 = $2.275
    assert abs(provider.estimate_cost(1_000_000, 1_000_000) - 2.275) < 1e-6


def test_qwen36_flash_registered():
    """qwen/qwen3.6-flash is the direct successor to qwen3.5-flash (1M ctx)."""
    info = OpenRouterProvider.MODELS["qwen/qwen3.6-flash"]
    assert info["input_cost"] == 0.1875
    assert info["output_cost"] == 1.125
    assert info["context"] == 1_000_000


def test_ling_26_flash_registered():
    """inclusionai/ling-2.6-flash — cheapest paid flash alternative."""
    info = OpenRouterProvider.MODELS["inclusionai/ling-2.6-flash"]
    assert info["input_cost"] == 0.01
    assert info["output_cost"] == 0.03
    assert info["context"] == 262_144


def test_tencent_hy3_preview_registered():
    """tencent/hy3-preview — parity-price newer alternative to qwen3.5-flash."""
    info = OpenRouterProvider.MODELS["tencent/hy3-preview"]
    assert info["input_cost"] == 0.066
    assert info["output_cost"] == 0.26
    assert info["context"] == 262_144


def test_flash_tier_alternatives_after_qwen35_flash():
    """The three flash-tier alternatives must appear AFTER qwen3.5-flash in the
    MODELS dict (insertion order is preserved in Python 3.7+ dicts)."""
    keys = list(OpenRouterProvider.MODELS.keys())
    base = keys.index("qwen/qwen3.5-flash-02-23")
    for follower in (
        "qwen/qwen3.6-flash",
        "inclusionai/ling-2.6-flash",
        "tencent/hy3-preview",
    ):
        assert keys.index(follower) > base, f"{follower} must come after qwen3.5-flash"


# --- DeepSeek V4 family — verified against the OpenRouter catalog ---


def test_deepseek_v4_flash_registered():
    """deepseek/deepseek-v4-flash — efficiency-optimized MoE, default model
    for the medical-advisor agent. Pricing and context match OpenRouter."""
    info = OpenRouterProvider.MODELS["deepseek/deepseek-v4-flash"]
    assert info["input_cost"] == 0.112
    assert info["output_cost"] == 0.224
    assert info["context"] == 1_050_000
    assert info["max_output"] >= 8_192
    assert info["reasoning"] >= 0.80


def test_deepseek_v4_flash_capabilities():
    provider = OpenRouterProvider(model="deepseek/deepseek-v4-flash", api_key="test")
    caps = provider.capabilities
    assert caps.max_context == 1_050_000
    assert caps.supports_tools is True
    assert caps.supports_streaming is True
    assert provider.input_cost_per_million == 0.112
    assert provider.output_cost_per_million == 0.224


def test_deepseek_v4_flash_in_fallback_chain():
    """DeepSeek-v4-flash must be reachable through the fallback chain."""
    assert "deepseek/deepseek-v4-flash" in OpenRouterProvider.FALLBACK_ORDER


def test_deepseek_v4_pro_registered():
    """deepseek/deepseek-v4-pro — large-scale MoE (1.6T total / 49B active)."""
    info = OpenRouterProvider.MODELS["deepseek/deepseek-v4-pro"]
    assert info["input_cost"] == 0.435
    assert info["output_cost"] == 0.87
    assert info["context"] == 1_050_000
    assert info["coding"] >= 0.88


def test_deepseek_v4_pro_capabilities():
    provider = OpenRouterProvider(model="deepseek/deepseek-v4-pro", api_key="test")
    caps = provider.capabilities
    assert caps.max_context == 1_050_000
    assert provider.input_cost_per_million == 0.435
    assert provider.output_cost_per_million == 0.87


def test_deepseek_v4_pro_ranked_above_flash_in_fallback():
    """Pro should be preferred over Flash when both are reachable — it has
    higher reasoning and coding scores."""
    order = OpenRouterProvider.FALLBACK_ORDER
    assert "deepseek/deepseek-v4-pro" in order
    assert "deepseek/deepseek-v4-flash" in order
    assert order.index("deepseek/deepseek-v4-pro") < order.index("deepseek/deepseek-v4-flash")
