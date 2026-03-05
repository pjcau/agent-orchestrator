"""Tests for parallel node execution."""

import asyncio
import time

import pytest

from agent_orchestrator.core.graph import END, START, GraphConfig, StateGraph
from agent_orchestrator.core.reducers import append_reducer, add_reducer


class TestParallelExecution:
    @pytest.mark.asyncio
    async def test_two_parallel_nodes(self):
        """Two nodes from START should run in parallel."""

        async def node_a(state):
            return {"results": ["a_done"]}

        async def node_b(state):
            return {"results": ["b_done"]}

        async def merge(state):
            return {"final": sorted(state.get("results", []))}

        g = StateGraph(reducers={"results": append_reducer})
        g.add_node("a", node_a)
        g.add_node("b", node_b)
        g.add_node("merge", merge)
        g.add_edge(START, "a")
        g.add_edge(START, "b")
        g.add_edge("a", "merge")
        g.add_edge("b", "merge")
        g.add_edge("merge", END)

        result = await g.compile().invoke({})
        assert result.success
        assert "a_done" in result.state["results"]
        assert "b_done" in result.state["results"]
        assert result.state["final"] == ["a_done", "b_done"]

    @pytest.mark.asyncio
    async def test_parallel_actually_concurrent(self):
        """Verify parallel nodes run concurrently (not sequentially)."""

        async def slow_a(state):
            await asyncio.sleep(0.1)
            return {"done_a": True}

        async def slow_b(state):
            await asyncio.sleep(0.1)
            return {"done_b": True}

        g = StateGraph()
        g.add_node("a", slow_a)
        g.add_node("b", slow_b)
        g.add_edge(START, "a")
        g.add_edge(START, "b")
        g.add_edge("a", END)
        g.add_edge("b", END)

        start = time.monotonic()
        result = await g.compile(config=GraphConfig(enable_parallel=True)).invoke({})
        elapsed = time.monotonic() - start

        assert result.success
        assert result.state["done_a"] is True
        assert result.state["done_b"] is True
        # If parallel: ~0.1s. If sequential: ~0.2s. Allow margin.
        assert elapsed < 0.18, f"Took {elapsed:.3f}s — not parallel!"

    @pytest.mark.asyncio
    async def test_parallel_disabled_runs_sequentially(self):
        """When enable_parallel=False, only first node runs per step."""

        async def node_a(state):
            return {"order": [1]}

        async def node_b(state):
            return {"order": [2]}

        g = StateGraph(reducers={"order": append_reducer})
        g.add_node("a", node_a)
        g.add_node("b", node_b)
        g.add_edge(START, "a")
        g.add_edge(START, "b")
        g.add_edge("a", END)
        g.add_edge("b", END)

        result = await g.compile(config=GraphConfig(enable_parallel=False)).invoke({})
        assert result.success

    @pytest.mark.asyncio
    async def test_parallel_with_reducers(self):
        """Parallel nodes use reducers correctly when merging updates."""

        async def add_ten(state):
            return {"total": 10}

        async def add_twenty(state):
            return {"total": 20}

        g = StateGraph(reducers={"total": add_reducer})
        g.add_node("ten", add_ten)
        g.add_node("twenty", add_twenty)
        g.add_edge(START, "ten")
        g.add_edge(START, "twenty")
        g.add_edge("ten", END)
        g.add_edge("twenty", END)

        result = await g.compile().invoke({"total": 0})
        assert result.success
        assert result.state["total"] == 30  # 0 + 10 + 20

    @pytest.mark.asyncio
    async def test_parallel_step_record(self):
        """Parallel execution records parallel_group in StepRecord."""

        async def node_a(state):
            return {"a": True}

        async def node_b(state):
            return {"b": True}

        g = StateGraph()
        g.add_node("a", node_a)
        g.add_node("b", node_b)
        g.add_edge(START, "a")
        g.add_edge(START, "b")
        g.add_edge("a", END)
        g.add_edge("b", END)

        result = await g.compile().invoke({})
        assert result.success
        assert len(result.steps) >= 1
        parallel_step = result.steps[0]
        assert parallel_step.parallel_group is not None
        assert "a" in parallel_step.parallel_group
        assert "b" in parallel_step.parallel_group

    @pytest.mark.asyncio
    async def test_parallel_one_fails(self):
        """If one parallel node fails, the whole step fails."""

        async def good_node(state):
            return {"good": True}

        async def bad_node(state):
            raise ValueError("boom")

        g = StateGraph()
        g.add_node("good", good_node)
        g.add_node("bad", bad_node)
        g.add_edge(START, "good")
        g.add_edge(START, "bad")
        g.add_edge("good", END)
        g.add_edge("bad", END)

        result = await g.compile().invoke({})
        assert not result.success
        assert "boom" in result.error
