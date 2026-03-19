"""Tests for memory_filter — session-scoped file path sanitization."""

import pytest
import asyncio

from agent_orchestrator.core.memory_filter import (
    MemoryFilter,
    SESSION_FILE_PATTERNS,
    PLACEHOLDER,
)
from agent_orchestrator.core.conversation import (
    ConversationManager,
    ConversationMessage,
)
from agent_orchestrator.core.checkpoint import InMemoryCheckpointer
from agent_orchestrator.core.store import InMemoryStore


# ─── Unit Tests: MemoryFilter ─────────────────────────────────────────


class TestFilterMessage:
    """Test that session-scoped paths are replaced with [session-file]."""

    def test_job_path_replaced(self):
        mf = MemoryFilter()
        result = mf.filter_message("See jobs/job_abc123-def4/output.txt for results")
        assert result == f"See {PLACEHOLDER} for results"

    def test_tmp_path_replaced(self):
        mf = MemoryFilter()
        result = mf.filter_message("Written to /tmp/abc123-def4-5678")
        assert result == f"Written to {PLACEHOLDER}"

    def test_upload_path_replaced(self):
        mf = MemoryFilter()
        result = mf.filter_message("File at uploads/abc123-def4/image.png is ready")
        assert result == f"File at {PLACEHOLDER} is ready"

    def test_workspace_path_replaced(self):
        mf = MemoryFilter()
        result = mf.filter_message("Created /workspace/abcdef12/main.py")
        assert result == f"Created {PLACEHOLDER}"

    def test_non_session_paths_preserved(self):
        mf = MemoryFilter()
        msg = "Edit src/agent_orchestrator/core/graph.py line 42"
        assert mf.filter_message(msg) == msg

    def test_regular_text_preserved(self):
        mf = MemoryFilter()
        msg = "Please build me a REST API with authentication"
        assert mf.filter_message(msg) == msg

    def test_mixed_content_filtered(self):
        mf = MemoryFilter()
        msg = "I wrote the output to jobs/job_aabb-ccdd/result.json and the API is ready"
        result = mf.filter_message(msg)
        assert result == f"I wrote the output to {PLACEHOLDER} and the API is ready"

    def test_multiple_paths_replaced(self):
        mf = MemoryFilter()
        msg = "See jobs/job_aabb/a.txt and /tmp/ccdd1234 for details"
        result = mf.filter_message(msg)
        assert PLACEHOLDER in result
        assert "jobs/job_aabb" not in result
        assert "/tmp/ccdd1234" not in result


class TestShouldPersist:
    """Test should_persist logic."""

    def test_file_only_message_returns_false(self):
        mf = MemoryFilter()
        assert mf.should_persist("jobs/job_aabb-ccdd/output.txt") is False

    def test_file_only_with_whitespace_returns_false(self):
        mf = MemoryFilter()
        assert mf.should_persist("  jobs/job_aabb-ccdd/output.txt  ") is False

    def test_mixed_message_returns_true(self):
        mf = MemoryFilter()
        assert mf.should_persist("Here is jobs/job_aabb-ccdd/output.txt done") is True

    def test_no_paths_returns_true(self):
        mf = MemoryFilter()
        assert mf.should_persist("Build a REST API") is True

    def test_empty_string_returns_false(self):
        mf = MemoryFilter()
        assert mf.should_persist("") is False


class TestFilterMessages:
    """Test filtering a list of message dicts."""

    def test_filters_list_correctly(self):
        mf = MemoryFilter()
        messages = [
            {"role": "user", "content": "Build an API"},
            {"role": "assistant", "content": "jobs/job_aabb/result.json"},
            {"role": "user", "content": "Check jobs/job_aabb/log.txt and fix the bug"},
        ]
        result = mf.filter_messages(messages)
        # Second message is file-only, should be dropped
        assert len(result) == 2
        assert result[0]["content"] == "Build an API"
        assert PLACEHOLDER in result[1]["content"]
        assert "fix the bug" in result[1]["content"]

    def test_preserves_metadata(self):
        mf = MemoryFilter()
        messages = [
            {"role": "user", "content": "Hello", "timestamp": 123.0, "metadata": {"key": "val"}},
        ]
        result = mf.filter_messages(messages)
        assert len(result) == 1
        assert result[0]["timestamp"] == 123.0
        assert result[0]["metadata"] == {"key": "val"}

    def test_empty_list(self):
        mf = MemoryFilter()
        assert mf.filter_messages([]) == []


