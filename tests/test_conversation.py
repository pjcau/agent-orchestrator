"""Tests for ConversationManager — thread-based message memory.

Covers:
- Core ConversationManager (thread memory, persistence, fork, clear)
- Agent integration (multi-turn agent execution with conversation history)
- Graph integration (multi-turn graph execution with conversation history)
"""

import pytest

from agent_orchestrator.core.conversation import (
    ConversationManager,
    ConversationMessage,
)
from agent_orchestrator.core.checkpoint import InMemoryCheckpointer, SQLiteCheckpointer
from agent_orchestrator.core.agent import Agent, AgentConfig, Task, TaskStatus
from agent_orchestrator.core.provider import (
    Completion,
    Message,
    ModelCapabilities,
    Provider,
    Role,
    Usage,
)
from agent_orchestrator.core.skill import SkillRegistry


# --- Mock graph functions ---


async def echo_graph_func(messages: list[dict]) -> str:
    """Echo the last user message back."""
    last_user = [m for m in messages if m["role"] == "user"][-1]
    return f"echo: {last_user['content']}"


async def context_aware_graph_func(messages: list[dict]) -> str:
    """Return a response that references the full conversation history."""
    user_msgs = [m["content"] for m in messages if m["role"] == "user"]
    return f"I see {len(user_msgs)} requests: {', '.join(user_msgs)}"


async def failing_graph_func(messages: list[dict]) -> str:
    """Simulate a graph failure."""
    raise RuntimeError("LLM provider unavailable")


async def counter_graph_func(messages: list[dict]) -> str:
    """Count total messages (all roles) to verify accumulation."""
    return f"total_messages={len(messages)}"


# --- Tests ---


class TestConversationMessage:
    def test_to_dict_and_back(self):
        msg = ConversationMessage(
            role="user",
            content="hello",
            timestamp=1234.0,
            metadata={"source": "test"},
        )
        d = msg.to_dict()
        assert d["role"] == "user"
        assert d["content"] == "hello"
        assert d["timestamp"] == 1234.0
        assert d["metadata"] == {"source": "test"}

        restored = ConversationMessage.from_dict(d)
        assert restored.role == msg.role
        assert restored.content == msg.content
        assert restored.timestamp == msg.timestamp
        assert restored.metadata == msg.metadata

    def test_from_dict_defaults(self):
        msg = ConversationMessage.from_dict({"role": "assistant", "content": "hi"})
        assert msg.timestamp == 0.0
        assert msg.metadata == {}


class TestConversationManagerBasic:
    @pytest.mark.asyncio
    async def test_single_turn(self):
        mgr = ConversationManager()
        result = await mgr.send("t1", "hello", echo_graph_func)

        assert result.success
        assert result.thread_id == "t1"
        assert result.response == "echo: hello"
        assert result.turn_count == 1
        assert len(result.messages) == 2  # user + assistant

    @pytest.mark.asyncio
    async def test_multi_turn_memory(self):
        """Core test: verify that previous messages are visible to the graph."""
        mgr = ConversationManager()

        # Turn 1
        r1 = await mgr.send("t1", "build an API", context_aware_graph_func)
        assert r1.success
        assert r1.turn_count == 1
        assert "1 requests: build an API" in r1.response

        # Turn 2 — graph should see both requests
        r2 = await mgr.send("t1", "add auth", context_aware_graph_func)
        assert r2.success
        assert r2.turn_count == 2
        assert "2 requests: build an API, add auth" in r2.response

        # Turn 3 — graph should see all three
        r3 = await mgr.send("t1", "add tests", context_aware_graph_func)
        assert r3.success
        assert r3.turn_count == 3
        assert "3 requests" in r3.response
        assert len(r3.messages) == 6  # 3 user + 3 assistant

    @pytest.mark.asyncio
    async def test_separate_threads_isolated(self):
        """Messages in one thread must not leak into another."""
        mgr = ConversationManager()

        await mgr.send("thread-a", "question A", echo_graph_func)
        await mgr.send("thread-b", "question B", echo_graph_func)

        history_a = await mgr.get_history("thread-a")
        history_b = await mgr.get_history("thread-b")

        assert len(history_a) == 2
        assert len(history_b) == 2
        assert history_a[0].content == "question A"
        assert history_b[0].content == "question B"

    @pytest.mark.asyncio
    async def test_message_accumulation_count(self):
        """Verify that messages accumulate correctly (user + assistant pairs)."""
        mgr = ConversationManager()

        r1 = await mgr.send("t1", "msg1", counter_graph_func)
        # Graph sees: [user:msg1] = 1 message
        assert r1.response == "total_messages=1"

        r2 = await mgr.send("t1", "msg2", counter_graph_func)
        # Graph sees: [user:msg1, assistant:..., user:msg2] = 3 messages
        assert r2.response == "total_messages=3"

        r3 = await mgr.send("t1", "msg3", counter_graph_func)
        # Graph sees: [user:msg1, assistant:..., user:msg2, assistant:..., user:msg3] = 5
        assert r3.response == "total_messages=5"


