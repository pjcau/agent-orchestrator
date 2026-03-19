"""Loop detection middleware for agent tool calls.

Detects when an agent is stuck in a loop by tracking repeated tool calls
within a sliding window. Emits warnings and raises errors to break cycles.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import OrderedDict, deque
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class LoopStatus(str, Enum):
    """Result of a loop detection check."""

    OK = "ok"
    WARNING = "warning"
    HARD_STOP = "hard_stop"


class LoopDetectedError(Exception):
    """Raised when a hard-stop loop is detected (same tool call repeated too many times)."""

    def __init__(self, tool_name: str, count: int, session_id: str) -> None:
        self.tool_name = tool_name
        self.count = count
        self.session_id = session_id
        super().__init__(f"Loop detected: {tool_name} called {count} times in session {session_id}")


def _hash_tool_call(tool_name: str, params: dict[str, Any]) -> str:
    """Hash a tool call (name + sorted params) using MD5 for speed.

    Parameters are sorted recursively so that different key orderings
    produce the same hash.
    """
    payload = json.dumps({"tool": tool_name, "params": params}, sort_keys=True)
    return hashlib.md5(payload.encode()).hexdigest()


class LoopDetector:
    """Per-session sliding window loop detector with LRU eviction.

    Args:
        warn_threshold: Number of identical calls in the window before a WARNING.
        stop_threshold: Number of identical calls in the window before a HARD_STOP.
        window_size: Size of the sliding window (recent tool call hashes).
        max_sessions: Maximum number of sessions tracked (LRU eviction).
    """

    def __init__(
        self,
        warn_threshold: int = 3,
        stop_threshold: int = 5,
        window_size: int = 20,
        max_sessions: int = 500,
    ) -> None:
        if warn_threshold < 1:
            raise ValueError("warn_threshold must be >= 1")
        if stop_threshold <= warn_threshold:
            raise ValueError("stop_threshold must be > warn_threshold")
        if window_size < stop_threshold:
            raise ValueError("window_size must be >= stop_threshold")

        self.warn_threshold = warn_threshold
        self.stop_threshold = stop_threshold
        self.window_size = window_size
        self.max_sessions = max_sessions

        # OrderedDict for LRU eviction: session_id -> deque of hashes
        self._sessions: OrderedDict[str, deque[str]] = OrderedDict()

    def check(self, session_id: str, tool_name: str, params: dict[str, Any]) -> LoopStatus:
        """Check a tool call for loop patterns.

        Returns LoopStatus.OK, WARNING, or HARD_STOP.
        Raises LoopDetectedError on HARD_STOP.
        """
        call_hash = _hash_tool_call(tool_name, params)

        # Get or create session window (move to end for LRU)
        if session_id in self._sessions:
            self._sessions.move_to_end(session_id)
        else:
            # Evict oldest if at capacity
            if len(self._sessions) >= self.max_sessions:
                self._sessions.popitem(last=False)
            self._sessions[session_id] = deque(maxlen=self.window_size)

        window = self._sessions[session_id]
        window.append(call_hash)

        # Count occurrences of this hash in the window
        count = sum(1 for h in window if h == call_hash)

        if count >= self.stop_threshold:
            logger.error(
                "Loop hard stop: %s called %d times in session %s",
                tool_name,
                count,
                session_id,
            )
            return LoopStatus.HARD_STOP

        if count >= self.warn_threshold:
            logger.warning(
                "Loop warning: %s called %d times in session %s",
                tool_name,
                count,
                session_id,
            )
            return LoopStatus.WARNING

        return LoopStatus.OK

    def reset(self, session_id: str) -> None:
        """Clear tracking for a session."""
        self._sessions.pop(session_id, None)

    @property
    def active_sessions(self) -> int:
        """Number of sessions currently being tracked."""
        return len(self._sessions)
