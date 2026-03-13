"""LLM node factories — bridge between Provider and StateGraph.

Create graph nodes that call LLM providers. Each node reads from state,
builds a prompt, calls the provider, and writes the result back to state.

Usage:
    from agent_orchestrator.core.llm_nodes import llm_node, multi_provider_node

    # Simple LLM call node
    analyze = llm_node(
        provider=claude_provider,
        system="You are an analyst.",
        prompt_key="input",
        output_key="analysis",
    )
    graph.add_node("analyze", analyze)

    # With caching (identical prompts skip the LLM call)
    from agent_orchestrator.core.cache import CachePolicy
    analyze = llm_node(
        provider=claude_provider,
        system="You are an analyst.",
        prompt_key="input",
        output_key="analysis",
        cache_policy=CachePolicy(ttl_seconds=300),
    )

    # Multi-provider node (tries providers in order)
    robust_analyze = multi_provider_node(
        providers=[claude, gpt, local],
        system="You are an analyst.",
        prompt_key="input",
        output_key="analysis",
    )
"""

from __future__ import annotations

from typing import Any, Callable

from .cache import BaseCache, CachePolicy, InMemoryCache, cached_node, make_cache_key
from .provider import Message, Provider, Role, ToolDefinition

# Module-level shared cache for all LLM nodes
_llm_cache = InMemoryCache(max_entries=500)


def get_llm_cache() -> InMemoryCache:
    """Return the shared LLM cache instance (for stats/clearing)."""
    return _llm_cache


