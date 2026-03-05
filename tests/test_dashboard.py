"""Tests for the dashboard event bus, snapshot, and instrumentation."""

import asyncio
from typing import AsyncIterator

import pytest
from agent_orchestrator.dashboard.events import Event, EventBus, EventType
from agent_orchestrator.dashboard.instrument import (
    _instrument_agent,
    _instrument_graph,
    _instrument_cooperation,
)
from agent_orchestrator.core.agent import Agent, AgentConfig, Task, TaskStatus
from agent_orchestrator.core.skill import SkillRegistry
from agent_orchestrator.core.cooperation import (
    CooperationProtocol,
    TaskAssignment,
    TaskReport,
)
from agent_orchestrator.core.graph import StateGraph, CompiledGraph, START, END
from agent_orchestrator.core.provider import (
    Completion,
    ModelCapabilities,
    Provider,
    StreamChunk,
    Usage,
)


# --- Fixtures ---


@pytest.fixture
def bus():
    return EventBus()


class MockProvider(Provider):
    """Provider that returns a single text completion (no tool calls)."""

    @property
    def model_id(self) -> str:
        return "mock-1"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            max_context=4096,
            supports_tools=True,
            supports_vision=False,
            supports_streaming=False,
        )

    @property
    def input_cost_per_million(self) -> float:
        return 0.0

    @property
    def output_cost_per_million(self) -> float:
        return 0.0

    async def complete(self, messages, tools=None, system=None, **kwargs):
        return Completion(
            content="done",
            tool_calls=[],
            usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.001),
        )

    async def stream(
        self, messages, tools=None, system=None, **kwargs
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content="done", is_final=True)


# ===== EventBus =====


class TestEvent:
    def test_to_dict(self):
        e = Event(
            event_type=EventType.AGENT_SPAWN,
            agent_name="backend",
            data={"provider": "openai"},
        )
        d = e.to_dict()
        assert d["event_type"] == "agent.spawn"
        assert d["agent_name"] == "backend"
        assert d["data"]["provider"] == "openai"
        assert isinstance(d["timestamp"], float)

    def test_to_dict_preserves_all_fields(self):
        e = Event(
            event_type=EventType.GRAPH_NODE_ENTER,
            node_name="analyze",
            data={"step_index": 3},
        )
        d = e.to_dict()
        assert d["node_name"] == "analyze"
        assert d["agent_name"] is None


class TestEventBus:
    @pytest.mark.asyncio
    async def test_emit_stores_history(self, bus):
        await bus.emit(Event(event_type=EventType.ORCHESTRATOR_START))
        assert len(bus.get_history()) == 1

    @pytest.mark.asyncio
    async def test_history_cap(self, bus):
        bus._max_history = 5
        for i in range(10):
            await bus.emit(Event(event_type=EventType.AGENT_STEP, data={"i": i}))
        assert len(bus.get_history()) == 5
        assert bus.get_history()[0].data["i"] == 5

    @pytest.mark.asyncio
    async def test_subscribe_receives_events(self, bus):
        queue = bus.subscribe()
        await bus.emit(Event(event_type=EventType.AGENT_SPAWN, agent_name="test"))
        event = queue.get_nowait()
        assert event.agent_name == "test"

    @pytest.mark.asyncio
    async def test_unsubscribe(self, bus):
        queue = bus.subscribe()
        bus.unsubscribe(queue)
        await bus.emit(Event(event_type=EventType.AGENT_SPAWN))
        assert queue.empty()

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self, bus):
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        await bus.emit(Event(event_type=EventType.ORCHESTRATOR_START))
        assert not q1.empty()
        assert not q2.empty()

    @pytest.mark.asyncio
    async def test_full_queue_drops_events(self, bus):
        queue = bus.subscribe()
        for _ in range(250):
            await bus.emit(Event(event_type=EventType.AGENT_STEP))
        assert queue.qsize() == 200

    def test_singleton(self):
        EventBus.reset()
        a = EventBus.get()
        b = EventBus.get()
        assert a is b
        EventBus.reset()


# ===== Snapshot =====


