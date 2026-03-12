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
from typing import Any, Awaitable, Callable

from .checkpoint import Checkpoint, Checkpointer, InMemoryCheckpointer


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
    """

    def __init__(
        self,
        checkpointer: Checkpointer | None = None,
        max_history: int = 0,
    ):
        self._checkpointer = checkpointer or InMemoryCheckpointer()
        self._max_history = max_history
        self._threads: dict[str, list[ConversationMessage]] = {}

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
