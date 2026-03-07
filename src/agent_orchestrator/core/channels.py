"""Typed channels for state management.

Channels control how concurrent writes to the same state key are handled.
Each state key can be assigned a channel type via StateGraph's channel_config.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar, Sequence, Callable
from dataclasses import dataclass

V = TypeVar("V")  # Value type
U = TypeVar("U")  # Update type


class EmptyChannelError(Exception):
    """Raised when reading from an empty channel."""
    pass


class InvalidUpdateError(Exception):
    """Raised when a channel receives conflicting updates."""
    pass


class BaseChannel(ABC, Generic[V, U]):
    """Base class for all channels."""

    @abstractmethod
    def get(self) -> V:
        """Get current value. Raises EmptyChannelError if empty."""
        ...

    @abstractmethod
    def update(self, values: Sequence[U]) -> bool:
        """Apply updates. Returns True if value changed."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Whether the channel has a value."""
        ...

    @abstractmethod
    def checkpoint(self) -> Any:
        """Serialize for persistence."""
        ...

    @abstractmethod
    def from_checkpoint(self, data: Any) -> None:
        """Restore from checkpoint."""
        ...

    def reset(self) -> None:
        """Called between supersteps for ephemeral channels."""
        pass


_MISSING = object()


class LastValue(BaseChannel[V, V]):
    """Stores exactly one value. Error if multiple writers per step."""

    def __init__(self, default: V | object = _MISSING):
        self._value: V | object = default
        self._updated = False

    def get(self) -> V:
        if self._value is _MISSING:
            raise EmptyChannelError("LastValue channel is empty")
        return self._value  # type: ignore

    def update(self, values: Sequence[V]) -> bool:
        if len(values) > 1:
            raise InvalidUpdateError(
                f"LastValue channel received {len(values)} updates, expected at most 1"
            )
        if len(values) == 1:
            self._value = values[0]
            self._updated = True
            return True
        return False

    def is_available(self) -> bool:
        return self._value is not _MISSING

    def checkpoint(self) -> Any:
        return None if self._value is _MISSING else self._value

    def from_checkpoint(self, data: Any) -> None:
        self._value = data if data is not None else _MISSING


class BinaryOperatorChannel(BaseChannel[V, V]):
    """Folds multiple updates via a binary operator (reducer).

    Supports concurrent writes — all values are folded through the operator.
    """

    def __init__(self, operator: Callable[[V, V], V], default: V | object = _MISSING):
        self._operator = operator
        self._value: V | object = default
        self._default = default

    def get(self) -> V:
        if self._value is _MISSING:
            raise EmptyChannelError("BinaryOperatorChannel is empty")
        return self._value  # type: ignore

    def update(self, values: Sequence[V]) -> bool:
        if not values:
            return False
        result = self._value
        for v in values:
            if result is _MISSING:
                result = v
            else:
                result = self._operator(result, v)  # type: ignore
        self._value = result
        return True

    def is_available(self) -> bool:
        return self._value is not _MISSING

    def checkpoint(self) -> Any:
        return None if self._value is _MISSING else self._value

    def from_checkpoint(self, data: Any) -> None:
        self._value = data if data is not None else self._default


class TopicChannel(BaseChannel[list[V], V]):
    """PubSub channel — accumulates values into a list.

    Multiple writers can publish; all values are collected.
    Can optionally clear between steps (ephemeral mode).
    """

    def __init__(self, *, accumulate: bool = True):
        self._values: list[V] = []
        self._accumulate = accumulate

    def get(self) -> list[V]:
        return list(self._values)

    def update(self, values: Sequence[V]) -> bool:
        if not values:
            return False
        self._values.extend(values)
        return True

    def is_available(self) -> bool:
        return True  # Always available (empty list is valid)

    def reset(self) -> None:
        if not self._accumulate:
            self._values.clear()

    def checkpoint(self) -> Any:
        return list(self._values)

    def from_checkpoint(self, data: Any) -> None:
        self._values = list(data) if data else []


class EphemeralChannel(BaseChannel[V, V]):
    """Stores one value for a single step, then clears.

    Used for trigger channels (START, edge triggers).
    """

    def __init__(self) -> None:
        self._value: V | object = _MISSING

    def get(self) -> V:
        if self._value is _MISSING:
            raise EmptyChannelError("EphemeralChannel is empty")
        return self._value  # type: ignore

    def update(self, values: Sequence[V]) -> bool:
        if not values:
            return False
        self._value = values[-1]  # Take last
        return True

    def is_available(self) -> bool:
        return self._value is not _MISSING

    def reset(self) -> None:
        self._value = _MISSING

    def checkpoint(self) -> Any:
        return None  # Never persisted

    def from_checkpoint(self, data: Any) -> None:
        self._value = _MISSING  # Always starts empty


class BarrierChannel(BaseChannel[bool, str]):
    """Waits for all named values before becoming available.

    Used for fan-in join edges.
    """

    def __init__(self, names: set[str]):
        self._expected = frozenset(names)
        self._received: set[str] = set()

    def get(self) -> bool:
        return self._received >= self._expected

    def update(self, values: Sequence[str]) -> bool:
        before = len(self._received)
        self._received.update(values)
        return len(self._received) > before

    def is_available(self) -> bool:
        return self._received >= self._expected

    def reset(self) -> None:
        self._received.clear()

    def checkpoint(self) -> Any:
        return list(self._received)

    def from_checkpoint(self, data: Any) -> None:
        self._received = set(data) if data else set()


@dataclass
class ChannelConfig:
    """Configuration for a state key's channel."""
    channel: BaseChannel
    key: str


class ChannelManager:
    """Manages channels for a graph's state.

    Provides centralized access to create, update, and checkpoint channels.
    """

    def __init__(self) -> None:
        self._channels: dict[str, BaseChannel] = {}

    def register(self, key: str, channel: BaseChannel) -> None:
        self._channels[key] = channel

    def get_channel(self, key: str) -> BaseChannel | None:
        return self._channels.get(key)

    def get_state(self) -> dict[str, Any]:
        """Read current state from all available channels."""
        state: dict[str, Any] = {}
        for key, channel in self._channels.items():
            if channel.is_available():
                state[key] = channel.get()
        return state

    def apply_writes(self, updates: dict[str, list[Any]]) -> set[str]:
        """Apply grouped writes to channels. Returns set of changed keys."""
        changed: set[str] = set()
        for key, values in updates.items():
            channel = self._channels.get(key)
            if channel is None:
                # No channel registered — treat as LastValue
                channel = LastValue()
                self._channels[key] = channel
            if channel.update(values):
                changed.add(key)
        return changed

    def reset_ephemeral(self) -> None:
        """Reset ephemeral channels between steps."""
        for channel in self._channels.values():
            channel.reset()

    def checkpoint(self) -> dict[str, Any]:
        """Serialize all channel state."""
        return {
            key: channel.checkpoint()
            for key, channel in self._channels.items()
        }

    def restore(self, data: dict[str, Any]) -> None:
        """Restore channel state from checkpoint."""
        for key, value in data.items():
            channel = self._channels.get(key)
            if channel:
                channel.from_checkpoint(value)

    @property
    def channels(self) -> dict[str, BaseChannel]:
        return dict(self._channels)
