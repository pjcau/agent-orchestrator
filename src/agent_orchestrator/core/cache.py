"""Task-level result caching for graph nodes and skills.

Caches results by input hash to skip redundant LLM calls.
Supports InMemory and configurable TTL.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CachePolicy:
    """Per-node caching configuration."""

    enabled: bool = True
    ttl_seconds: int = 3600  # 1 hour default
    max_entries: int = 1000
    cache_key_fn: Any = None  # Optional custom key function


@dataclass
class CacheEntry:
    """A cached result."""

    key: str
    value: Any
    created_at: float = field(default_factory=time.time)
    ttl_seconds: int = 3600
    hit_count: int = 0
    node_name: str = ""

    @property
    def is_expired(self) -> bool:
        return time.time() - self.created_at > self.ttl_seconds


class CacheStats:
    """Track cache hit/miss statistics."""

    def __init__(self) -> None:
        self.hits: int = 0
        self.misses: int = 0
        self.evictions: int = 0
        self.total_saved_tokens: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "hit_rate": round(self.hit_rate, 3),
            "total_saved_tokens": self.total_saved_tokens,
        }


class BaseCache(ABC):
    """Abstract cache interface."""

    @abstractmethod
    def get(self, key: str) -> CacheEntry | None:
        """Get cached entry. Returns None on miss or expiry."""
        ...

    @abstractmethod
    def put(self, key: str, value: Any, ttl_seconds: int = 3600, node_name: str = "") -> None:
        """Store a result."""
        ...

    @abstractmethod
    def invalidate(self, key: str) -> bool:
        """Remove a specific entry. Returns True if existed."""
        ...

    @abstractmethod
    def clear(self) -> int:
        """Clear all entries. Returns count removed."""
        ...

    @abstractmethod
    def size(self) -> int:
        """Number of entries (including expired)."""
        ...

    @abstractmethod
    def get_stats(self) -> CacheStats:
        """Get hit/miss statistics."""
        ...


class InMemoryCache(BaseCache):
    """In-memory LRU-style cache with TTL support. Thread-safe via RLock."""

    def __init__(self, max_entries: int = 1000) -> None:
        self._store: dict[str, CacheEntry] = {}
        self._max_entries = max_entries
        self._stats = CacheStats()
        self._lock = threading.RLock()

    def get(self, key: str) -> CacheEntry | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._stats.misses += 1
                return None
            if entry.is_expired:
                del self._store[key]
                self._stats.misses += 1
                self._stats.evictions += 1
                return None
            entry.hit_count += 1
            self._stats.hits += 1
            return entry

    def put(self, key: str, value: Any, ttl_seconds: int = 3600, node_name: str = "") -> None:
        with self._lock:
            if len(self._store) >= self._max_entries and key not in self._store:
                self._evict_oldest()
            self._store[key] = CacheEntry(
                key=key,
                value=value,
                ttl_seconds=ttl_seconds,
                node_name=node_name,
            )

    def invalidate(self, key: str) -> bool:
        with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    def clear(self) -> int:
        with self._lock:
            count = len(self._store)
            self._store.clear()
            return count

    def size(self) -> int:
        with self._lock:
            return len(self._store)

    def get_stats(self) -> CacheStats:
        return self._stats

    def _evict_oldest(self) -> None:
        if not self._store:
            return
        oldest_key = min(self._store, key=lambda k: self._store[k].created_at)
        del self._store[oldest_key]
        self._stats.evictions += 1


def make_cache_key(*args: Any, **kwargs: Any) -> str:
    """Generate a deterministic cache key from arguments.

    Uses JSON serialization + SHA256 for stability.
    """
    try:
        payload = json.dumps(
            {"args": args, "kwargs": kwargs},
            sort_keys=True,
            default=str,
        )
    except (TypeError, ValueError):
        payload = str(args) + str(sorted(kwargs.items()))
    return hashlib.sha256(payload.encode()).hexdigest()


def cached_node(cache: BaseCache, policy: CachePolicy | None = None):
    """Decorator to add caching to a graph node function.

    Usage:
        cache = InMemoryCache()

        @cached_node(cache, CachePolicy(ttl_seconds=600))
        async def my_node(state):
            # expensive LLM call
            return {"result": ...}
    """
    policy = policy or CachePolicy()

    def decorator(func):
        import functools

        @functools.wraps(func)
        async def wrapper(state: dict[str, Any]) -> dict[str, Any] | None:
            if not policy.enabled:
                return await func(state)

            # Generate cache key from relevant state
            if policy.cache_key_fn:
                key = policy.cache_key_fn(state)
            else:
                key = make_cache_key(func.__name__, state)

            # Check cache
            entry = cache.get(key)
            if entry is not None:
                return entry.value

            # Execute and cache
            result = await func(state)
            if result is not None:
                cache.put(
                    key,
                    result,
                    ttl_seconds=policy.ttl_seconds,
                    node_name=func.__name__,
                )
            return result

        wrapper._cache = cache
        wrapper._cache_policy = policy
        return wrapper

    return decorator
