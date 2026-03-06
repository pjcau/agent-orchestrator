"""Model benchmarking — compare providers on latency, throughput, and cost."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .provider import Message, Provider, Role


@dataclass
class BenchmarkResult:
    provider_key: str
    model_id: str
    task_type: str
    latency_ms: float
    tokens_per_second: float
    output_quality: Optional[float]  # 0-1, if measured; None if not applicable
    cost_usd: float


class BenchmarkSuite:
    """Run benchmarks against one or more providers and store results.

    Usage::

        suite = BenchmarkSuite()
        result = await suite.run_benchmark(provider, task="Explain recursion.", task_type="reasoning")
        best = suite.get_best_for_task("reasoning")
    """

    def __init__(self) -> None:
        self._results: list[BenchmarkResult] = []

    async def run_benchmark(
        self,
        provider: Provider,
        task: str,
        task_type: str,
        provider_key: str = "",
    ) -> BenchmarkResult:
        """Run a single benchmark and store the result.

        Args:
            provider: the provider to benchmark
            task: the task string to send
            task_type: a free-form label (e.g. "coding", "reasoning", "summarisation")
            provider_key: registry key for this provider (used as identifier in results);
                falls back to provider.model_id if empty
        """
        key = provider_key or provider.model_id
        messages = [Message(role=Role.USER, content=task)]

        t0 = time.monotonic()
        completion = await provider.complete(messages)
        elapsed_ms = (time.monotonic() - t0) * 1000.0

        output_tokens = completion.usage.output_tokens or 1
        tokens_per_second = (
            output_tokens / (elapsed_ms / 1000.0) if elapsed_ms > 0 else 0.0
        )

        result = BenchmarkResult(
            provider_key=key,
            model_id=provider.model_id,
            task_type=task_type,
            latency_ms=elapsed_ms,
            tokens_per_second=tokens_per_second,
            output_quality=None,
            cost_usd=completion.usage.cost_usd,
        )
        self._results.append(result)
        return result

    async def compare_models(
        self,
        providers: dict[str, Provider],
        task: str,
        task_type: str = "general",
    ) -> list[BenchmarkResult]:
        """Run the same task on multiple providers and return all results.

        Results are appended to the internal store and also returned sorted by
        latency (fastest first).
        """
        results: list[BenchmarkResult] = []
        for key, provider in providers.items():
            result = await self.run_benchmark(
                provider=provider,
                task=task,
                task_type=task_type,
                provider_key=key,
            )
            results.append(result)

        results.sort(key=lambda r: r.latency_ms)
        return results

    def get_results(self) -> list[BenchmarkResult]:
        """Return all stored benchmark results."""
        return list(self._results)

    def get_best_for_task(self, task_type: str) -> BenchmarkResult | None:
        """Return the result with the lowest latency for the given task_type.

        If output_quality is available for some results, those are preferred
        (highest quality first), with latency as a tiebreaker.
        """
        matching = [r for r in self._results if r.task_type == task_type]
        if not matching:
            return None

        # Prefer results that have an output_quality score
        with_quality = [r for r in matching if r.output_quality is not None]
        if with_quality:
            return max(
                with_quality,
                key=lambda r: (r.output_quality or 0.0, -r.latency_ms),
            )

        # Fall back to fastest (lowest latency)
        return min(matching, key=lambda r: r.latency_ms)