class TestConversationManagerMaxHistory:
    @pytest.mark.asyncio
    async def test_max_history_trims_oldest(self):
        """When max_history is set, oldest messages are dropped."""
        mgr = ConversationManager(max_history=4)

        await mgr.send("t1", "msg1", echo_graph_func)  # 2 msgs (user+assistant)
        await mgr.send("t1", "msg2", echo_graph_func)  # 4 msgs
        await mgr.send("t1", "msg3", echo_graph_func)  # would be 5, trimmed to 4+1

        # After trim: last 4 messages before graph call + the new assistant response
        # The graph receives 4 messages (trimmed), then assistant is appended = 5 stored
        # But the trim happens before the graph call, so:
        # messages at trim time: [user:msg1, asst, user:msg2, asst, user:msg3] = 5
        # trimmed to 4: [asst, user:msg2, asst, user:msg3]
        # after graph: [asst, user:msg2, asst, user:msg3, asst:echo] = 5
        history = await mgr.get_history("t1")
        assert len(history) == 5  # 4 kept + 1 new assistant
        # First user message should have been dropped
        user_msgs = [m.content for m in history if m.role == "user"]
        assert "msg1" not in user_msgs
        assert "msg2" in user_msgs
        assert "msg3" in user_msgs


class TestConversationManagerErrorHandling:
    @pytest.mark.asyncio
    async def test_graph_failure_preserves_user_message(self):
        """If the graph fails, the user message is still saved."""
        mgr = ConversationManager()

        result = await mgr.send("t1", "hello", failing_graph_func)
        assert not result.success
        assert "LLM provider unavailable" in result.error
        assert result.turn_count == 1

        # User message should be preserved
        history = await mgr.get_history("t1")
        assert len(history) == 1  # user msg saved, no assistant msg
        assert history[0].role == "user"
        assert history[0].content == "hello"

    @pytest.mark.asyncio
    async def test_recovery_after_failure(self):
        """After a failure, sending again should include the failed turn's user msg."""
        mgr = ConversationManager()

        # First call fails
        await mgr.send("t1", "try this", failing_graph_func)

        # Second call succeeds — should see the previous user message
        r2 = await mgr.send("t1", "try again", context_aware_graph_func)
        assert r2.success
        assert "2 requests: try this, try again" in r2.response


class TestConversationManagerClearAndFork:
    @pytest.mark.asyncio
    async def test_clear_thread(self):
        mgr = ConversationManager()
        await mgr.send("t1", "hello", echo_graph_func)
        await mgr.clear_thread("t1")

        history = await mgr.get_history("t1")
        assert len(history) == 0

    @pytest.mark.asyncio
    async def test_clear_then_new_conversation(self):
        """After clearing, a new send starts fresh."""
        mgr = ConversationManager()
        await mgr.send("t1", "old message", echo_graph_func)
        await mgr.clear_thread("t1")

        result = await mgr.send("t1", "fresh start", context_aware_graph_func)
        assert result.success
        assert "1 requests: fresh start" in result.response

    @pytest.mark.asyncio
    async def test_fork_thread(self):
        """Fork creates an independent copy of the conversation."""
        mgr = ConversationManager()
        await mgr.send("original", "base question", echo_graph_func)

        forked_id = await mgr.fork_thread("original", "fork-1")
        assert forked_id == "fork-1"

        # Fork should have same history
        fork_history = await mgr.get_history("fork-1")
        orig_history = await mgr.get_history("original")
        assert len(fork_history) == len(orig_history)
        assert fork_history[0].content == orig_history[0].content

        # Sending to fork should not affect original
        await mgr.send("fork-1", "diverge", echo_graph_func)
        fork_history = await mgr.get_history("fork-1")
        orig_history = await mgr.get_history("original")
        assert len(fork_history) == 4  # original 2 + user + assistant
        assert len(orig_history) == 2  # unchanged

    @pytest.mark.asyncio
    async def test_fork_auto_id(self):
        mgr = ConversationManager()
        await mgr.send("src", "hello", echo_graph_func)
        forked_id = await mgr.fork_thread("src")
        assert forked_id  # auto-generated UUID
        assert forked_id != "src"

    @pytest.mark.asyncio
    async def test_list_threads(self):
        mgr = ConversationManager()
        await mgr.send("t1", "a", echo_graph_func)
        await mgr.send("t2", "b", echo_graph_func)
        threads = await mgr.list_threads()
        assert "t1" in threads
        assert "t2" in threads


