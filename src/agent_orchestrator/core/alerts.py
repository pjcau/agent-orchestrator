"""Spend alerts — fire when cumulative cost crosses configurable thresholds."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Valid period values
PERIOD_TASK = "task"
PERIOD_SESSION = "session"
PERIOD_DAY = "day"

# Valid action values
ACTION_LOG = "log"
ACTION_WEBHOOK = "webhook"


@dataclass
class AlertRule:
    name: str
    threshold_usd: float
    period: str  # "task" | "session" | "day"
    action: str  # "log" | "webhook"
    webhook_url: str | None = None


@dataclass
class Alert:
    rule_name: str
    triggered_at: float
    current_spend: float
    threshold: float
    message: str


class AlertManager:
    """Check spend against rules and fire alerts when thresholds are exceeded."""

    def __init__(self, rules: list[AlertRule]) -> None:
        self._rules: dict[str, AlertRule] = {r.name: r for r in rules}
        self._triggered: list[Alert] = []
        # Track which (rule_name, period, task_id) combos have already fired
        # to avoid duplicate alerts within a single period.
        self._fired: set[str] = set()

    # ------------------------------------------------------------------
    # Core check
    # ------------------------------------------------------------------

    def check(
        self,
        current_spend: float,
        period: str,
        task_id: str | None = None,
    ) -> list[Alert]:
        """Evaluate all rules for the given period and spend amount.

        Returns the list of newly triggered alerts (rules that fired this call).
        Alerts that have already been triggered for the same (rule, period,
        task_id) key are suppressed to prevent duplicate firing.
        """
        new_alerts: list[Alert] = []

        for rule in self._rules.values():
            if rule.period != period:
                continue
            if current_spend < rule.threshold_usd:
                continue

            # Dedup key
            dedup_key = f"{rule.name}:{period}:{task_id or ''}"
            if dedup_key in self._fired:
                continue

            alert = Alert(
                rule_name=rule.name,
                triggered_at=time.time(),
                current_spend=current_spend,
                threshold=rule.threshold_usd,
                message=(
                    f"Alert '{rule.name}': spend ${current_spend:.4f} exceeded "
                    f"threshold ${rule.threshold_usd:.4f} for period '{period}'"
                    + (f" (task: {task_id})" if task_id else "")
                ),
            )

            self._triggered.append(alert)
            self._fired.add(dedup_key)
            new_alerts.append(alert)

            # Dispatch
            if rule.action == ACTION_LOG:
                logger.warning(alert.message)
            elif rule.action == ACTION_WEBHOOK:
                # Webhook dispatch deferred — log intent and store alert
                logger.warning(
                    "Webhook alert (dispatch deferred): url=%s message=%s",
                    rule.webhook_url,
                    alert.message,
                )

        return new_alerts

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_triggered_alerts(self) -> list[Alert]:
        """Return all alerts that have been triggered so far."""
        return list(self._triggered)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def clear_alerts(self) -> None:
        """Clear stored alerts and reset dedup tracking."""
        self._triggered.clear()
        self._fired.clear()

    def add_rule(self, rule: AlertRule) -> None:
        """Add or replace an alert rule."""
        self._rules[rule.name] = rule

    def remove_rule(self, name: str) -> None:
        """Remove a rule by name.  No-op if not found."""
        self._rules.pop(name, None)
