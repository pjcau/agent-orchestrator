"""Tests for dangling tool call detection and recovery.

Covers:
- No-op when all tool calls have responses
- Single dangling call gets placeholder
- Multiple dangling calls in sequence
- Mixed: some responded, some dangling
- Placeholder content and structure
- Original list is not mutated
- Integration with ConversationManager thread reload
"""

import pytest

from agent_orchestrator.core.provider import Message, Role, ToolCall
from agent_orchestrator.core.tool_recovery import (
    PLACEHOLDER_CONTENT,
    recover_dangling_tool_calls,
)
from agent_orchestrator.core.conversation import (
    ConversationManager,
    _recover_raw_messages,
)
from agent_orchestrator.core.checkpoint import Checkpoint, InMemoryCheckpointer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user(content: str) -> Message:
    return Message(role=Role.USER, content=content)


def _assistant(content: str, tool_calls: list[ToolCall] | None = None) -> Message:
    return Message(role=Role.ASSISTANT, content=content, tool_calls=tool_calls)


def _tool_response(tool_call_id: str, content: str = "ok") -> Message:
    return Message(role=Role.TOOL, content=content, tool_call_id=tool_call_id)


def _tc(call_id: str, name: str = "file_read") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments={})


# ---------------------------------------------------------------------------
# Core function tests
# ---------------------------------------------------------------------------


class TestRecoverDanglingToolCalls:
    """Tests for recover_dangling_tool_calls()."""

    def test_noop_no_tool_calls(self):
        """Messages without tool calls pass through unchanged."""
        msgs = [_user("hello"), _assistant("hi")]
        result = recover_dangling_tool_calls(msgs)
        assert len(result) == 2
        assert result[0].content == "hello"
        assert result[1].content == "hi"

    def test_noop_all_responded(self):
        """When every tool call has a response, no placeholders are added."""
        msgs = [
            _user("do stuff"),
            _assistant("calling tool", tool_calls=[_tc("tc-1")]),
            _tool_response("tc-1", "result"),
            _assistant("done"),
        ]
        result = recover_dangling_tool_calls(msgs)
        assert len(result) == len(msgs)

    def test_single_dangling_call(self):
        """A single dangling tool call gets a placeholder injected."""
        msgs = [
            _user("do stuff"),
            _assistant("calling tool", tool_calls=[_tc("tc-1", "shell_exec")]),
        ]
        result = recover_dangling_tool_calls(msgs)
        assert len(result) == 3
        placeholder = result[2]
        assert placeholder.role == Role.TOOL
        assert placeholder.tool_call_id == "tc-1"
        assert placeholder.content == PLACEHOLDER_CONTENT

    def test_multiple_dangling_calls(self):
        """Multiple dangling tool calls each get their own placeholder."""
        msgs = [
            _user("multi-tool"),
            _assistant(
                "calling two tools",
                tool_calls=[_tc("tc-1", "file_read"), _tc("tc-2", "shell_exec")],
            ),
        ]
        result = recover_dangling_tool_calls(msgs)
        assert len(result) == 4  # user + assistant + 2 placeholders
        assert result[2].tool_call_id == "tc-1"
        assert result[3].tool_call_id == "tc-2"

    def test_mixed_responded_and_dangling(self):
        """Only dangling calls get placeholders; responded calls are left alone."""
        msgs = [
            _user("task"),
            _assistant(
                "tools",
                tool_calls=[_tc("tc-1", "file_read"), _tc("tc-2", "shell_exec")],
            ),
            _tool_response("tc-1", "file contents"),
            # tc-2 has no response — dangling
        ]
        result = recover_dangling_tool_calls(msgs)
        assert len(result) == 4  # original 3 + 1 placeholder
        # The placeholder for tc-2 is inserted right after the assistant message
        # (before the tc-1 response in the result list)
        tc2_placeholders = [m for m in result if m.role == Role.TOOL and m.tool_call_id == "tc-2"]
        assert len(tc2_placeholders) == 1
        assert tc2_placeholders[0].content == PLACEHOLDER_CONTENT

    def test_placeholder_content_structure(self):
        """Verify the placeholder message has the correct structure."""
        msgs = [
            _assistant("call", tool_calls=[_tc("tc-99", "web_search")]),
        ]
        result = recover_dangling_tool_calls(msgs)
        placeholder = result[1]
        assert placeholder.role == Role.TOOL
        assert placeholder.content == PLACEHOLDER_CONTENT
        assert placeholder.tool_call_id == "tc-99"
        # tool_calls should be None on the placeholder
        assert placeholder.tool_calls is None

    def test_original_list_not_mutated(self):
        """The input list must not be modified."""
        msgs = [
            _user("task"),
            _assistant("call", tool_calls=[_tc("tc-1")]),
        ]
        original_len = len(msgs)
        original_ids = [id(m) for m in msgs]

        result = recover_dangling_tool_calls(msgs)

        # Original list unchanged
        assert len(msgs) == original_len
        assert [id(m) for m in msgs] == original_ids
        # Result is a different list
        assert result is not msgs
        assert len(result) == 3

    def test_multiple_assistant_messages_with_dangling(self):
        """Dangling calls across multiple assistant messages are all recovered."""
        msgs = [
            _user("step 1"),
            _assistant("first call", tool_calls=[_tc("tc-1")]),
            # tc-1 dangling
            _user("step 2"),
            _assistant("second call", tool_calls=[_tc("tc-2")]),
            _tool_response("tc-2", "ok"),
        ]
        result = recover_dangling_tool_calls(msgs)
        # Only tc-1 is dangling, tc-2 is responded
        dangling_placeholders = [
            m for m in result if m.role == Role.TOOL and m.content == PLACEHOLDER_CONTENT
        ]
        assert len(dangling_placeholders) == 1
        assert dangling_placeholders[0].tool_call_id == "tc-1"

    def test_empty_messages(self):
        """Empty input returns empty output."""
        result = recover_dangling_tool_calls([])
        assert result == []

    def test_assistant_without_tool_calls_none(self):
        """Assistant messages with tool_calls=None are skipped."""
        msgs = [_user("hi"), _assistant("hello", tool_calls=None)]
        result = recover_dangling_tool_calls(msgs)
        assert len(result) == 2

    def test_assistant_with_empty_tool_calls(self):
        """Assistant messages with tool_calls=[] are skipped."""
        msgs = [_user("hi"), _assistant("hello", tool_calls=[])]
        result = recover_dangling_tool_calls(msgs)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Raw message dict recovery (used by ConversationManager)