class TestCustomPatterns:
    """Test that custom patterns work."""

    def test_custom_pattern(self):
        mf = MemoryFilter(patterns=[r"artifacts/[a-z]+/[^\s]*"])
        msg = "Output in artifacts/build/report.html is done"
        result = mf.filter_message(msg)
        assert result == f"Output in {PLACEHOLDER} is done"

    def test_custom_pattern_does_not_match_defaults(self):
        mf = MemoryFilter(patterns=[r"custom/[a-z]+"])
        msg = "See jobs/job_aabb/output.txt"
        # Default patterns are NOT active when custom patterns are provided
        assert mf.filter_message(msg) == msg


# ─── Integration: ConversationManager ─────────────────────────────────


@pytest.mark.asyncio
async def test_conversation_persist_filters_paths():
    """ConversationManager._save_thread applies MemoryFilter to persisted state."""
    checkpointer = InMemoryCheckpointer()
    manager = ConversationManager(checkpointer=checkpointer)

    async def echo_graph(msgs):
        return "Done! See jobs/job_abcd-1234/result.json"

    result = await manager.send("t1", "Build an API", echo_graph)
    assert result.success

    # Load the persisted checkpoint and verify filtering was applied
    cp = await checkpointer.get_latest("conv:t1")
    assert cp is not None
    persisted_msgs = cp.state["messages"]

    # The assistant response should have the path filtered
    assistant_msgs = [m for m in persisted_msgs if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    assert PLACEHOLDER in assistant_msgs[0]["content"]
    assert "jobs/job_abcd-1234" not in assistant_msgs[0]["content"]


@pytest.mark.asyncio
async def test_conversation_persist_drops_file_only_messages():
    """Messages with only session-file references are dropped from persistence."""
    checkpointer = InMemoryCheckpointer()
    manager = ConversationManager(checkpointer=checkpointer)

    async def file_only_graph(msgs):
        return "jobs/job_abcd-1234/output.txt"

    result = await manager.send("t2", "Build an API", file_only_graph)
    assert result.success

    cp = await checkpointer.get_latest("conv:t2")
    persisted_msgs = cp.state["messages"]
    # The assistant response is file-only, should be dropped from persistence
    assistant_msgs = [m for m in persisted_msgs if m["role"] == "assistant"]
    assert len(assistant_msgs) == 0


@pytest.mark.asyncio
async def test_conversation_in_memory_unfiltered():
    """In-memory thread retains original unfiltered messages for current session."""
    checkpointer = InMemoryCheckpointer()
    manager = ConversationManager(checkpointer=checkpointer)

    async def echo_graph(msgs):
        return "See jobs/job_abcd-1234/result.json"

    await manager.send("t3", "Build an API", echo_graph)

    # In-memory history should have the original unfiltered content
    history = await manager.get_history("t3")
    assistant_msgs = [m for m in history if m.role == "assistant"]
    assert len(assistant_msgs) == 1
    assert "jobs/job_abcd-1234/result.json" in assistant_msgs[0].content


# ─── Integration: InMemoryStore ───────────────────────────────────────


@pytest.mark.asyncio
async def test_store_put_filters_string_values():
    """InMemoryStore.aput filters string values when memory_filter is set."""
    mf = MemoryFilter()
    store = InMemoryStore(memory_filter=mf)

    await store.aput(
        ("agents",),
        "result1",
        {"output": "See jobs/job_aabb/data.csv for results", "count": 42},
    )

    item = await store.aget(("agents",), "result1")
    assert item is not None
    assert PLACEHOLDER in item.value["output"]
    assert "jobs/job_aabb" not in item.value["output"]
    # Non-string values are preserved
    assert item.value["count"] == 42


@pytest.mark.asyncio
async def test_store_put_no_filter_by_default():
    """InMemoryStore without memory_filter does not alter values."""
    store = InMemoryStore()

    raw = "jobs/job_aabb/data.csv"
    await store.aput(("agents",), "result1", {"path": raw})

    item = await store.aget(("agents",), "result1")
    assert item is not None
    assert item.value["path"] == raw
