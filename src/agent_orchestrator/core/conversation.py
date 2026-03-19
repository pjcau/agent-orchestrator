"""Conversation memory — thread-based message history for iterative interactions.

Enables multi-turn conversations where each invocation remembers previous
requests and responses. Uses checkpointing for persistence.

Usage:
    manager = ConversationManager(checkpointer=InMemoryCheckpointer())

    # First request
    result = await manager.send("thread-1", "Build me an API", my_graph_func)
    # result.messages has: [user: "Build me an API", assistant: "..."]

    # Second request — remembers full context
    result = await manager.send("thread-1", "Now add authentication", my_graph_func)
    # result.messages has all 4 messages (2 user + 2 assistant)
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from .checkpoint import Checkpoint, Checkpointer, InMemoryCheckpointer


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English."""
    return len(text) // 4


# ---------------------------------------------------------------------------
# Summarization configuration
# ---------------------------------------------------------------------------


class SummarizationTrigger(Enum):
    """Determines when context summarization fires."""

    TOKEN_COUNT = "token_count"
    MESSAGE_COUNT = "message_count"
    FRACTION = "fraction"


@dataclass
class SummarizationConfig:
    """Configuration for automatic context summarization.

    Args:
        trigger: Which metric triggers summarization.
        threshold: The threshold value for the trigger.
            - TOKEN_COUNT: total estimated tokens across all messages.
            - MESSAGE_COUNT: total number of messages in the thread.
            - FRACTION: fraction (0.0-1.0) of max_history that triggers
              summarization (requires max_history > 0 on the manager).
        retain_last: Number of most-recent messages to keep verbatim.
        summary_model: Optional model identifier (informational; the actual
            summarization is performed by the ``summarize_func`` passed to
            the manager).
        enabled: Set to False to disable summarization entirely.
    """

    trigger: SummarizationTrigger = SummarizationTrigger.MESSAGE_COUNT
    threshold: int | float = 20
    retain_last: int = 4
    summary_model: str | None = None
    enabled: bool = True


@dataclass
class ConversationMessage:
    """A single message in a conversation thread."""

    role: str  # "user", "assistant", "system"
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ConversationMessage:
        return cls(
            role=d["role"],
            content=d["content"],
            timestamp=d.get("timestamp", 0.0),
            metadata=d.get("metadata", {}),
        )


@dataclass
class ConversationResult:
    """Result of a conversation turn."""

    thread_id: str
    messages: list[ConversationMessage]
    response: str
    success: bool
    error: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    turn_count: int = 0