# ---------------------------------------------------------------------------


class TestRecoverRawMessages:
    """Tests for _recover_raw_messages() used in conversation thread loading."""

    def test_noop_no_tool_calls(self):
        dicts = [
            {"role": "user", "content": "hi", "timestamp": 1.0, "metadata": {}},
            {"role": "assistant", "content": "hello", "timestamp": 2.0, "metadata": {}},
        ]
        result = _recover_raw_messages(dicts, "thread-1")
        assert len(result) == 2

    def test_dangling_tool_call_in_metadata(self):
        dicts = [
            {
                "role": "assistant",
                "content": "calling",
                "timestamp": 1.0,
                "metadata": {"tool_calls": [{"id": "tc-1", "name": "shell_exec"}]},
            },
        ]
        result = _recover_raw_messages(dicts, "thread-1")
        assert len(result) == 2
        placeholder = result[1]
        assert placeholder["role"] == "tool"
        assert placeholder["content"] == PLACEHOLDER_CONTENT
        assert placeholder["metadata"]["tool_call_id"] == "tc-1"
        assert placeholder["metadata"]["recovered"] is True

    def test_responded_tool_call_not_duplicated(self):
        dicts = [
            {
                "role": "assistant",
                "content": "calling",
                "timestamp": 1.0,
                "metadata": {"tool_calls": [{"id": "tc-1", "name": "file_read"}]},
            },
            {
                "role": "tool",
                "content": "result",
                "timestamp": 2.0,
                "metadata": {"tool_call_id": "tc-1"},
            },
        ]
        result = _recover_raw_messages(dicts, "thread-1")
        assert len(result) == 2  # No placeholder added

    def test_input_not_mutated(self):
        dicts = [
            {
                "role": "assistant",
                "content": "calling",
                "timestamp": 1.0,
                "metadata": {"tool_calls": [{"id": "tc-1", "name": "shell"}]},
            },
        ]
        original_len = len(dicts)
        _recover_raw_messages(dicts, "thread-1")
        assert len(dicts) == original_len


# ---------------------------------------------------------------------------
# Integration: ConversationManager thread reload
# ---------------------------------------------------------------------------


class TestConversationManagerRecovery:
    """Integration test: dangling tool calls are recovered when loading a thread."""

    @pytest.mark.asyncio
    async def test_recovery_on_thread_load_from_checkpoint(self):
        """When a checkpoint contains dangling tool calls, loading the thread
        injects placeholders before returning ConversationMessage objects."""
        checkpointer = InMemoryCheckpointer()

        # Simulate a checkpoint with a dangling tool call in metadata
        await checkpointer.save(
            Checkpoint(
                checkpoint_id="conv:thread-broken:1",
                thread_id="conv:thread-broken",
                state={
                    "messages": [
                        {
                            "role": "user",
                            "content": "run tests",
                            "timestamp": 1.0,
                            "metadata": {},
                        },
                        {
                            "role": "assistant",
                            "content": "calling shell",
                            "timestamp": 2.0,
                            "metadata": {"tool_calls": [{"id": "tc-42", "name": "shell_exec"}]},
                        },
                        # No tool response — dangling
                    ]
                },
                next_nodes=[],
                step_index=1,
            )
        )

        manager = ConversationManager(checkpointer=checkpointer)
        messages = await manager._load_thread("thread-broken")

        # Should have 3 messages: user, assistant, recovered placeholder
        assert len(messages) == 3
        assert messages[2].role == "tool"
        assert messages[2].content == PLACEHOLDER_CONTENT

    @pytest.mark.asyncio
    async def test_no_recovery_needed_on_clean_thread(self):
        """Clean threads (no dangling calls) pass through without modification."""
        checkpointer = InMemoryCheckpointer()

        await checkpointer.save(
            Checkpoint(
                checkpoint_id="conv:thread-clean:1",
                thread_id="conv:thread-clean",
                state={
                    "messages": [
                        {
                            "role": "user",
                            "content": "hello",
                            "timestamp": 1.0,
                            "metadata": {},
                        },
                        {
                            "role": "assistant",
                            "content": "hi there",
                            "timestamp": 2.0,
                            "metadata": {},
                        },
                    ]
                },
                next_nodes=[],
                step_index=1,
            )
        )

        manager = ConversationManager(checkpointer=checkpointer)
        messages = await manager._load_thread("thread-clean")
        assert len(messages) == 2
