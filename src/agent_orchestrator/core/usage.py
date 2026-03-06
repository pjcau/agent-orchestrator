"""Usage tracking and budget enforcement — records token/cost data per call."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class UsageRecord:
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    timestamp: float = field(default_factory=time.time)
    task_id: str | None = None
    agent_name: str | None = None


@dataclass
class BudgetConfig:
    max_per_task: Optional[float] = None    # USD cap for a single task_id
    max_per_session: Optional[float] = None  # USD cap for the current session
    max_per_day: Optional[float] = None     # USD cap for the current calendar day


@dataclass
class BudgetStatus:
    within_budget: bool
    remaining_usd: float | None  # None if no applicable limit
    limit_type: str | None       # "task", "session", "day", or None


@dataclass
class CostBreakdown:
    local_cost: float
    cloud_cost: float
    total_cost: float
    local_tokens: int
    cloud_tokens: int
    by_provider: dict[str, float]
    by_agent: dict[str, float]


# Provider keys that are considered "local" (zero or near-zero cost).
_LOCAL_PROVIDER_PREFIXES = ("local", "ollama", "vllm", "lmstudio")


def _is_local(provider: str) -> bool:
    return any(provider.lower().startswith(p) for p in _LOCAL_PROVIDER_PREFIXES)


class UsageTracker:
    """In-memory store for token usage and cost records.

    All costs are in USD. Timestamps are Unix epoch floats (time.time()).
    """

    def __init__(self) -> None:
        self._records: list[UsageRecord] = []
        self._session_start: float = time.time()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(self, record: UsageRecord) -> None:
        """Append a usage record."""
        self._records.append(record)

    # ------------------------------------------------------------------
    # Budget checking
    # ------------------------------------------------------------------

    def check_budget(
        self, budget: BudgetConfig, task_id: str | None = None
    ) -> BudgetStatus:
        """Check whether the current usage is within the given budget.

        Checks are applied in order: task → session → day.
        Returns on the first limit that is breached (or nearest to breach).
        """
        # Per-task check
        if budget.max_per_task is not None and task_id is not None:
            task_cost = sum(
                r.cost_usd for r in self._records if r.task_id == task_id
            )
            remaining = budget.max_per_task - task_cost
            if remaining < 0:
                return BudgetStatus(
                    within_budget=False,
                    remaining_usd=remaining,
                    limit_type="task",
                )

        # Per-session check
        if budget.max_per_session is not None:
            session_cost = self.get_session_cost()
            remaining = budget.max_per_session - session_cost
            if remaining < 0:
                return BudgetStatus(
                    within_budget=False,
                    remaining_usd=remaining,
                    limit_type="session",
                )

        # Per-day check
        if budget.max_per_day is not None:
            daily_cost = self.get_daily_cost()
            remaining = budget.max_per_day - daily_cost
            if remaining < 0:
                return BudgetStatus(
                    within_budget=False,
                    remaining_usd=remaining,
                    limit_type="day",
                )

        # Within all limits — report the tightest remaining budget
        remaining_values: list[float] = []
        if budget.max_per_task is not None and task_id is not None:
            task_cost = sum(
                r.cost_usd for r in self._records if r.task_id == task_id
            )
            remaining_values.append(budget.max_per_task - task_cost)
        if budget.max_per_session is not None:
            remaining_values.append(budget.max_per_session - self.get_session_cost())
        if budget.max_per_day is not None:
            remaining_values.append(budget.max_per_day - self.get_daily_cost())

        tightest = min(remaining_values) if remaining_values else None
        return BudgetStatus(within_budget=True, remaining_usd=tightest, limit_type=None)

    # ------------------------------------------------------------------
    # Aggregate queries
    # ------------------------------------------------------------------

    def get_session_cost(self) -> float:
        """Total cost since the tracker was instantiated."""
        return sum(r.cost_usd for r in self._records)

    def get_daily_cost(self) -> float:
        """Total cost for records timestamped within the current UTC day."""
        # Midnight of the current day in local time (seconds since epoch)
        now = time.time()
        # Calculate seconds since midnight
        seconds_since_midnight = now % 86400
        day_start = now - seconds_since_midnight
        return sum(r.cost_usd for r in self._records if r.timestamp >= day_start)

    def get_cost_by_provider(self) -> dict[str, float]:
        """Total cost grouped by provider key."""
        result: dict[str, float] = {}
        for r in self._records:
            result[r.provider] = result.get(r.provider, 0.0) + r.cost_usd
        return result

    def get_cost_by_agent(self) -> dict[str, float]:
        """Total cost grouped by agent name (None-keyed entries use '__unknown__')."""
        result: dict[str, float] = {}
        for r in self._records:
            key = r.agent_name or "__unknown__"
            result[key] = result.get(key, 0.0) + r.cost_usd
        return result

    def get_records(self, since: float | None = None) -> list[UsageRecord]:
        """Return all records, optionally filtered to those after *since* (epoch)."""
        if since is None:
            return list(self._records)
        return [r for r in self._records if r.timestamp >= since]

    def get_cost_breakdown(self) -> CostBreakdown:
        """Split costs and tokens into local vs cloud."""
        local_cost = 0.0
        cloud_cost = 0.0
        local_tokens = 0
        cloud_tokens = 0

        for r in self._records:
            tokens = r.input_tokens + r.output_tokens
            if _is_local(r.provider):
                local_cost += r.cost_usd
                local_tokens += tokens
            else:
                cloud_cost += r.cost_usd
                cloud_tokens += tokens

        return CostBreakdown(
            local_cost=local_cost,
            cloud_cost=cloud_cost,
            total_cost=local_cost + cloud_cost,
            local_tokens=local_tokens,
            cloud_tokens=cloud_tokens,
            by_provider=self.get_cost_by_provider(),
            by_agent=self.get_cost_by_agent(),
        )