class ConversationManager:
    """Manages multi-turn conversations with thread-based memory.

    Each thread accumulates messages across invocations. The graph
    receives the full message history on each call, enabling iterative
    refinement of solutions.

    Args:
        checkpointer: Persistence backend. Defaults to InMemoryCheckpointer.
        max_history: Maximum number of messages to keep per thread.
                     0 means unlimited.
        summarization_config: Optional config for automatic context
            summarization when threads grow too large.
        summarize_func: Async callable that receives a list of message
            dicts and returns a summary string.  Required when
            ``summarization_config`` is provided and enabled.
    """

    def __init__(
        self,
        checkpointer: Checkpointer | None = None,
        max_history: int = 0,
        summarization_config: SummarizationConfig | None = None,
        summarize_func: Callable[[list[dict[str, Any]]], Awaitable[str]] | None = None,
    ):
        self._checkpointer = checkpointer or InMemoryCheckpointer()
        self._max_history = max_history
        self._threads: dict[str, list[ConversationMessage]] = {}
        self._summarization_config = summarization_config
        self._summarize_func = summarize_func
        # Track summarization statistics
        self.summarization_count: int = 0
        self.tokens_saved: int = 0

    async def send(
        self,
        thread_id: str,
        user_message: str,
        graph_func: Callable[[list[dict[str, Any]]], Awaitable[str]],
        metadata: dict[str, Any] | None = None,
    ) -> ConversationResult:
        """Send a message and get a response, with full conversation context.

        Args:
            thread_id: Conversation thread identifier.
            user_message: The user's new message.
            graph_func: Async function that takes a list of message dicts
                        (each with "role" and "content") and returns a
                        response string.
            metadata: Optional metadata attached to the user message.
        """
        messages = await self._load_thread(thread_id)

        user_msg = ConversationMessage(
            role="user",
            content=user_message,
            metadata=metadata or {},
        )
        messages.append(user_msg)

        # Trim history if limit is set
        if self._max_history > 0 and len(messages) > self._max_history:
            messages = messages[-self._max_history :]

        # Summarize older messages if trigger fires
        if self._should_summarize(messages):
            messages = await self.summarize_thread(messages)

        try:
            msg_dicts = [m.to_dict() for m in messages]
            response_text = await graph_func(msg_dicts)

            assistant_msg = ConversationMessage(
                role="assistant",
                content=response_text,
            )
            messages.append(assistant_msg)

            await self._save_thread(thread_id, messages)

            return ConversationResult(
                thread_id=thread_id,
                messages=list(messages),
                response=response_text,
                success=True,
                turn_count=len([m for m in messages if m.role == "user"]),
            )
        except Exception as e:
            # Save partial state (user message added) so thread is not corrupted
            await self._save_thread(thread_id, messages)
            return ConversationResult(
                thread_id=thread_id,
                messages=list(messages),
                response="",
                success=False,
                error=str(e),
                turn_count=len([m for m in messages if m.role == "user"]),
            )

    async def get_history(self, thread_id: str) -> list[ConversationMessage]:
        """Get the full message history for a thread."""
        return await self._load_thread(thread_id)

    async def clear_thread(self, thread_id: str) -> None:
        """Clear all messages for a thread."""
        self._threads.pop(thread_id, None)
        await self._checkpointer.save(
            Checkpoint(
                checkpoint_id=f"conv:{thread_id}:0",
                thread_id=f"conv:{thread_id}",
                state={"messages": []},
                next_nodes=[],
                step_index=0,
            )
        )

    async def list_threads(self) -> list[str]:
        """List all active thread IDs."""
        return list(self._threads.keys())

    async def fork_thread(
        self,
        source_thread_id: str,
        new_thread_id: str | None = None,
    ) -> str:
        """Create a new thread branching from an existing one.

        Copies the full history so you can explore a different direction
        without losing the original conversation.
        """
        new_id = new_thread_id or str(uuid.uuid4())
        source_messages = await self._load_thread(source_thread_id)
        forked = [
            ConversationMessage(
                role=m.role,
                content=m.content,
                timestamp=m.timestamp,
                metadata=dict(m.metadata),
            )
            for m in source_messages
        ]
        self._threads[new_id] = forked
        await self._save_thread(new_id, forked)
        return new_id

    def _should_summarize(self, messages: list[ConversationMessage]) -> bool:
        """Check whether the summarization trigger condition is met."""
        cfg = self._summarization_config
        if cfg is None or not cfg.enabled or self._summarize_func is None:
            return False
        if len(messages) <= cfg.retain_last:
            return False

        if cfg.trigger == SummarizationTrigger.MESSAGE_COUNT:
            return len(messages) >= int(cfg.threshold)
        elif cfg.trigger == SummarizationTrigger.TOKEN_COUNT:
            total = sum(estimate_tokens(m.content) for m in messages)
            return total >= int(cfg.threshold)
        elif cfg.trigger == SummarizationTrigger.FRACTION:
            if self._max_history <= 0:
                return False
            return len(messages) >= int(self._max_history * float(cfg.threshold))
        return False

    async def summarize_thread(
        self, messages: list[ConversationMessage]
    ) -> list[ConversationMessage]:
        """Summarize older messages and retain the most recent ones.

        1. Split messages into *old* (to summarize) and *recent* (to keep).
        2. Send the old messages to the summarize_func.
        3. Replace old messages with a single system summary message.
        4. Return [summary] + recent.
        """
        cfg = self._summarization_config
        if cfg is None or self._summarize_func is None:
            return messages

        retain = cfg.retain_last
        if len(messages) <= retain:
            return messages

        old_messages = messages[:-retain]
        recent_messages = messages[-retain:]

        # Estimate tokens before summarization
        tokens_before = sum(estimate_tokens(m.content) for m in old_messages)

        old_dicts = [m.to_dict() for m in old_messages]
        summary_text = await self._summarize_func(old_dicts)

        tokens_after = estimate_tokens(summary_text)
        saved = max(0, tokens_before - tokens_after)
        self.tokens_saved += saved
        self.summarization_count += 1

        summary_msg = ConversationMessage(
            role="system",
            content=summary_text,
            metadata={"summarized_messages": len(old_messages)},
        )

        return [summary_msg] + recent_messages

    async def _load_thread(self, thread_id: str) -> list[ConversationMessage]:
        """Load thread from in-memory cache or checkpoint."""
        if thread_id in self._threads:
            return list(self._threads[thread_id])

        checkpoint = await self._checkpointer.get_latest(f"conv:{thread_id}")
        if checkpoint:
            msg_dicts = checkpoint.state.get("messages", [])
            messages = [ConversationMessage.from_dict(d) for d in msg_dicts]
            self._threads[thread_id] = messages
            return list(messages)

        return []

    async def _save_thread(self, thread_id: str, messages: list[ConversationMessage]) -> None:
        """Persist thread to in-memory cache and checkpoint store."""
        self._threads[thread_id] = messages
        turn_count = len([m for m in messages if m.role == "user"])
        await self._checkpointer.save(
            Checkpoint(
                checkpoint_id=f"conv:{thread_id}:{turn_count}",
                thread_id=f"conv:{thread_id}",
                state={"messages": [m.to_dict() for m in messages]},
                next_nodes=[],
                step_index=turn_count,
            )
        )
