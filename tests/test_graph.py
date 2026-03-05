"""Tests for the StateGraph engine."""

import pytest

from agent_orchestrator.core.graph import (
    END,
    START,
    CompiledGraph,
    GraphConfig,
    StateGraph,
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