def llm_node(
    provider: Provider,
    system: str,
    prompt_key: str = "input",
    output_key: str = "output",
    prompt_template: str | Callable[[dict[str, Any]], str] | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.0,
    tools: list[ToolDefinition] | None = None,
    track_usage: bool = True,
    cache_policy: CachePolicy | None = None,
    cache: BaseCache | None = None,
) -> Callable[[dict[str, Any]], Any]:
    """Create a graph node that calls an LLM provider.

    Args:
        provider: LLM provider to use
        system: System prompt
        prompt_key: State key containing the user prompt
        output_key: State key to write the response to
        prompt_template: Optional template string with {key} placeholders,
                        or a callable(state) -> str for custom prompt building
        max_tokens: Max tokens for the completion
        temperature: Temperature for the completion
        tools: Optional tool definitions for tool-use
        track_usage: If True, writes usage stats to state["_usage"]
        cache_policy: Optional caching policy. Skipped when temperature > 0.
        cache: Optional cache instance. Defaults to shared _llm_cache.
    """

    async def node_func(state: dict[str, Any]) -> dict[str, Any]:
        # Build prompt
        if prompt_template is not None:
            if callable(prompt_template):
                user_content = prompt_template(state)
            else:
                user_content = prompt_template.format(**state)
        else:
            user_content = str(state.get(prompt_key, ""))

        messages = [Message(role=Role.USER, content=user_content)]

        completion = await provider.complete(
            messages=messages,
            tools=tools,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        result: dict[str, Any] = {output_key: completion.content}

        if completion.tool_calls:
            result[f"{output_key}_tool_calls"] = [
                {"name": tc.name, "arguments": tc.arguments} for tc in completion.tool_calls
            ]

        if track_usage:
            result["_usage"] = {
                "provider": provider.model_id,
                "input_tokens": completion.usage.input_tokens,
                "output_tokens": completion.usage.output_tokens,
                "cost_usd": completion.usage.cost_usd,
            }

        return result

    # Wrap with caching if policy is provided and temperature == 0
    if cache_policy and cache_policy.enabled and temperature == 0.0:
        target_cache = cache or _llm_cache

        def _cache_key_fn(state: dict[str, Any]) -> str:
            if prompt_template is not None:
                if callable(prompt_template):
                    content = prompt_template(state)
                else:
                    content = prompt_template.format(**state)
            else:
                content = str(state.get(prompt_key, ""))
            return make_cache_key(provider.model_id, system, content)

        policy = CachePolicy(
            enabled=True,
            ttl_seconds=cache_policy.ttl_seconds,
            max_entries=cache_policy.max_entries,
            cache_key_fn=_cache_key_fn,
        )
        return cached_node(target_cache, policy)(node_func)

    return node_func


def multi_provider_node(
    providers: list[Provider],
    system: str,
    prompt_key: str = "input",
    output_key: str = "output",
    prompt_template: str | Callable[[dict[str, Any]], str] | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.0,
    cache_policy: CachePolicy | None = None,
    cache: BaseCache | None = None,
) -> Callable[[dict[str, Any]], Any]:
    """Create a node that tries multiple providers in order (fallback chain).

    If the first provider fails, tries the next one, etc.
    Writes which provider was used to state["_provider_used"].
    """

    async def node_func(state: dict[str, Any]) -> dict[str, Any]:
        if prompt_template is not None:
            if callable(prompt_template):
                user_content = prompt_template(state)
            else:
                user_content = prompt_template.format(**state)
        else:
            user_content = str(state.get(prompt_key, ""))

        messages = [Message(role=Role.USER, content=user_content)]

        last_error = None
        for provider in providers:
            try:
                completion = await provider.complete(
                    messages=messages,
                    system=system,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                return {
                    output_key: completion.content,
                    "_provider_used": provider.model_id,
                    "_usage": {
                        "provider": provider.model_id,
                        "input_tokens": completion.usage.input_tokens,
                        "output_tokens": completion.usage.output_tokens,
                        "cost_usd": completion.usage.cost_usd,
                    },
                }
            except Exception as e:
                last_error = e
                continue

        raise RuntimeError(f"All {len(providers)} providers failed. Last error: {last_error}")

    # Wrap with caching if policy is provided and temperature == 0
    if cache_policy and cache_policy.enabled and temperature == 0.0 and providers:
        target_cache = cache or _llm_cache
        first_model = providers[0].model_id

        def _cache_key_fn(state: dict[str, Any]) -> str:
            if prompt_template is not None:
                if callable(prompt_template):
                    content = prompt_template(state)
                else:
                    content = prompt_template.format(**state)
            else:
                content = str(state.get(prompt_key, ""))
            return make_cache_key(first_model, system, content)

        policy = CachePolicy(
            enabled=True,
            ttl_seconds=cache_policy.ttl_seconds,
            max_entries=cache_policy.max_entries,
            cache_key_fn=_cache_key_fn,
        )
        return cached_node(target_cache, policy)(node_func)

    return node_func


def chat_node(
    provider: Provider,
    system: str,
    messages_key: str = "messages",
    output_key: str = "messages",
    max_tokens: int = 4096,
    temperature: float = 0.0,
) -> Callable[[dict[str, Any]], Any]:
    """Create a node for multi-turn chat. Reads/writes a messages list.

    State must use append_reducer for the messages_key.
    Chat nodes are NOT cached because messages accumulate across turns.
    """

    async def node_func(state: dict[str, Any]) -> dict[str, Any]:
        messages = state.get(messages_key, [])

        # Convert state messages to provider Messages
        provider_messages = []
        for msg in messages:
            if isinstance(msg, Message):
                provider_messages.append(msg)
            elif isinstance(msg, dict):
                provider_messages.append(Message(role=Role(msg["role"]), content=msg["content"]))
            elif isinstance(msg, str):
                provider_messages.append(Message(role=Role.USER, content=msg))

        completion = await provider.complete(
            messages=provider_messages,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        return {
            output_key: [{"role": "assistant", "content": completion.content}],
            "_usage": {
                "provider": provider.model_id,
                "input_tokens": completion.usage.input_tokens,
                "output_tokens": completion.usage.output_tokens,
                "cost_usd": completion.usage.cost_usd,
            },
        }

    return node_func
