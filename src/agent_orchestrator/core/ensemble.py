"""Multi-model ensemble inference (scout finding #58).

``EnsembleProvider`` wraps N member providers behind the standard
:class:`~agent_orchestrator.core.provider.Provider` interface, so an ensemble
drops into any place a single provider fits (agents, graph nodes, routing).

Strategies:

- ``FIRST_SUCCESS`` — run members in parallel, return the fastest successful
  completion, cancel the rest. Latency win.
- ``CONSENSUS`` — run all members, return the majority answer (normalised
  content match); ties fall back to the first member's answer. Quality win
  for short factual outputs.
- ``BEST_OF`` — run all members, let a caller-supplied ``scorer`` pick.

Cost honesty: the returned ``usage`` aggregates tokens and cost of **every**
member call that ran — an ensemble is 2-5x the price of a single call and the
accounting must show that.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from enum import Enum
from typing import cast

from .provider import (
    Completion,
    Message,
    ModelCapabilities,
    Provider,
    StreamChunk,
    ToolDefinition,
    Usage,
)


class EnsembleStrategy(str, Enum):
    FIRST_SUCCESS = "first_success"
    CONSENSUS = "consensus"
    BEST_OF = "best_of"


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


class EnsembleProvider(Provider):
    """Provider that fans one request out to several member providers."""

    def __init__(
        self,
        providers: Sequence[Provider],
        strategy: EnsembleStrategy = EnsembleStrategy.FIRST_SUCCESS,
        scorer: Callable[[Completion], float] | None = None,
    ) -> None:
        if len(providers) < 2:
            raise ValueError("An ensemble needs at least 2 providers")
        if strategy == EnsembleStrategy.BEST_OF and scorer is None:
            raise ValueError("BEST_OF strategy requires a scorer")
        self._providers = list(providers)
        self._strategy = strategy
        self._scorer = scorer

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion:
        call = lambda p: p.complete(
            messages, tools=tools, system=system, max_tokens=max_tokens, temperature=temperature
        )

        if self._strategy == EnsembleStrategy.FIRST_SUCCESS:
            return await self._first_success(call)

        results = await asyncio.gather(*(call(p) for p in self._providers), return_exceptions=True)
        completions = [r for r in results if not isinstance(r, BaseException)]
        if not completions:
            first_error = next(r for r in results if isinstance(r, BaseException))
            raise first_error

        total_usage = self._aggregate_usage(completions)
        if self._strategy == EnsembleStrategy.CONSENSUS:
            winner = self._majority(completions)
        else:  # BEST_OF
            assert self._scorer is not None
            winner = max(completions, key=self._scorer)
        return Completion(
            content=winner.content,
            tool_calls=winner.tool_calls,
            usage=total_usage,
            stop_reason=winner.stop_reason,
        )

    async def _first_success(self, call) -> Completion:
        tasks = [asyncio.ensure_future(call(p)) for p in self._providers]
        errors: list[BaseException] = []
        usage_from_losers = Usage()
        try:
            for future in asyncio.as_completed(tasks):
                try:
                    winner = await future
                except BaseException as exc:  # noqa: BLE001 — collected and re-raised below
                    errors.append(exc)
                    continue
                combined = Usage(
                    input_tokens=winner.usage.input_tokens + usage_from_losers.input_tokens,
                    output_tokens=winner.usage.output_tokens + usage_from_losers.output_tokens,
                    cost_usd=winner.usage.cost_usd + usage_from_losers.cost_usd,
                )
                return Completion(
                    content=winner.content,
                    tool_calls=winner.tool_calls,
                    usage=combined,
                    stop_reason=winner.stop_reason,
                )
            raise errors[0]
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            # Cancelled members bill nothing further; awaiting suppresses warnings.
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _aggregate_usage(completions: list[Completion]) -> Usage:
        return Usage(
            input_tokens=sum(c.usage.input_tokens for c in completions),
            output_tokens=sum(c.usage.output_tokens for c in completions),
            cost_usd=sum(c.usage.cost_usd for c in completions),
        )

    @staticmethod
    def _majority(completions: list[Completion]) -> Completion:
        votes: dict[str, list[Completion]] = {}
        for completion in completions:
            votes.setdefault(_normalise(completion.content), []).append(completion)
        best = max(votes.values(), key=len)
        # Tie or no majority: the earliest member (highest-priority) wins.
        return best[0]

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamChunk]:
        # Streaming an ensemble degenerates to streaming the first member —
        # consensus/best-of need complete outputs to compare.
        stream = cast(
            AsyncIterator[StreamChunk],
            self._providers[0].stream(messages, tools=tools, system=system, max_tokens=max_tokens),
        )
        async for chunk in stream:
            yield chunk

    @property
    def model_id(self) -> str:
        members = "+".join(p.model_id for p in self._providers)
        return f"ensemble[{self._strategy.value}]({members})"

    @property
    def capabilities(self) -> ModelCapabilities:
        members = [p.capabilities for p in self._providers]
        return ModelCapabilities(
            max_context=min(c.max_context for c in members),
            supports_tools=all(c.supports_tools for c in members),
            supports_vision=all(c.supports_vision for c in members),
            supports_streaming=members[0].supports_streaming,
            max_output_tokens=min(c.max_output_tokens for c in members),
        )

    @property
    def input_cost_per_million(self) -> float:
        # Worst case: every member bills its input.
        return sum(p.input_cost_per_million for p in self._providers)

    @property
    def output_cost_per_million(self) -> float:
        return sum(p.output_cost_per_million for p in self._providers)