class TestSnapshot:
    @pytest.mark.asyncio
    async def test_empty_snapshot(self, bus):
        snap = bus.get_snapshot()
        assert snap["orchestrator_status"] == "idle"
        assert snap["agents"] == {}
        assert snap["tasks"] == []
        assert snap["total_cost_usd"] == 0.0
        assert snap["event_count"] == 0

    @pytest.mark.asyncio
    async def test_orchestrator_lifecycle(self, bus):
        await bus.emit(Event(event_type=EventType.ORCHESTRATOR_START))
        assert bus.get_snapshot()["orchestrator_status"] == "running"

        await bus.emit(Event(event_type=EventType.ORCHESTRATOR_END, data={"success": True}))
        assert bus.get_snapshot()["orchestrator_status"] == "completed"

    @pytest.mark.asyncio
    async def test_orchestrator_failure(self, bus):
        await bus.emit(Event(event_type=EventType.ORCHESTRATOR_START))
        await bus.emit(Event(event_type=EventType.ORCHESTRATOR_END, data={"success": False}))
        assert bus.get_snapshot()["orchestrator_status"] == "failed"

    @pytest.mark.asyncio
    async def test_agent_lifecycle(self, bus):
        await bus.emit(
            Event(
                event_type=EventType.AGENT_SPAWN,
                agent_name="backend",
                data={"provider": "openai", "role": "API dev", "tools": ["shell"]},
            )
        )
        snap = bus.get_snapshot()
        assert "backend" in snap["agents"]
        assert snap["agents"]["backend"]["status"] == "running"
        assert snap["agents"]["backend"]["provider"] == "openai"
        assert snap["agents"]["backend"]["tools"] == ["shell"]

        await bus.emit(Event(event_type=EventType.AGENT_STEP, agent_name="backend"))
        await bus.emit(Event(event_type=EventType.AGENT_STEP, agent_name="backend"))
        assert bus.get_snapshot()["agents"]["backend"]["steps"] == 2

        await bus.emit(Event(event_type=EventType.AGENT_COMPLETE, agent_name="backend"))
        assert bus.get_snapshot()["agents"]["backend"]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_agent_error(self, bus):
        await bus.emit(Event(event_type=EventType.AGENT_SPAWN, agent_name="broken"))
        await bus.emit(Event(event_type=EventType.AGENT_ERROR, agent_name="broken"))
        assert bus.get_snapshot()["agents"]["broken"]["status"] == "error"

    @pytest.mark.asyncio
    async def test_agent_stalled(self, bus):
        await bus.emit(Event(event_type=EventType.AGENT_SPAWN, agent_name="slow"))
        await bus.emit(Event(event_type=EventType.AGENT_STALLED, agent_name="slow"))
        assert bus.get_snapshot()["agents"]["slow"]["status"] == "stalled"

    @pytest.mark.asyncio
    async def test_task_lifecycle(self, bus):
        await bus.emit(
            Event(
                event_type=EventType.TASK_ASSIGNED,
                data={
                    "task_id": "t1",
                    "from_agent": "lead",
                    "to_agent": "backend",
                    "description": "Build API",
                    "priority": "high",
                },
            )
        )
        snap = bus.get_snapshot()
        assert len(snap["tasks"]) == 1
        assert snap["tasks"][0]["status"] == "pending"
        assert snap["tasks"][0]["priority"] == "high"

        await bus.emit(
            Event(
                event_type=EventType.TASK_COMPLETED,
                data={"task_id": "t1", "success": True},
            )
        )
        assert bus.get_snapshot()["tasks"][0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_task_failed(self, bus):
        await bus.emit(
            Event(
                event_type=EventType.TASK_ASSIGNED,
                data={"task_id": "t2", "from_agent": "lead", "to_agent": "fe"},
            )
        )
        await bus.emit(
            Event(
                event_type=EventType.TASK_COMPLETED,
                data={"task_id": "t2", "success": False},
            )
        )
        assert bus.get_snapshot()["tasks"][0]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_metrics_update(self, bus):
        await bus.emit(Event(event_type=EventType.COST_UPDATE, data={"total_cost_usd": 1.23}))
        await bus.emit(Event(event_type=EventType.TOKEN_UPDATE, data={"total_tokens": 5000}))
        snap = bus.get_snapshot()
        assert snap["total_cost_usd"] == 1.23
        assert snap["total_tokens"] == 5000

    @pytest.mark.asyncio
    async def test_token_update_per_agent(self, bus):
        await bus.emit(Event(event_type=EventType.AGENT_SPAWN, agent_name="ai"))
        await bus.emit(
            Event(
                event_type=EventType.TOKEN_UPDATE,
                agent_name="ai",
                data={"agent_tokens": 300, "agent_cost_usd": 0.05},
            )
        )
        snap = bus.get_snapshot()
        assert snap["agents"]["ai"]["tokens"] == 300
        assert snap["agents"]["ai"]["cost_usd"] == 0.05

    @pytest.mark.asyncio
    async def test_graph_start(self, bus):
        await bus.emit(
            Event(
                event_type=EventType.GRAPH_START,
                data={
                    "nodes": ["a", "b"],
                    "edges": [{"source": "__start__", "target": "a"}],
                },
            )
        )
        snap = bus.get_snapshot()
        assert snap["graph"]["nodes"] == ["a", "b"]
        assert len(snap["graph"]["edges"]) == 1

    @pytest.mark.asyncio
    async def test_event_count(self, bus):
        await bus.emit(Event(event_type=EventType.AGENT_STEP))
        await bus.emit(Event(event_type=EventType.AGENT_STEP))
        await bus.emit(Event(event_type=EventType.AGENT_STEP))
        assert bus.get_snapshot()["event_count"] == 3


# ===== Instrumentation — Agent =====


class TestInstrumentAgent:
    @pytest.mark.asyncio
    async def test_agent_emits_spawn_and_complete(self, bus):
        original = Agent.execute
        _instrument_agent(bus)
        try:
            agent = Agent(
                config=AgentConfig(
                    name="test-agent",
                    role="tester",
                    provider_key="mock",
                    tools=[],
                ),
                provider=MockProvider(),
                skill_registry=SkillRegistry(),
            )
            result = await agent.execute(Task(description="do something"))
            assert result.status == TaskStatus.COMPLETED

            history = bus.get_history()
            types = [e.event_type for e in history]
            assert EventType.AGENT_SPAWN in types
            assert EventType.AGENT_COMPLETE in types
            assert EventType.TOKEN_UPDATE in types

            spawn = next(e for e in history if e.event_type == EventType.AGENT_SPAWN)
            assert spawn.agent_name == "test-agent"
            assert spawn.data["provider"] == "mock"
        finally:
            Agent.execute = original


# ===== Instrumentation — Graph =====


class TestInstrumentGraph:
    @pytest.mark.asyncio
    async def test_graph_emits_node_events(self, bus):
        orig_single = CompiledGraph._execute_single
        orig_parallel = CompiledGraph._execute_parallel
        _instrument_graph(bus)
        try:

            async def node_a(state):
                return {"value": state.get("value", 0) + 1}

            graph = StateGraph()
            graph.add_node("a", node_a)
            graph.add_edge(START, "a")
            graph.add_edge("a", END)
            compiled = graph.compile()

            result = await compiled.invoke({"value": 0})
            assert result.success
            assert result.state["value"] == 1

            history = bus.get_history()
            types = [e.event_type for e in history]
            assert EventType.GRAPH_NODE_ENTER in types
            assert EventType.GRAPH_NODE_EXIT in types

            enter = next(e for e in history if e.event_type == EventType.GRAPH_NODE_ENTER)
            assert enter.node_name == "a"
        finally:
            CompiledGraph._execute_single = orig_single
            CompiledGraph._execute_parallel = orig_parallel

    @pytest.mark.asyncio
    async def test_graph_emits_parallel_events(self, bus):
        orig_single = CompiledGraph._execute_single
        orig_parallel = CompiledGraph._execute_parallel
        _instrument_graph(bus)
        try:

            async def inc(state):
                return {"value": 1}

            graph = StateGraph(reducers={"value": lambda old, new: (old or 0) + new})
            graph.add_node("a", inc)
            graph.add_node("b", inc)
            graph.add_edge(START, "a")
            graph.add_edge(START, "b")
            graph.add_edge("a", END)
            graph.add_edge("b", END)
            compiled = graph.compile()

            result = await compiled.invoke({"value": 0})
            assert result.success

            types = [e.event_type for e in bus.get_history()]
            assert EventType.GRAPH_PARALLEL in types
        finally:
            CompiledGraph._execute_single = orig_single
            CompiledGraph._execute_parallel = orig_parallel


# ===== Instrumentation — Cooperation =====


class TestInstrumentCooperation:
    @pytest.mark.asyncio
    async def test_cooperation_emits_assign_event(self, bus):
        orig_assign = CooperationProtocol.assign
        orig_complete = CooperationProtocol.complete
        _instrument_cooperation(bus)
        try:
            proto = CooperationProtocol()
            assignment = TaskAssignment(
                task_id="t1",
                from_agent="lead",
                to_agent="backend",
                description="Build API",
            )
            proto.assign(assignment)
            await asyncio.sleep(0.05)

            types = [e.event_type for e in bus.get_history()]
            assert EventType.TASK_ASSIGNED in types

            assigned = next(e for e in bus.get_history() if e.event_type == EventType.TASK_ASSIGNED)
            assert assigned.data["task_id"] == "t1"
            assert assigned.data["to_agent"] == "backend"
        finally:
            CooperationProtocol.assign = orig_assign
            CooperationProtocol.complete = orig_complete

    @pytest.mark.asyncio
    async def test_cooperation_emits_complete_event(self, bus):
        orig_assign = CooperationProtocol.assign
        orig_complete = CooperationProtocol.complete
        _instrument_cooperation(bus)
        try:
            proto = CooperationProtocol()
            proto.assign(
                TaskAssignment(
                    task_id="t1",
                    from_agent="lead",
                    to_agent="backend",
                    description="Build API",
                )
            )
            proto.complete(
                TaskReport(task_id="t1", agent_name="backend", success=True, output="done")
            )
            await asyncio.sleep(0.05)

            types = [e.event_type for e in bus.get_history()]
            assert EventType.TASK_COMPLETED in types

            completed = next(
                e for e in bus.get_history() if e.event_type == EventType.TASK_COMPLETED
            )
            assert completed.data["task_id"] == "t1"
            assert completed.data["success"] is True
        finally:
            CooperationProtocol.assign = orig_assign
            CooperationProtocol.complete = orig_complete