class TestConversationManagerPersistence:
    @pytest.mark.asyncio
    async def test_checkpoint_persistence(self):
        """Verify that a new manager can load threads from a shared checkpointer."""
        cp = InMemoryCheckpointer()

        # Manager 1 creates a conversation
        mgr1 = ConversationManager(checkpointer=cp)
        await mgr1.send("t1", "hello", echo_graph_func)
        await mgr1.send("t1", "world", echo_graph_func)

        # Manager 2 loads from same checkpointer (simulates restart)
        mgr2 = ConversationManager(checkpointer=cp)
        history = await mgr2.get_history("t1")
        assert len(history) == 4  # 2 user + 2 assistant
        assert history[0].content == "hello"
        assert history[2].content == "world"

    @pytest.mark.asyncio
    async def test_checkpoint_resume_and_continue(self):
        """After loading from checkpoint, new messages append correctly."""
        cp = InMemoryCheckpointer()

        mgr1 = ConversationManager(checkpointer=cp)
        await mgr1.send("t1", "step 1", echo_graph_func)

        # New manager, same checkpointer
        mgr2 = ConversationManager(checkpointer=cp)
        result = await mgr2.send("t1", "step 2", context_aware_graph_func)
        assert result.success
        assert "2 requests: step 1, step 2" in result.response

    @pytest.mark.asyncio
    async def test_sqlite_checkpointer_persistence(self, tmp_path):
        """End-to-end test with SQLite checkpointer."""
        db_path = tmp_path / "test_conv.db"

        # Create and populate
        cp1 = SQLiteCheckpointer(db_path=db_path)
        mgr1 = ConversationManager(checkpointer=cp1)
        await mgr1.send("t1", "persistent msg", echo_graph_func)
        cp1.close()

        # Reload from disk
        cp2 = SQLiteCheckpointer(db_path=db_path)
        mgr2 = ConversationManager(checkpointer=cp2)
        history = await mgr2.get_history("t1")
        assert len(history) == 2
        assert history[0].content == "persistent msg"
        assert history[1].content == "echo: persistent msg"
        cp2.close()


class TestConversationManagerMetadata:
    @pytest.mark.asyncio
    async def test_metadata_attached_to_message(self):
        mgr = ConversationManager()
        result = await mgr.send("t1", "hello", echo_graph_func, metadata={"source": "dashboard"})
        assert result.success
        user_msg = result.messages[0]
        assert user_msg.metadata == {"source": "dashboard"}

    @pytest.mark.asyncio
    async def test_metadata_persists_across_turns(self):
        mgr = ConversationManager()
        await mgr.send("t1", "msg1", echo_graph_func, metadata={"turn": 1})
        await mgr.send("t1", "msg2", echo_graph_func, metadata={"turn": 2})

        history = await mgr.get_history("t1")
        user_msgs = [m for m in history if m.role == "user"]
        assert user_msgs[0].metadata == {"turn": 1}
        assert user_msgs[1].metadata == {"turn": 2}


# --- Mock provider for agent tests ---


class MockProvider(Provider):
    """A mock LLM provider that records messages it receives."""

    def __init__(self):
        self.call_log: list[list[Message]] = []
        self.response_text = "mock response"

    @property
    def model_id(self) -> str:
        return "mock-model"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(max_context=4096, max_output_tokens=1024, supports_tools=False)

    @property
    def input_cost_per_million(self) -> float:
        return 0.0

    @property
    def output_cost_per_million(self) -> float:
        return 0.0

    async def complete(self, messages, **kwargs) -> Completion:
        self.call_log.append(list(messages))
        return Completion(
            content=self.response_text,
            usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.0),
            tool_calls=[],
        )

    async def stream(self, messages, **kwargs):
        yield self.response_text


