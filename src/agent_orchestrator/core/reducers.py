"""Built-in reducer functions for state management.

Reducers define how state updates are merged. Each state key can have
its own reducer. If no reducer is specified, the value is overwritten.

Usage:
    graph = StateGraph(reducers={
        "messages": append_reducer,
        "counter": add_reducer,
        "errors": append_unique_reducer,
    })
"""

from __future__ import annotations

from typing import Any


def append_reducer(current: list | None, update: list | Any) -> list:
    """Append new items to a list. If update is not a list, wrap it."""
    current = current or []
    if isinstance(update, list):
        return current + update
    return current + [update]


def add_reducer(current: int | float | None, update: int | float) -> int | float:
    """Add numeric values."""
    return (current or 0) + update


def replace_reducer(current: Any, update: Any) -> Any:
    """Always replace with the new value (default behavior)."""
    return update


def merge_dict_reducer(current: dict | None, update: dict) -> dict:
    """Shallow merge two dicts."""
    result = dict(current or {})
    result.update(update)
    return result


def append_unique_reducer(current: list | None, update: list | Any) -> list:
    """Append only items not already in the list."""
    current = current or []
    if not isinstance(update, list):
        update = [update]
    return current + [item for item in update if item not in current]


def max_reducer(current: int | float | None, update: int | float) -> int | float:
    """Keep the maximum value."""
    if current is None:
        return update
    return max(current, update)


def last_non_none_reducer(current: Any, update: Any) -> Any:
    """Keep the last non-None value."""
    return update if update is not None else current
