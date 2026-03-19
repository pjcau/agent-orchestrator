"""Memory upload filtering — sanitize session-scoped file paths before persistence.

Prevents ephemeral session artifacts (job files, temp files, uploads) from
polluting long-term conversation memory and cross-thread stores.

Usage:
    mf = MemoryFilter()
    clean = mf.filter_message("See jobs/job_abc123/output.txt for results")
    # clean == "See [session-file] for results"

    if mf.should_persist("jobs/job_abc123/output.txt"):
        # False — message contains ONLY session-file references
        pass
"""

from __future__ import annotations

import re
from typing import Any


# Default patterns matching session-scoped file paths that should not persist.
SESSION_FILE_PATTERNS = [
    r"jobs/job_[a-f0-9\-]+/[^\s]*",
    r"/tmp/[a-f0-9\-]+[^\s]*",
    r"uploads/[a-f0-9\-]+/[^\s]*",
    r"/workspace/[a-f0-9\-]+/[^\s]*",
]

PLACEHOLDER = "[session-file]"


class MemoryFilter:
    """Filter session-scoped file paths from messages before persistence.

    Args:
        patterns: Regex patterns matching session-scoped paths.
                  Defaults to SESSION_FILE_PATTERNS.
    """

    def __init__(self, patterns: list[str] | None = None) -> None:
        raw = patterns if patterns is not None else SESSION_FILE_PATTERNS
        self._patterns = [re.compile(p) for p in raw]

    def filter_message(self, content: str) -> str:
        """Replace session-scoped file paths with [session-file] placeholder."""
        result = content
        for pat in self._patterns:
            result = pat.sub(PLACEHOLDER, result)
        return result

    def should_persist(self, content: str) -> bool:
        """Check if a message should be persisted.

        Returns False if the message contains ONLY session-file references
        (after filtering, nothing meaningful remains). Returns True otherwise.
        """
        filtered = self.filter_message(content)
        # Strip placeholders and whitespace — if nothing is left, skip persistence
        stripped = filtered.replace(PLACEHOLDER, "").strip()
        return len(stripped) > 0

    def filter_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter a list of message dicts for persistent memory storage.

        Each message dict must have a "content" key. Messages that contain
        only session-file references are dropped entirely. Remaining messages
        have their content filtered.
        """
        result: list[dict[str, Any]] = []
        for msg in messages:
            content = msg.get("content", "")
            if not self.should_persist(content):
                continue
            filtered_msg = dict(msg)
            filtered_msg["content"] = self.filter_message(content)
            result.append(filtered_msg)
        return result