class TestAgentConversationMemory:
    """Test that Agent.execute() uses conversation_history for multi-turn context."""

    @pytest.mark.asyncio
    async def test_agent_without_history(self):
        """Without history, agent sees only the current task."""
        provider = MockProvider()
        agent = Agent(
            config=AgentConfig(name="test", role="helper", provider_key="mock"),
            provider=provider,
            skill_registry=SkillRegistry(),
        )

        result = await agent.execute(Task(description="do something"))
        assert result.status == TaskStatus.COMPLETED
        # Only one user message (the task)
        assert len(provider.call_log[0]) == 1
        assert provider.call_log[0][0].content == "do something"

    @pytest.mark.asyncio
    async def test_agent_with_history(self):
        """With conversation_history, agent sees previous exchanges + current task."""
        provider = MockProvider()
        agent = Agent(
            config=AgentConfig(name="test", role="helper", provider_key="mock"),
            provider=provider,
            skill_registry=SkillRegistry(),
        )

        history = [
            Message(role=Role.USER, content="build an API"),
            Message(role=Role.ASSISTANT, content="I built the API"),
        ]

        result = await agent.execute(
            Task(description="now add auth"),
            conversation_history=history,
        )
        assert result.status == TaskStatus.COMPLETED
        # Agent should see: history (2 msgs) + current task (1 msg) = 3 msgs
        messages_sent = provider.call_log[0]
        assert len(messages_sent) == 3
        assert messages_sent[0].content == "build an API"
        assert messages_sent[1].content == "I built the API"
        assert messages_sent[2].content == "now add auth"

    @pytest.mark.asyncio
    async def test_agent_with_history_and_context(self):
        """History + task context both appear in messages."""
        provider = MockProvider()
        agent = Agent(
            config=AgentConfig(name="test", role="helper", provider_key="mock"),
            provider=provider,
            skill_registry=SkillRegistry(),
        )

        history = [
            Message(role=Role.USER, content="previous question"),
            Message(role=Role.ASSISTANT, content="previous answer"),
        ]

        result = await agent.execute(
            Task(
                description="new question",
                context={"spec": "REST API with auth"},
            ),
            conversation_history=history,
        )
        assert result.status == TaskStatus.COMPLETED
        # history (2) + context (1) + task (1) = 4 messages
        messages_sent = provider.call_log[0]
        assert len(messages_sent) == 4
        assert messages_sent[0].content == "previous question"
        assert messages_sent[1].content == "previous answer"
        assert "Available context" in messages_sent[2].content
        assert messages_sent[3].content == "new question"

    @pytest.mark.asyncio
    async def test_agent_multi_turn_via_conversation_manager(self):
        """Full integration: ConversationManager feeds history to Agent across calls."""
        provider = MockProvider()
        mgr = ConversationManager()

        agent = Agent(
            config=AgentConfig(name="test", role="helper", provider_key="mock"),
            provider=provider,
            skill_registry=SkillRegistry(),
        )

        # Turn 1
        async def run_with_history_1(msgs):
            history = [
                Message(role=Role(m["role"]), content=m["content"])
                for m in msgs[:-1]  # all except the last (current request)
            ]
            result = await agent.execute(
                Task(description=msgs[-1]["content"]),
                conversation_history=history if history else None,
            )
            return result.output

        r1 = await mgr.send("t1", "build API", run_with_history_1)
        assert r1.success
        # First call: only 1 message (no history)
        assert len(provider.call_log[0]) == 1

        # Turn 2 — agent should see turn 1
        r2 = await mgr.send("t1", "add auth", run_with_history_1)
        assert r2.success
        # Second call: 2 history msgs + 1 current = 3
        assert len(provider.call_log[1]) == 3
        assert provider.call_log[1][0].content == "build API"
        assert provider.call_log[1][1].content == "mock response"
        assert provider.call_log[1][2].content == "add auth"


class TestGraphConversationMemory:
    """Test that graph execution uses conversation memory."""

    @pytest.mark.asyncio
    async def test_graph_multi_turn_state(self):
        """Verify ConversationManager stores graph outputs across turns."""
        mgr = ConversationManager()

        async def mock_graph_1(msgs):
            return "graph output 1"

        async def mock_graph_2(msgs):
            # Should see previous exchange
            user_msgs = [m["content"] for m in msgs if m["role"] == "user"]
            return f"saw {len(user_msgs)} requests"

        r1 = await mgr.send("graph-t1", "prompt 1", mock_graph_1)
        assert r1.success
        assert r1.response == "graph output 1"

        r2 = await mgr.send("graph-t1", "prompt 2", mock_graph_2)
        assert r2.success
        assert "2 requests" in r2.response

        # Verify full history
        history = await mgr.get_history("graph-t1")
        assert len(history) == 4  # 2 user + 2 assistant
        assert history[0].content == "prompt 1"
        assert history[1].content == "graph output 1"
        assert history[2].content == "prompt 2"
        assert "2 requests" in history[3].content


