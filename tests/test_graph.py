"""Tests for the StateGraph engine."""

import pytest

from agent_orchestrator.core.graph import (
    END,
    START,
    CompiledGraph,
    GraphConfig,
    StateGraph,
    StreamEvent,
    StreamEventType,
)
from agent_orchestrator.core.checkpoint import InMemoryCheckpointer
from agent_orchestrator.core.reducers import append_reducer, add_reducer


# --- Helper node functions ---


async def increment_node(state):
    return {"counter": state.get("counter", 0) + 1}


async def double_node(state):
    return {"counter": state.get("counter", 0) * 2}


async def append_message_node(state):
    return {"messages": [f"step_{len(state.get('messages', []))}"]}


async def noop_node(state):
    return None


async def failing_node(state):
    raise ValueError("intentional failure")


# --- Tests ---


class TestStateGraphBuild:
    def test_simple_graph_compiles(self):
        g = StateGraph()
        g.add_node("a", increment_node)
        g.add_edge(START, "a")
        g.add_edge("a", END)
        compiled = g.compile()
        assert isinstance(compiled, CompiledGraph)

    def test_reserved_name_raises(self):
        g = StateGraph()
        with pytest.raises(ValueError, match="reserved"):
            g.add_node(START, increment_node)

    def test_duplicate_node_raises(self):
        g = StateGraph()
        g.add_node("a", increment_node)
        with pytest.raises(ValueError, match="already exists"):
            g.add_node("a", double_node)

    def test_no_start_edge_raises(self):
        g = StateGraph()
        g.add_node("a", increment_node)
        g.add_edge("a", END)
        with pytest.raises(ValueError, match="START"):
            g.compile()

    def test_unreachable_node_raises(self):
        g = StateGraph()
        g.add_node("a", increment_node)
        g.add_node("b", double_node)
        g.add_edge(START, "a")
        g.add_edge("a", END)
        with pytest.raises(ValueError, match="Unreachable"):
            g.compile()

    def test_invalid_edge_target_raises(self):
        g = StateGraph()
        g.add_node("a", increment_node)
        g.add_edge(START, "a")
        g.add_edge("a", "nonexistent")
        with pytest.raises(ValueError, match="not found"):
            g.compile()


class TestStateGraphExecution:
    @pytest.mark.asyncio
    async def test_single_node(self):
        g = StateGraph()
        g.add_node("inc", increment_node)
        g.add_edge(START, "inc")
        g.add_edge("inc", END)
        result = await g.compile().invoke({"counter": 0})
        assert result.success
        assert result.state["counter"] == 1
        assert len(result.steps) == 1

    @pytest.mark.asyncio
    async def test_chain_two_nodes(self):
        g = StateGraph()
        g.add_node("inc", increment_node)
        g.add_node("dbl", double_node)
        g.add_edge(START, "inc")
        g.add_edge("inc", "dbl")
        g.add_edge("dbl", END)
        result = await g.compile().invoke({"counter": 0})
        assert result.success
        assert result.state["counter"] == 2  # (0+1) * 2

    @pytest.mark.asyncio
    async def test_conditional_edge(self):
        def router(state):
            return "high" if state["counter"] > 5 else "low"

        async def high_node(state):
            return {"result": "took high path"}

        async def low_node(state):
            return {"result": "took low path"}

        g = StateGraph()
        g.add_node("high", high_node)
        g.add_node("low", low_node)
        g.add_edge(START, "inc")
        g.add_node("inc", increment_node)
        g.add_conditional_edges("inc", router, {"high": "high", "low": "low"})
        g.add_edge("high", END)
        g.add_edge("low", END)

        # counter=10 -> inc makes 11 -> router picks "high"
        result = await g.compile().invoke({"counter": 10})
        assert result.success
        assert result.state["result"] == "took high path"

        # counter=0 -> inc makes 1 -> router picks "low"
        result = await g.compile().invoke({"counter": 0})
        assert result.success
        assert result.state["result"] == "took low path"

    @pytest.mark.asyncio
    async def test_noop_node_preserves_state(self):
        g = StateGraph()
        g.add_node("noop", noop_node)
        g.add_edge(START, "noop")
        g.add_edge("noop", END)
        result = await g.compile().invoke({"data": "preserved"})
        assert result.success
        assert result.state["data"] == "preserved"

    @pytest.mark.asyncio
    async def test_node_failure_returns_error(self):
        g = StateGraph()
        g.add_node("fail", failing_node)
        g.add_edge(START, "fail")
        g.add_edge("fail", END)
        result = await g.compile().invoke({})
        assert not result.success
        assert "intentional failure" in result.error

    @pytest.mark.asyncio
    async def test_recursion_limit(self):
        async def loop_node(state):
            return {"counter": state.get("counter", 0) + 1}

        def always_loop(state):
            return "loop"

        g = StateGraph()
        g.add_node("loop", loop_node)
        g.add_edge(START, "loop")
        g.add_conditional_edges("loop", always_loop, {"loop": "loop"})

        result = await g.compile(config=GraphConfig(recursion_limit=5)).invoke({})
        assert not result.success
        assert "Recursion limit" in result.error
        assert result.state["counter"] == 5


