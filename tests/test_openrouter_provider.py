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


def test_no_free_models_in_catalog():
    """All `:free` endpoints were removed — the catalog is paid-only now,
    so the model picker doesn't double-list the same vendor at lower quality."""
    free = [m for m in OpenRouterProvider.MODELS if ":free" in m]
    assert free == [], f"unexpected free models still in MODELS: {free}"


def test_every_model_has_a_tier():
    """Each entry must declare `tier` so the UI dropdown can group correctly."""
    for model_id, info in OpenRouterProvider.MODELS.items():
        assert "tier" in info, f"{model_id} missing tier"
        assert info["tier"] in ("premium", "paid"), f"{model_id} has invalid tier {info['tier']!r}"


def test_paid_premium_tier_exact_membership():
    """Paid Premium is the pinned set: qwen3.6-plus, qwen3-235b-a22b-thinking-2507,
    deepseek-v4-pro, qwen3.5-397b-a17b. Anything else here means the catalog
    has drifted."""
    premium = {m for m, info in OpenRouterProvider.MODELS.items() if info["tier"] == "premium"}
    assert premium == {
        "qwen/qwen3.6-plus",
        "qwen/qwen3-235b-a22b-thinking-2507",
        "deepseek/deepseek-v4-pro",
        "qwen/qwen3.5-397b-a17b",
    }, f"premium tier drift: {premium}"


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


# ---------------------------------------------------------------------------
# Cache context injection — v0.4.1
# ---------------------------------------------------------------------------


def test_convert_messages_no_cache_ctx_matches_base():
    """Without a cache context, the converted messages must equal whatever
    the OpenAI-compatible base produced — no behaviour change for users
    who do not opt in to caching."""
    from agent_orchestrator.core.cache_context import set_cache_context
    from agent_orchestrator.core.provider import Message, Role

    set_cache_context(None)
    p = OpenRouterProvider(model="qwen/qwen3.6-plus", api_key="test")
    msgs = [Message(role=Role.USER, content="hello")]
    out = p._convert_messages(msgs, "be brief")
    assert out == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hello"},
    ]


def test_convert_messages_injects_cache_control_on_system():
    """When cache_context is set, the system message is split into two
    content blocks: the original prompt + the cacheable context with
    cache_control: {type: ephemeral}."""
    from agent_orchestrator.core.cache_context import set_cache_context
    from agent_orchestrator.core.provider import Message, Role

    set_cache_context("FILE CONTEXT BLOB")
    p = OpenRouterProvider(model="qwen/qwen3.6-plus", api_key="test")
    msgs = [Message(role=Role.USER, content="hello")]
    out = p._convert_messages(msgs, "be brief")
    system = out[0]
    assert system["role"] == "system"
    assert isinstance(system["content"], list)
    assert system["content"][0] == {"type": "text", "text": "be brief"}
    cache_block = system["content"][1]
    assert cache_block["text"] == "FILE CONTEXT BLOB"
    assert cache_block["cache_control"] == {"type": "ephemeral"}
    set_cache_context(None)  # reset for other tests


def test_convert_messages_synthesises_system_when_absent():
    """If no system prompt is provided, the cache_control block becomes the
    sole entry of a fresh system message."""
    from agent_orchestrator.core.cache_context import set_cache_context
    from agent_orchestrator.core.provider import Message, Role

    set_cache_context("BLOB")
    p = OpenRouterProvider(model="qwen/qwen3.6-plus", api_key="test")
    out = p._convert_messages([Message(role=Role.USER, content="hi")], None)
    assert out[0]["role"] == "system"
    assert out[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert out[0]["content"][0]["text"] == "BLOB"
    set_cache_context(None)