# ===== Session Restore Tests =====


class TestSessionRestore:
    """Test re-hydrating conversation from job records (session load from history)."""

    @pytest.mark.asyncio
    async def test_restore_from_records_creates_thread(self):
        """Loading a session should create a conversation thread with full history."""
        mgr = ConversationManager()

        # Simulate what the /restore endpoint does
        messages = [
            ConversationMessage(role="user", content="Build an API"),
            ConversationMessage(role="assistant", content="Here's your API..."),
            ConversationMessage(role="user", content="Add authentication"),
            ConversationMessage(role="assistant", content="Auth added..."),
        ]
        await mgr._save_thread("restored-1", messages)

        # Now a follow-up request should see the full history
        async def check_context(msgs):
            user_msgs = [m["content"] for m in msgs if m["role"] == "user"]
            return f"saw {len(user_msgs)} requests"

        result = await mgr.send("restored-1", "Add tests", check_context)
        assert result.success
        assert "3 requests" in result.response  # 2 restored + 1 new

    @pytest.mark.asyncio
    async def test_restore_with_empty_records(self):
        """Restoring with no records should create empty thread."""
        mgr = ConversationManager()
        await mgr._save_thread("empty-1", [])

        history = await mgr.get_history("empty-1")
        assert len(history) == 0

    @pytest.mark.asyncio
    async def test_restore_overwrites_stale_thread(self):
        """Restoring should replace any existing stale thread data."""
        mgr = ConversationManager()

        # Create initial thread
        await mgr.send("thread-x", "old message", echo_graph_func)
        old_history = await mgr.get_history("thread-x")
        assert len(old_history) == 2  # user + assistant

        # Now restore with different data (simulating session load)
        restored_messages = [
            ConversationMessage(role="user", content="restored msg 1"),
            ConversationMessage(role="assistant", content="restored resp 1"),
            ConversationMessage(role="user", content="restored msg 2"),
            ConversationMessage(role="assistant", content="restored resp 2"),
        ]
        await mgr._save_thread("thread-x", restored_messages)

        new_history = await mgr.get_history("thread-x")
        assert len(new_history) == 4
        assert new_history[0].content == "restored msg 1"

    @pytest.mark.asyncio
    async def test_restored_thread_survives_checkpointer_reload(self):
        """Restored thread should persist via checkpointer and survive cache clear."""
        cp = InMemoryCheckpointer()
        mgr = ConversationManager(checkpointer=cp)

        messages = [
            ConversationMessage(role="user", content="hello"),
            ConversationMessage(role="assistant", content="hi"),
        ]
        await mgr._save_thread("persist-1", messages)

        # Clear in-memory cache to simulate server restart
        mgr._threads.clear()

        # Should reload from checkpointer
        history = await mgr.get_history("persist-1")
        assert len(history) == 2
        assert history[0].content == "hello"
        assert history[1].content == "hi"

    @pytest.mark.asyncio
    async def test_sqlite_checkpointer_survives_manager_recreation(self):
        """With SQLiteCheckpointer, data persists even with new manager instance."""
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            cp1 = SQLiteCheckpointer(db_path)

            mgr1 = ConversationManager(checkpointer=cp1)
            messages = [
                ConversationMessage(role="user", content="question 1"),
                ConversationMessage(role="assistant", content="answer 1"),
            ]
            await mgr1._save_thread("durable-1", messages)

            # Create a completely new manager with same DB (simulates restart)
            cp2 = SQLiteCheckpointer(db_path)
            mgr2 = ConversationManager(checkpointer=cp2)

            history = await mgr2.get_history("durable-1")
            assert len(history) == 2
            assert history[0].content == "question 1"

            # Send follow-up — should see history
            async def check(msgs):
                user_msgs = [m["content"] for m in msgs if m["role"] == "user"]
                return f"total: {len(user_msgs)}"

            result = await mgr2.send("durable-1", "question 2", check)
            assert result.success
            assert "total: 2" in result.response