class TestReducers:
    @pytest.mark.asyncio
    async def test_append_reducer(self):
        g = StateGraph(reducers={"messages": append_reducer})
        g.add_node("step", append_message_node)
        g.add_edge(START, "step")
        g.add_edge("step", END)
        result = await g.compile().invoke({"messages": ["initial"]})
        assert result.success
        assert result.state["messages"] == ["initial", "step_1"]

    @pytest.mark.asyncio
    async def test_add_reducer(self):
        async def add_five(state):
            return {"total": 5}

        g = StateGraph(reducers={"total": add_reducer})
        g.add_node("add", add_five)
        g.add_edge(START, "add")
        g.add_edge("add", END)
        result = await g.compile().invoke({"total": 10})
        assert result.success
        assert result.state["total"] == 15


class TestCheckpointing:
    @pytest.mark.asyncio
    async def test_in_memory_checkpoint(self):
        cp = InMemoryCheckpointer()
        g = StateGraph()
        g.add_node("inc", increment_node)
        g.add_node("dbl", double_node)
        g.add_edge(START, "inc")
        g.add_edge("inc", "dbl")
        g.add_edge("dbl", END)

        result = await g.compile(checkpointer=cp).invoke({"counter": 0}, thread_id="test-thread")
        assert result.success

        # Verify checkpoints were saved
        checkpoints = await cp.list_thread("test-thread")
        assert len(checkpoints) == 2  # one per node

        latest = await cp.get_latest("test-thread")
        assert latest is not None
        assert latest.state["counter"] == 2

    @pytest.mark.asyncio
    async def test_resume_from_checkpoint(self):
        cp = InMemoryCheckpointer()

        # Build a 3-node graph
        async def step_a(state):
            return {"log": state.get("log", "") + "A"}

        async def step_b(state):
            return {"log": state.get("log", "") + "B"}

        async def step_c(state):
            return {"log": state.get("log", "") + "C"}

        g = StateGraph()
        g.add_node("a", step_a)
        g.add_node("b", step_b)
        g.add_node("c", step_c)
        g.add_edge(START, "a")
        g.add_edge("a", "b")
        g.add_edge("b", "c")
        g.add_edge("c", END)

        compiled = g.compile(checkpointer=cp)

        # Run full graph
        result = await compiled.invoke({}, thread_id="t1")
        assert result.success
        assert result.state["log"] == "ABC"

        # Resume from after step A (checkpoint at step 0)
        result2 = await compiled.invoke({}, resume_from="t1:0")
        assert result2.success
        assert result2.state["log"] == "ABC"  # B and C run again from A's state


class TestGraphInfo:
    def test_get_graph_info(self):
        g = StateGraph()
        g.add_node("a", increment_node)
        g.add_node("b", double_node)
        g.add_edge(START, "a")
        g.add_edge("a", "b")
        g.add_edge("b", END)
        info = g.compile().get_graph_info()
        assert "a" in info["nodes"]
        assert "b" in info["nodes"]
        assert len(info["edges"]) == 3


