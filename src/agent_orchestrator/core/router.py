"""Smart task router — selects the best provider/model based on complexity, cost,
and real-time health data.

Complexity classification inspired by:
- tzachbon/claude-model-router-hook (regex-based tier matching)
- flatrick/everything-claude-code cost-aware-llm-pipeline (threshold routing)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from .health import HealthMonitor
from .orchestrator import TaskComplexity
from .provider import Provider

# Rust acceleration (optional — falls back to pure Python)
try:
    from _agent_orchestrator_rust import RustClassifier as _RustClassifier

    _HAS_RUST = True
except ImportError:
    _HAS_RUST = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Complexity classifier
# ---------------------------------------------------------------------------

# High complexity: architecture, deep analysis, multi-system reasoning
_HIGH_KEYWORDS: frozenset[str] = frozenset(
    {
        "architect",
        "architecture",
        "design",
        "optimize",
        "refactor",
        "security audit",
        "performance",
        "distributed",
        "scalability",
        "migration",
        "machine learning",
        "neural",
        "inference",
        "complex",
        "extensive",
        "multi-step",
        "multistep",
        "comprehensive",
        "analyse",
        "analyze",
        "reasoning",
        "strategy",
        "evaluate",
        "compare",
        "tradeoff",
        "trade-off",
        "deep dive",
        "redesign",
        "across the codebase",
        "multi-system",
        "plan mode",
    }
)

# Low complexity: simple ops, git commands, formatting, lookups
_LOW_KEYWORDS: frozenset[str] = frozenset(
    {
        "summarize",
        "summarise",
        "list",
        "simple",
        "basic",
        "quick",
        "brief",
        "short",
        "translate",
        "format",
        "fix typo",
        "rename",
        "echo",
        "hello",
        "ping",
        "status",
        "check",
    }
)

# Regex patterns for low-complexity tasks (git ops, renames, formatting)
_LOW_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p)
    for p in [
        r"\bgit\s+(commit|push|pull|status|log|diff|add|stash|branch|merge)\b",
        r"\brename\b",
        r"\bmove\s+file\b",
        r"\bdelete\s+file\b",
        r"\bformat\b",
        r"\blint\b",
        r"\bprettier\b",
        r"\beslint\b",
        r"\bremove\s+(unused|dead)\b",
        r"\bupdate\s+(version|package)\b",
    ]
]

# Thresholds (inspired by cost-aware-llm-pipeline)
_HIGH_WORD_THRESHOLD = 200  # long prompts likely need strong model
_LOW_WORD_CEILING = 30  # short prompts default to low if no signals


class TaskComplexityClassifier:
    """Classify task complexity using keyword + regex heuristics — no LLM call.

    Three-tier classification (low/medium/high) using:
    - Keyword matching against curated word sets
    - Regex patterns for common low-complexity operations
    - Word count thresholds for length-based signals

    Uses Rust accelerated classifier when available.
    """

    def __init__(self) -> None:
        self._rust = _RustClassifier() if _HAS_RUST else None

    def classify(self, task: str) -> TaskComplexity:
        if self._rust:
            rc = self._rust.classify(task)
            return TaskComplexity(
                level=rc.level,
                estimated_tokens=rc.estimated_tokens,
                requires_tools=rc.requires_tools,
                requires_reasoning=rc.requires_reasoning,
            )
        return self._classify_python(task)

    def _classify_python(self, task: str) -> TaskComplexity:
        """Return a TaskComplexity for the given task description string."""
        lower = task.lower()

        high_hits = sum(1 for kw in _HIGH_KEYWORDS if kw in lower)
        low_hits = sum(1 for kw in _LOW_KEYWORDS if kw in lower)
        low_regex = sum(1 for p in _LOW_PATTERNS if p.search(lower))

        # Rough token estimate: ~1.3 tokens per word
        word_count = len(task.split())
        estimated_tokens = max(500, int(word_count * 1.3) + 1500)

        requires_reasoning = high_hits > 0 or word_count > _HIGH_WORD_THRESHOLD
        requires_tools = any(
            kw in lower for kw in ("code", "file", "run", "execute", "test", "deploy", "write")
        )

        # Classification with combined signals
        low_score = low_hits + low_regex
        if high_hits > low_score or word_count > _HIGH_WORD_THRESHOLD * 1.5:
            level = "high"
        elif low_score > high_hits or (word_count < _LOW_WORD_CEILING and high_hits == 0):
            level = "low"
        else:
            level = "medium"

        return TaskComplexity(
            level=level,
            estimated_tokens=estimated_tokens,
            requires_tools=requires_tools,
            requires_reasoning=requires_reasoning,
        )


# ---------------------------------------------------------------------------
# Routing strategies
# ---------------------------------------------------------------------------


class RoutingStrategy(str, Enum):
    LOCAL_FIRST = "local_first"
    COST_OPTIMIZED = "cost_optimized"
    CAPABILITY_BASED = "capability_based"
    FALLBACK_CHAIN = "fallback_chain"
    COMPLEXITY_BASED = "complexity_based"
    SPLIT_EXECUTION = "split_execution"  # interface stub only


# Provider keys considered "local" (Ollama, vLLM, etc.)
_LOCAL_PREFIXES = ("local", "ollama", "vllm", "lmstudio")


def _is_local(key: str) -> bool:
    return any(key.lower().startswith(p) for p in _LOCAL_PREFIXES)


# ---------------------------------------------------------------------------
# TaskRouter
# ---------------------------------------------------------------------------


@dataclass
class RouterConfig:
    strategy: RoutingStrategy = RoutingStrategy.COMPLEXITY_BASED
    fallback_chain: list[str] = field(default_factory=list)
    # Min coding_quality score required when task needs coding
    min_coding_quality: float = 0.5
    # Min reasoning_quality score required when task needs reasoning
    min_reasoning_quality: float = 0.5
    # Context length required (tokens) — providers with smaller max_context excluded
    min_context_tokens: int = 0


class TaskRouter:
    """Select the best provider for a task given a routing strategy.

    Args:
        providers: mapping of provider_key -> Provider instance
        health_monitor: shared HealthMonitor (can be the same instance used by
            the orchestrator so health data is shared)
        config: routing behaviour
    """

    def __init__(
        self,
        providers: dict[str, Provider],
        health_monitor: HealthMonitor | None = None,
        config: RouterConfig | None = None,
    ) -> None:
        self._providers = providers
        self._health = health_monitor or HealthMonitor()
        self._config = config or RouterConfig()
        self._classifier = TaskComplexityClassifier()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(
        self,
        task: str,
        complexity: TaskComplexity | None = None,
        required_capabilities: dict | None = None,
    ) -> Provider | None:
        """Return the best Provider for the given task, or None if none qualify."""
        if complexity is None:
            complexity = self._classifier.classify(task)

        strategy = self._config.strategy

        if strategy == RoutingStrategy.LOCAL_FIRST:
            return self._local_first(complexity)
        if strategy == RoutingStrategy.COST_OPTIMIZED:
            return self._cost_optimized(complexity)
        if strategy == RoutingStrategy.CAPABILITY_BASED:
            return self._capability_based(complexity)
        if strategy == RoutingStrategy.FALLBACK_CHAIN:
            return self._fallback_chain()
        if strategy == RoutingStrategy.COMPLEXITY_BASED:
            return self._complexity_based(complexity)
        if strategy == RoutingStrategy.SPLIT_EXECUTION:
            # Interface stub: fall back to complexity_based for now
            return self._complexity_based(complexity)

        return None

    def get_classifier(self) -> TaskComplexityClassifier:
        return self._classifier

    # ------------------------------------------------------------------
    # Strategy implementations
    # ------------------------------------------------------------------

    def _available_providers(self) -> list[tuple[str, Provider]]:
        """Return (key, provider) pairs whose health is considered available."""
        return [(k, p) for k, p in self._providers.items() if self._health.is_available(k)]

    def _local_first(self, complexity: TaskComplexity) -> Provider | None:
        """Prefer local models; fall back to cloud only when unavailable or task is high."""
        available = self._available_providers()

        local = [(k, p) for k, p in available if _is_local(k)]
        cloud = [(k, p) for k, p in available if not _is_local(k)]

        if local and complexity.level != "high":
            # Pick the local provider with the best health score
            best_key = self._health.get_best_provider([k for k, _ in local])
            if best_key:
                return self._providers[best_key]

        if cloud:
            best_key = self._health.get_best_provider([k for k, _ in cloud])
            if best_key:
                return self._providers[best_key]

        # Last resort: any available
        if available:
            return available[0][1]
        return None

    def _cost_optimized(self, complexity: TaskComplexity) -> Provider | None:
        """Pick cheapest provider able to handle the task complexity."""
        available = self._available_providers()
        if not available:
            return None

        # Filter by context length requirement
        cfg = self._config
        candidates = [
            (k, p) for k, p in available if p.capabilities.max_context >= cfg.min_context_tokens
        ]
        if not candidates:
            candidates = available

        sorted_by_cost = sorted(
            candidates,
            key=lambda kp: kp[1].output_cost_per_million,
        )

        if complexity.level == "low":
            return sorted_by_cost[0][1]
        if complexity.level == "high":
            return sorted_by_cost[-1][1]
        # medium — pick the middle tier
        mid = len(sorted_by_cost) // 2
        return sorted_by_cost[mid][1]

    def _capability_based(self, complexity: TaskComplexity) -> Provider | None:
        """Match provider capabilities to task requirements."""
        available = self._available_providers()
        if not available:
            return None

        cfg = self._config
        candidates: list[tuple[str, Provider]] = []

        for key, provider in available:
            caps = provider.capabilities
            if caps.max_context < cfg.min_context_tokens:
                continue
            if complexity.requires_tools and not caps.supports_tools:
                continue
            if complexity.requires_reasoning and caps.reasoning_quality < cfg.min_reasoning_quality:
                continue
            candidates.append((key, provider))

        if not candidates:
            candidates = available  # relax constraints

        # Score: combination of coding + reasoning quality, minus cost penalty
        def _score(kp: tuple[str, Provider]) -> float:
            caps = kp[1].capabilities
            cost_penalty = kp[1].output_cost_per_million / 100.0
            return caps.coding_quality + caps.reasoning_quality - cost_penalty

        return max(candidates, key=_score)[1]

    def _fallback_chain(self) -> Provider | None:
        """Try providers in the configured fallback chain order, skip unhealthy ones."""
        chain = self._config.fallback_chain
        for key in chain:
            if key in self._providers and self._health.is_available(key):
                return self._providers[key]
        # If nothing in chain is available, try any healthy provider
        available = self._available_providers()
        return available[0][1] if available else None

    def _complexity_based(self, complexity: TaskComplexity) -> Provider | None:
        """Route by complexity tier: low->local, medium->mid-tier cloud, high->top-tier."""
        available = self._available_providers()
        if not available:
            return None

        local = [(k, p) for k, p in available if _is_local(k)]
        cloud = [(k, p) for k, p in available if not _is_local(k)]

        if complexity.level == "low":
            # Prefer local
            if local:
                best = self._health.get_best_provider([k for k, _ in local])
                if best:
                    return self._providers[best]
            # Fallback: cheapest cloud
            if cloud:
                return min(cloud, key=lambda kp: kp[1].output_cost_per_million)[1]

        if complexity.level == "high":
            # Prefer highest-capability cloud provider
            if cloud:
                return max(
                    cloud,
                    key=lambda kp: (
                        kp[1].capabilities.reasoning_quality + kp[1].capabilities.coding_quality
                    ),
                )[1]
            # Fallback: any local
            if local:
                best = self._health.get_best_provider([k for k, _ in local])
                if best:
                    return self._providers[best]

        # medium — mid-tier cloud (sorted by cost, pick middle)
        if cloud:
            sorted_cloud = sorted(cloud, key=lambda kp: kp[1].output_cost_per_million)
            mid = len(sorted_cloud) // 2
            return sorted_cloud[mid][1]
        if local:
            best = self._health.get_best_provider([k for k, _ in local])
            if best:
                return self._providers[best]

        return available[0][1]
