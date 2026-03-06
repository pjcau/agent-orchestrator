"""Advanced graph patterns — composable utilities on top of StateGraph.

Provides sub-graphs, retry/loop control, map-reduce parallelism, and
provider-selection annotations. None of these modify graph.py; they
produce plain NodeFunc callables that slot into any StateGraph.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from .graph import CompiledGraph, NodeFunc, State
from .provider import Provider


# ---------------------------------------------------------------------------
# SubGraphNode
# ---------------------------------------------------------------------------

class SubGraphNode:
    """Wrap a CompiledGraph as a callable node function for parent graphs.

    Args:
        graph: A compiled sub-graph.
        input_mapping: Maps parent state keys to sub-graph input keys.
            If empty the full parent state is passed through unchanged.
        output_mapping: Maps sub-graph output keys to parent state keys.
            If empty the full sub-graph result state is merged back.
    """

    def __init__(
        self,
        graph: CompiledGraph,
        input_mapping: dict[str, str] | None = None,
        output_mapping: dict[str, str] | None = None,
    ) -> None:
        self._graph = graph
        self._input_mapping = input_mapping or {}
        self._output_mapping = output_mapping or {}

    async def __call__(self, state: State) -> State:
        # Build sub-state from parent state using input_mapping.
        # input_mapping: parent_key -> sub_key
        if self._input_mapping:
            sub_input: State = {}
            for parent_key, sub_key in self._input_mapping.items():
                if parent_key in state:
                    sub_input[sub_key] = state[parent_key]
        else:
            sub_input = dict(state)

        result = await self._graph.invoke(sub_input)
        sub_state = result.state

        # Map sub-graph outputs back to parent state keys.
        # output_mapping: sub_key -> parent_key
        if self._output_mapping:
            partial: State = {}
            for sub_key, parent_key in self._output_mapping.items():
                if sub_key in sub_state:
                    partial[parent_key] = sub_state[sub_key]
        else:
            # Merge everything the sub-graph produced back into parent.
            partial = dict(sub_state)

        return partial


# ---------------------------------------------------------------------------
# retry_node
# ---------------------------------------------------------------------------

def retry_node(
    node_func: NodeFunc,
    max_retries: int = 3,
    upgrade_providers: list[Provider] | None = None,
) -> NodeFunc:
    """Wrap a node function with retry logic.

    On failure retries up to *max_retries* times.  If *upgrade_providers* is
    given the wrapped function expects the node to accept a ``_provider``
    keyword in state; on each retry the next provider from the list is
    injected into state under ``_provider`` so that provider-aware node
    factories can pick it up.

    Retry metadata is written to ``state["_retry_info"]``.
    """

    async def wrapped(state: State) -> State:
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            current_state = dict(state)
            current_state["_retry_info"] = {
                "attempt": attempt,
                "max_retries": max_retries,
            }

            if upgrade_providers and attempt < len(upgrade_providers):
                current_state["_provider"] = upgrade_providers[attempt]

            try:
                result = await node_func(current_state)
                update: State = result if result is not None else {}
                update["_retry_info"] = {
                    "attempt": attempt,
                    "max_retries": max_retries,
                    "succeeded": True,
                }
                return update
            except Exception as exc:
                last_exc = exc
                current_state["_retry_info"]["last_error"] = str(exc)

        raise RuntimeError(
            f"Node failed after {max_retries} retries. Last error: {last_exc}"
        ) from last_exc

    return wrapped


# ---------------------------------------------------------------------------
# loop_node
# ---------------------------------------------------------------------------

def loop_node(
    node_func: NodeFunc,
    condition: Callable[[State], bool],
    max_iterations: int = 10,
) -> NodeFunc:
    """Wrap a node to loop until *condition* returns False.

    Executes *node_func* repeatedly, merging each partial update back into
    state, until ``condition(state)`` is False or *max_iterations* is reached.
    The iteration count is stored in ``state["_loop_iterations"]``.
    """

    async def wrapped(state: State) -> State:
        current = dict(state)
        iterations = 0

        while condition(current) and iterations < max_iterations:
            update = await node_func(current)
            if update:
                current = {**current, **update}
            iterations += 1
            current["_loop_iterations"] = iterations

        current["_loop_iterations"] = iterations
        # Return only the keys that changed relative to the original state.
        changed: State = {}
        for key, value in current.items():
            if key not in state or state[key] != value:
                changed[key] = value
        return changed

    return wrapped


# ---------------------------------------------------------------------------
# map_reduce_node
# ---------------------------------------------------------------------------

def map_reduce_node(
    map_func: NodeFunc,
    reduce_func: Callable[[list[State]], State],
    items_key: str = "items",
    output_key: str = "results",
    max_concurrency: int = 10,
) -> NodeFunc:
    """Create a map-reduce node.

    1. Read ``state[items_key]`` — must be a list.
    2. For each item call *map_func* with ``state | {"_item": item, "_item_index": i}``.
    3. Collect all results (run in parallel, limited by *max_concurrency*).
    4. Call ``reduce_func(results_list)`` to merge.
    5. Return ``{output_key: reduced_result}``.
    """

    async def wrapped(state: State) -> State:
        items: list[Any] = state.get(items_key, [])
        semaphore = asyncio.Semaphore(max_concurrency)

        async def map_one(item: Any, index: int) -> State:
            item_state = {**state, "_item": item, "_item_index": index}
            async with semaphore:
                result = await map_func(item_state)
            return result if result is not None else {}

        results: list[State] = await asyncio.gather(
            *[map_one(item, i) for i, item in enumerate(items)]
        )

        reduced = reduce_func(results)
        return {output_key: reduced}

    return wrapped


# ---------------------------------------------------------------------------
# provider_annotated_node
# ---------------------------------------------------------------------------

_LOCAL_PREFIXES = ("local", "ollama", "vllm", "llamacpp", "lmstudio")


def _is_local(provider: Provider) -> bool:
    model_lower = provider.model_id.lower()
    return any(model_lower.startswith(prefix) for prefix in _LOCAL_PREFIXES)


def provider_annotated_node(
    providers: dict[str, Provider],
    node_func_factory: Callable[[Provider], NodeFunc],
    preferred: str = "any",
    fallback: str | None = None,
) -> NodeFunc:
    """Create a node with provider preference annotation.

    Selects a provider from *providers* dict based on *preferred*:

    - ``"local"``: first provider whose model_id starts with a local prefix
      (``local``, ``ollama``, ``vllm``, ``llamacpp``, ``lmstudio``).
    - ``"cloud"``: first provider that is NOT local.
    - ``"any"``: first available provider (insertion order).
    - Any other string: treated as a key into *providers*.

    If the preferred selection finds no match and *fallback* is set, the
    provider at ``providers[fallback]`` is used.  If that also fails a
    ``RuntimeError`` is raised at call time.
    """

    def _select() -> Provider | None:
        if preferred == "any":
            if providers:
                return next(iter(providers.values()))
            return None
        if preferred == "local":
            for p in providers.values():
                if _is_local(p):
                    return p
            return None
        if preferred == "cloud":
            for p in providers.values():
                if not _is_local(p):
                    return p
            return None
        # Specific key.
        return providers.get(preferred)

    selected = _select()
    if selected is None and fallback is not None:
        selected = providers.get(fallback)
    if selected is None:
        raise ValueError(
            f"provider_annotated_node: could not resolve provider "
            f"(preferred={preferred!r}, fallback={fallback!r}, "
            f"available={list(providers.keys())})"
        )

    node_func = node_func_factory(selected)

    async def wrapped(state: State) -> State:
        return await node_func(state)  # type: ignore[return-value]

    return wrapped


# ---------------------------------------------------------------------------
# long_context_node
# ---------------------------------------------------------------------------

def long_context_node(
    providers: dict[str, Provider],
    node_func_factory: Callable[[Provider], NodeFunc],
    min_context: int = 128_000,
) -> NodeFunc:
    """Auto-route to a provider with sufficient context window.

    Filters *providers* by ``capabilities.max_context >= min_context``.
    If no provider meets the minimum, falls back to the provider with the
    largest context window.
    """

    def _select() -> Provider:
        eligible = [p for p in providers.values() if p.capabilities.max_context >= min_context]
        if eligible:
            return max(eligible, key=lambda p: p.capabilities.max_context)
        if not providers:
            raise ValueError("long_context_node: providers dict is empty")
        return max(providers.values(), key=lambda p: p.capabilities.max_context)

    selected = _select()
    node_func = node_func_factory(selected)

    async def wrapped(state: State) -> State:
        return await node_func(state)  # type: ignore[return-value]

    return wrapped
