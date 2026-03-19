"""Dangling tool call detection and recovery.

When an agent execution is interrupted (crash, timeout, etc.), the message
history may contain AIMessage entries with ``tool_calls`` that have no
matching ToolMessage response.  Sending such a history back to the LLM
causes errors with most providers.

This module scans a message list and injects placeholder ToolMessage
responses for any dangling (unmatched) tool calls, allowing the
conversation to resume cleanly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .provider import Message, Role

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

#: Placeholder content injected for dangling tool calls.
PLACEHOLDER_CONTENT = "[Tool call interrupted — no result available]"


def recover_dangling_tool_calls(
    messages: list[Message],
    *,
    session_id: str | None = None,
) -> list[Message]:
    """Scan messages and inject placeholder responses for dangling tool calls.

    A *dangling* tool call is an assistant message with ``tool_calls`` where
    one or more of those calls has no subsequent ``ToolMessage`` with a
    matching ``tool_call_id``.

    Returns a **new** list with placeholders inserted immediately after their
    corresponding assistant message.  The input list is **not** mutated.

    Args:
        messages: Conversation history to scan.
        session_id: Optional session identifier for logging context.
    """
    # Collect all tool_call_ids that already have a response.
    responded_ids: set[str] = {
        msg.tool_call_id
        for msg in messages
        if msg.role == Role.TOOL and msg.tool_call_id is not None
    }

    # Build the new message list, inserting placeholders where needed.
    result: list[Message] = []
    recovered = False

    for msg in messages:
        result.append(msg)

        if msg.role != Role.ASSISTANT or not msg.tool_calls:
            continue

        for tool_call in msg.tool_calls:
            if tool_call.id not in responded_ids:
                logger.warning(
                    "Dangling tool call recovered: session=%s tool=%s call_id=%s",
                    session_id or "unknown",
                    tool_call.name,
                    tool_call.id,
                )
                result.append(
                    Message(
                        role=Role.TOOL,
                        content=PLACEHOLDER_CONTENT,
                        tool_call_id=tool_call.id,
                    )
                )
                recovered = True

    if recovered:
        logger.info(
            "Dangling tool call recovery complete: session=%s total_messages=%d -> %d",
            session_id or "unknown",
            len(messages),
            len(result),
        )

    return result
