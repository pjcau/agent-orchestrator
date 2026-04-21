"""Atomic task decomposition validator (PR #59).

Guards against overly large or multi-intent sub-tasks before they reach
agent sub-loops, which is the #1 cause of anti-stall failures in team
runs. Flags tasks that are:

- Too long (raw character count)
- Have too many imperative verbs (likely several tasks glued together)
- Contain "and then"/"and also" conjunctions (explicit serialisation)

This is a lint, not a hard gate — callers decide whether to split, warn,
or reject. Recording the count via ``tasks_rejected_too_complex_total``
lets the dashboard visualise when the team-lead is producing noisy
decompositions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# A small but representative set of action-starting imperatives in English.
# Complete coverage is impossible; this is tuned for false-positive
# avoidance (fewer, stronger hits) rather than recall.
_IMPERATIVE_VERBS = {
    "add",
    "build",
    "create",
    "define",
    "delete",
    "deploy",
    "design",
    "document",
    "draft",
    "fix",
    "generate",
    "implement",
    "install",
    "integrate",
    "migrate",
    "publish",
    "refactor",
    "register",
    "release",
    "remove",
    "run",
    "set",
    "ship",
    "test",
    "update",
    "write",
}

_CONJUNCTION_RE = re.compile(
    r"\b(?:and then|and also|after that|additionally|furthermore|"
    r"moreover|finally|then proceed to)\b",
    flags=re.IGNORECASE,
)

_WORD_RE = re.compile(r"[a-zA-Z]+")


@dataclass(frozen=True)
class AtomicTaskIssue:
    """One finding about a specific task in the decomposition."""

    index: int
    agent: str
    task: str
    reason: str


def _count_imperatives(task: str) -> int:
    words = _WORD_RE.findall(task.lower())
    # Count unique imperatives rather than total occurrences — avoids
    # penalising tasks that repeat the same verb ("test this, test that").
    return sum(1 for verb in set(words) if verb in _IMPERATIVE_VERBS)


def validate_atomic_tasks(
    assignments: list[dict[str, Any]],
    *,
    max_imperatives: int = 5,
    max_chars: int = 800,
    max_conjunctions: int = 1,
) -> list[AtomicTaskIssue]:
    """Return a list of issues — empty means the decomposition is atomic.

    Args:
        assignments: list of ``{"agent", "task"}`` dicts produced by the
            team-lead planner.
        max_imperatives: upper bound on distinct imperative verbs per task.
        max_chars: upper bound on task description length in characters.
        max_conjunctions: upper bound on sequencing phrases per task.
    """
    issues: list[AtomicTaskIssue] = []
    for i, entry in enumerate(assignments):
        agent = str(entry.get("agent", ""))
        task = str(entry.get("task", ""))

        if len(task) > max_chars:
            issues.append(
                AtomicTaskIssue(
                    index=i,
                    agent=agent,
                    task=task,
                    reason=f"task too long: {len(task)} chars (max {max_chars})",
                )
            )
            continue

        imperatives = _count_imperatives(task)
        if imperatives > max_imperatives:
            issues.append(
                AtomicTaskIssue(
                    index=i,
                    agent=agent,
                    task=task,
                    reason=(
                        f"too many imperatives ({imperatives} > {max_imperatives}); "
                        "split into smaller sub-tasks"
                    ),
                )
            )
            continue

        conj_count = len(_CONJUNCTION_RE.findall(task))
        if conj_count > max_conjunctions:
            issues.append(
                AtomicTaskIssue(
                    index=i,
                    agent=agent,
                    task=task,
                    reason=(
                        f"multi-step language detected ({conj_count} conjunctions); "
                        "consider sequencing across turns"
                    ),
                )
            )

    return issues


def record_issues(issues: list[AtomicTaskIssue], metrics: Any) -> None:
    """Increment the ``tasks_rejected_too_complex_total`` counter per issue."""
    if metrics is None or not issues:
        return
    counter = metrics.counter(
        "tasks_rejected_too_complex_total",
        "Total sub-tasks flagged as non-atomic during decomposition",
    )
    counter.inc(len(issues))