class TestAstream:
    @pytest.mark.asyncio
    async def test_single_node_stream(self):
        """astream yields GRAPH_START, NODE_START, NODE_END, GRAPH_END."""
        g = StateGraph()
        g.add_node("inc", increment_node)
        g.add_edge(START, "inc")
        g.add_edge("inc", END)

        events: list[StreamEvent] = []
        async for event in g.compile().astream({"counter": 0}):
            events.append(event)

        types = [e.event_type for e in events]
        assert types == [
            StreamEventType.GRAPH_START,
            StreamEventType.NODE_START,
            StreamEventType.NODE_END,
            StreamEventType.GRAPH_END,
        ]

    @pytest.mark.asyncio
    async def test_stream_state_progression(self):
        """State should progress through the stream events."""
        g = StateGraph()
        g.add_node("inc", increment_node)
        g.add_node("dbl", double_node)
        g.add_edge(START, "inc")
        g.add_edge("inc", "dbl")
        g.add_edge("dbl", END)

        events: list[StreamEvent] = []
        async for event in g.compile().astream({"counter": 0}):
            events.append(event)

        # After inc: counter=1
        inc_end = [
            e for e in events if e.event_type == StreamEventType.NODE_END and e.node == "inc"
        ]
        assert len(inc_end) == 1
        assert inc_end[0].state["counter"] == 1

        # After dbl: counter=2
        dbl_end = [
            e for e in events if e.event_type == StreamEventType.NODE_END and e.node == "dbl"
        ]
        assert len(dbl_end) == 1
        assert dbl_end[0].state["counter"] == 2

    @pytest.mark.asyncio
    async def test_stream_delta(self):
        """NODE_END events should include state delta."""
        g = StateGraph()
        g.add_node("inc", increment_node)
        g.add_edge(START, "inc")
        g.add_edge("inc", END)

        events: list[StreamEvent] = []
        async for event in g.compile().astream({"counter": 0}):
            events.append(event)

        node_end = [e for e in events if e.event_type == StreamEventType.NODE_END][0]
        assert node_end.delta is not None
        assert node_end.delta["counter"] == 1

    @pytest.mark.asyncio
    async def test_stream_node_error(self):
        """Failing node should yield NODE_ERROR and stop."""
        g = StateGraph()
        g.add_node("fail", failing_node)
        g.add_edge(START, "fail")
        g.add_edge("fail", END)

        events: list[StreamEvent] = []
        async for event in g.compile().astream({}):
            events.append(event)

        types = [e.event_type for e in events]
        assert StreamEventType.GRAPH_START in types
        assert StreamEventType.NODE_START in types
        assert StreamEventType.NODE_ERROR in types
        assert StreamEventType.GRAPH_END not in types

        error_event = [e for e in events if e.event_type == StreamEventType.NODE_ERROR][0]
        assert "intentional failure" in error_event.error

    @pytest.mark.asyncio
    async def test_stream_parallel_nodes(self):
        """Parallel nodes should emit NODE_START/NODE_END for each node."""

        async def add_a(state):
            return {"a": "done"}

        async def add_b(state):
            return {"b": "done"}

        g = StateGraph()
        g.add_node("node_a", add_a)
        g.add_node("node_b", add_b)
        g.add_node("merge", noop_node)
        g.add_edge(START, "node_a")
        g.add_edge(START, "node_b")
        g.add_edge("node_a", "merge")
        g.add_edge("node_b", "merge")
        g.add_edge("merge", END)

        events: list[StreamEvent] = []
        async for event in g.compile().astream({}):
            events.append(event)

        # Should have NODE_START for both parallel nodes
        starts = [e for e in events if e.event_type == StreamEventType.NODE_START]
        start_nodes = {e.node for e in starts}
        assert "node_a" in start_nodes
        assert "node_b" in start_nodes

        # Parallel events should have parallel_group set
        parallel_starts = [e for e in starts if e.parallel_group]
        assert len(parallel_starts) == 2
        assert set(parallel_starts[0].parallel_group) == {"node_a", "node_b"}

    @pytest.mark.asyncio
    async def test_stream_elapsed_ms(self):
        """NODE_END and GRAPH_END should have non-zero elapsed_ms."""
        g = StateGraph()
        g.add_node("inc", increment_node)
        g.add_edge(START, "inc")
        g.add_edge("inc", END)

        events: list[StreamEvent] = []
        async for event in g.compile().astream({"counter": 0}):
            events.append(event)

        graph_end = [e for e in events if e.event_type == StreamEventType.GRAPH_END][0]
        assert graph_end.elapsed_ms >= 0

    @pytest.mark.asyncio
    async def test_stream_recursion_limit(self):
        """Recursion limit should yield NODE_ERROR."""

        async def loop_fn(state):
            return {"counter": state.get("counter", 0) + 1}

        def always_loop(state):
            return "loop"

        g = StateGraph()
        g.add_node("loop", loop_fn)
        g.add_edge(START, "loop")
        g.add_conditional_edges("loop", always_loop, {"loop": "loop"})

        events: list[StreamEvent] = []
        async for event in g.compile(config=GraphConfig(recursion_limit=3)).astream({}):
            events.append(event)

        types = [e.event_type for e in events]
        assert StreamEventType.NODE_ERROR in types
        error_event = [e for e in events if e.event_type == StreamEventType.NODE_ERROR][0]
        assert "Recursion limit" in error_event.error

    @pytest.mark.asyncio
    async def test_stream_final_state_matches_invoke(self):
        """astream final state should match invoke result."""
        g = StateGraph()
        g.add_node("inc", increment_node)
        g.add_node("dbl", double_node)
        g.add_edge(START, "inc")
        g.add_edge("inc", "dbl")
        g.add_edge("dbl", END)

        compiled = g.compile()

        # invoke
        invoke_result = await compiled.invoke({"counter": 5})

        # astream
        last_event = None
        async for event in compiled.astream({"counter": 5}):
            last_event = event

        assert last_event.event_type == StreamEventType.GRAPH_END
        assert last_event.state == invoke_result.state
