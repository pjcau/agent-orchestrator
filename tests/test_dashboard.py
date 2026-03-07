"""Tests for the dashboard event bus, snapshot, instrumentation, and team graph."""

import asyncio
from typing import AsyncIterator

import pytest
from agent_orchestrator.dashboard.events import Event, EventBus, EventType
from agent_orchestrator.dashboard.instrument import (
    _instrument_agent,
    _instrument_graph,
    _instrument_cooperation,
)
from agent_orchestrator.dashboard.graphs import (
    _agent_node,
    _build_team_graph,
    _last_run,
    get_last_run_info,
    list_openrouter_models,
    replay_node,
)
from agent_orchestrator.providers.openrouter import OpenRouterProvider
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


# ===== Agent Node Wrapper =====


class TestAgentNode:
    @pytest.mark.asyncio
    async def test_agent_node_emits_lifecycle_events(self, bus):
        """_agent_node should emit agent.spawn, agent.step, agent.complete."""
        provider = MockProvider()
        node = _agent_node(
            agent_name="test-agent",
            provider=provider,
            system="Be concise.",
            prompt_key="input",
            output_key="result",
            role="tester",
            event_bus=bus,
        )

        state = await node({"input": "hello"})
        assert "result" in state

        types = [e.event_type for e in bus.get_history()]
        assert EventType.AGENT_SPAWN in types
        assert EventType.AGENT_STEP in types
        assert EventType.AGENT_COMPLETE in types

        spawn = next(e for e in bus.get_history() if e.event_type == EventType.AGENT_SPAWN)
        assert spawn.agent_name == "test-agent"
        assert spawn.data["provider"] == "mock-1"
        assert spawn.data["role"] == "tester"

    @pytest.mark.asyncio
    async def test_agent_node_with_parent_emits_cooperation(self, bus):
        """_agent_node with parent_agent should emit task_assigned and task_completed."""
        provider = MockProvider()
        node = _agent_node(
            agent_name="sub-agent",
            provider=provider,
            system="Be concise.",
            prompt_key="input",
            output_key="result",
            role="worker",
            event_bus=bus,
            parent_agent="lead",
            task_description="Do work",
        )

        await node({"input": "test"})

        types = [e.event_type for e in bus.get_history()]
        assert EventType.TASK_ASSIGNED in types
        assert EventType.TASK_COMPLETED in types

        assigned = next(e for e in bus.get_history() if e.event_type == EventType.TASK_ASSIGNED)
        assert assigned.data["from_agent"] == "lead"
        assert assigned.data["to_agent"] == "sub-agent"
        assert assigned.data["description"] == "Do work"

        completed = next(e for e in bus.get_history() if e.event_type == EventType.TASK_COMPLETED)
        assert completed.data["from_agent"] == "sub-agent"
        assert completed.data["to_agent"] == "lead"
        assert completed.data["success"] is True

    @pytest.mark.asyncio
    async def test_agent_node_without_parent_no_cooperation(self, bus):
        """_agent_node without parent_agent should NOT emit cooperation events."""
        provider = MockProvider()
        node = _agent_node(
            agent_name="solo",
            provider=provider,
            system="Be concise.",
            prompt_key="input",
            output_key="result",
            event_bus=bus,
        )

        await node({"input": "test"})

        types = [e.event_type for e in bus.get_history()]
        assert EventType.TASK_ASSIGNED not in types
        assert EventType.TASK_COMPLETED not in types


# ===== Team Graph =====


class TestTeamGraph:
    def test_team_graph_structure(self):
        """_build_team_graph should produce correct graph topology."""
        provider = MockProvider()
        graph, initial_state = _build_team_graph(provider, "Build a todo app")

        assert "input" in initial_state
        assert initial_state["input"] == "Build a todo app"

        compiled = graph.compile()
        info = compiled.get_graph_info()

        # Should have 6 nodes: __start__, team-lead-plan, backend-dev, frontend-dev,
        #                       team-lead-summarize, __end__
        assert "team-lead-plan" in info["nodes"]
        assert "backend-dev" in info["nodes"]
        assert "frontend-dev" in info["nodes"]
        assert "team-lead-summarize" in info["nodes"]

    @pytest.mark.asyncio
    async def test_team_graph_execution(self):
        """Team graph should run end-to-end and produce output keys."""
        bus = EventBus()
        # Set singleton so _build_team_graph picks it up
        old_instance = EventBus._instance
        EventBus._instance = bus
        try:
            provider = MockProvider()
            graph, initial_state = _build_team_graph(provider, "Build an API")
            compiled = graph.compile()
            result = await compiled.invoke(initial_state)

            assert result.success
            # Should have all output keys
            assert "plan" in result.state
            assert "backend_output" in result.state
            assert "frontend_output" in result.state
            assert "response" in result.state

            # Should have emitted agent events
            types = [e.event_type for e in bus.get_history()]
            assert types.count(EventType.AGENT_SPAWN) >= 3  # team-lead, backend, frontend
            assert EventType.TASK_ASSIGNED in types
            assert EventType.TASK_COMPLETED in types
            assert EventType.AGENT_COMPLETE in types
        finally:
            EventBus._instance = old_instance

    @pytest.mark.asyncio
    async def test_team_graph_emits_delegation(self):
        """Team graph should show delegation from team-lead to sub-agents."""
        bus = EventBus()
        old_instance = EventBus._instance
        EventBus._instance = bus
        try:
            provider = MockProvider()
            graph, initial_state = _build_team_graph(provider, "Test task")
            compiled = graph.compile()
            await compiled.invoke(initial_state)

            assignments = [e for e in bus.get_history() if e.event_type == EventType.TASK_ASSIGNED]
            # backend-dev and frontend-dev should each get a task from team-lead
            assert len(assignments) == 2
            agents_assigned = {a.data["to_agent"] for a in assignments}
            assert "backend-dev" in agents_assigned
            assert "frontend-dev" in agents_assigned
            for a in assignments:
                assert a.data["from_agent"] == "team-lead"
        finally:
            EventBus._instance = old_instance


# ===== Replay Node =====


class TestReplayNode:
    @pytest.mark.asyncio
    async def test_replay_no_previous_run(self):
        """replay_node should fail if no previous run exists."""
        bus = EventBus()
        # Clear last run
        _last_run["result"] = None
        _last_run["compiled"] = None
        result = await replay_node("some-node", event_bus=bus)
        assert not result["success"]
        assert "No previous run" in result["error"]

    @pytest.mark.asyncio
    async def test_replay_unknown_node(self):
        """replay_node should fail for a node not in the last run."""
        bus = EventBus()
        old_instance = EventBus._instance
        EventBus._instance = bus
        try:
            provider = MockProvider()
            graph, initial_state = _build_team_graph(provider, "test")
            compiled = graph.compile()
            result = await compiled.invoke(initial_state)

            # Store in _last_run manually
            _last_run["result"] = result
            _last_run["compiled"] = compiled
            _last_run["model"] = "mock-1"

            replay_result = await replay_node("nonexistent-node", event_bus=bus)
            assert not replay_result["success"]
            assert "not found" in replay_result["error"]
        finally:
            EventBus._instance = old_instance

    @pytest.mark.asyncio
    async def test_replay_valid_node(self):
        """replay_node should re-run a node and return its output."""
        bus = EventBus()
        old_instance = EventBus._instance
        EventBus._instance = bus
        try:
            provider = MockProvider()
            graph, initial_state = _build_team_graph(provider, "Build API")
            compiled = graph.compile()
            result = await compiled.invoke(initial_state)

            _last_run["result"] = result
            _last_run["compiled"] = compiled
            _last_run["model"] = "mock-1"

            replay_result = await replay_node("backend-dev", event_bus=bus)
            assert replay_result["success"]
            assert replay_result["node"] == "backend-dev"
            assert replay_result["replay"] is True
            assert "output" in replay_result
        finally:
            EventBus._instance = old_instance


# ===== Get Last Run Info =====


class TestGetLastRunInfo:
    def test_no_previous_run(self):
        _last_run["result"] = None
        info = get_last_run_info()
        assert not info["has_run"]

    @pytest.mark.asyncio
    async def test_with_previous_run(self):
        bus = EventBus()
        old_instance = EventBus._instance
        EventBus._instance = bus
        try:
            provider = MockProvider()
            graph, initial_state = _build_team_graph(provider, "test prompt")
            compiled = graph.compile()
            result = await compiled.invoke(initial_state)

            _last_run["result"] = result
            _last_run["compiled"] = compiled
            _last_run["model"] = "mock-1"
            _last_run["graph_type"] = "team"
            _last_run["prompt"] = "test prompt"

            info = get_last_run_info()
            assert info["has_run"]
            assert info["model"] == "mock-1"
            assert info["graph_type"] == "team"
            assert len(info["nodes"]) >= 3
        finally:
            EventBus._instance = old_instance


# ===== OpenRouter Model List =====


class TestOpenRouterModels:
    @pytest.mark.asyncio
    async def test_list_returns_models_without_api_key(self):
        """Model list must be returned even without an API key."""
        models = await list_openrouter_models("")
        assert len(models) > 0, "Model list should not be empty when API key is missing"

    @pytest.mark.asyncio
    async def test_list_returns_models_with_api_key(self):
        """Model list should also work with an API key."""
        models = await list_openrouter_models("sk-fake-key-for-test")
        assert len(models) > 0

    @pytest.mark.asyncio
    async def test_all_models_have_required_fields(self):
        """Every model entry must have name, size, and provider fields."""
        models = await list_openrouter_models("")
        for m in models:
            assert "name" in m, f"Model missing 'name': {m}"
            assert "size" in m, f"Model missing 'size': {m}"
            assert "provider" in m, f"Model missing 'provider': {m}"
            assert m["provider"] == "openrouter"

    @pytest.mark.asyncio
    async def test_all_models_have_slash_in_name(self):
        """OpenRouter model names follow the org/model format."""
        models = await list_openrouter_models("")
        for m in models:
            assert "/" in m["name"], f"Model name should contain '/': {m['name']}"

    @pytest.mark.asyncio
    async def test_models_match_provider_catalog(self):
        """Every model in the dashboard list must exist in OpenRouterProvider.MODELS."""
        models = await list_openrouter_models("")
        provider_models = set(OpenRouterProvider.MODELS.keys())
        for m in models:
            assert m["name"] in provider_models, (
                f"Dashboard model '{m['name']}' not found in OpenRouterProvider.MODELS. "
                f"Available: {sorted(provider_models)}"
            )

    @pytest.mark.asyncio
    async def test_provider_catalog_matches_dashboard(self):
        """Every model in OpenRouterProvider.MODELS should appear in the dashboard list."""
        models = await list_openrouter_models("")
        dashboard_names = {m["name"] for m in models}
        for model_id in OpenRouterProvider.MODELS:
            assert model_id in dashboard_names, (
                f"Provider model '{model_id}' missing from dashboard model list"
            )

    @pytest.mark.asyncio
    async def test_no_duplicate_models(self):
        """Model list should not contain duplicates."""
        models = await list_openrouter_models("")
        names = [m["name"] for m in models]
        assert len(names) == len(set(names)), f"Duplicate models found: {names}"

    @pytest.mark.asyncio
    async def test_minimum_model_count(self):
        """At least 10 models should be available."""
        models = await list_openrouter_models("")
        assert len(models) >= 10, f"Expected at least 10 models, got {len(models)}"


# ===========================================================================
# Job Logger Tests
# ===========================================================================

class TestJobLogger:
    """Tests for the job persistence logger."""

    def test_creates_session_directory(self, tmp_path):
        from agent_orchestrator.dashboard.job_logger import JobLogger
        logger = JobLogger(jobs_dir=tmp_path / "jobs")
        assert logger.session_dir.exists()
        assert logger.session_dir.name.startswith("job_")

    def test_session_id_format(self, tmp_path):
        from agent_orchestrator.dashboard.job_logger import JobLogger
        logger = JobLogger(jobs_dir=tmp_path / "jobs")
        # Format: YYYYMMDD_HHMMSS_hexhex
        parts = logger.session_id.split("_")
        assert len(parts) == 3
        assert len(parts[0]) == 8  # YYYYMMDD
        assert len(parts[1]) == 6  # HHMMSS
        assert len(parts[2]) == 6  # hex

    def test_session_dir_has_job_prefix(self, tmp_path):
        from agent_orchestrator.dashboard.job_logger import JobLogger
        logger = JobLogger(jobs_dir=tmp_path / "jobs")
        assert logger.session_dir.name == f"job_{logger.session_id}"

    def test_log_creates_json_file(self, tmp_path):
        from agent_orchestrator.dashboard.job_logger import JobLogger
        import json
        logger = JobLogger(jobs_dir=tmp_path / "jobs")
        path = logger.log("prompt", {"prompt": "hello", "result": {"success": True}})
        assert path.exists()
        assert path.name == "0001_prompt.json"
        data = json.loads(path.read_text())
        assert data["job_type"] == "prompt"
        assert data["prompt"] == "hello"
        assert data["session_id"] == logger.session_id

    def test_sequential_job_numbers(self, tmp_path):
        from agent_orchestrator.dashboard.job_logger import JobLogger
        logger = JobLogger(jobs_dir=tmp_path / "jobs")
        p1 = logger.log("prompt", {"prompt": "a"})
        p2 = logger.log("agent_run", {"agent": "test"})
        p3 = logger.log("stream", {"prompt": "b"})
        assert p1.name == "0001_prompt.json"
        assert p2.name == "0002_agent_run.json"
        assert p3.name == "0003_stream.json"

    def test_log_contains_timestamp(self, tmp_path):
        from agent_orchestrator.dashboard.job_logger import JobLogger
        import json
        logger = JobLogger(jobs_dir=tmp_path / "jobs")
        path = logger.log("prompt", {"prompt": "test"})
        data = json.loads(path.read_text())
        assert "timestamp" in data
        assert "T" in data["timestamp"]  # ISO format

    def test_log_preserves_nested_data(self, tmp_path):
        from agent_orchestrator.dashboard.job_logger import JobLogger
        import json
        logger = JobLogger(jobs_dir=tmp_path / "jobs")
        result = {
            "success": True,
            "output": "Hello world",
            "steps_taken": 3,
            "total_tokens": 500,
            "total_cost_usd": 0.001,
        }
        path = logger.log("agent_run", {
            "agent": "backend",
            "task": "Write tests",
            "model": "qwen3-coder",
            "result": result,
        })
        data = json.loads(path.read_text())
        assert data["result"]["success"] is True
        assert data["result"]["total_tokens"] == 500
        assert data["agent"] == "backend"

    def test_inactivity_creates_new_session(self, tmp_path):
        from agent_orchestrator.dashboard.job_logger import JobLogger
        logger = JobLogger(jobs_dir=tmp_path / "jobs", inactivity_timeout_s=0.0)
        first_id = logger.session_id
        first_dir = logger.session_dir
        # Immediately expired (timeout=0), next access creates new session
        import time
        time.sleep(0.01)
        second_id = logger.session_id
        assert first_id != second_id
        assert first_dir != logger.session_dir

    def test_touch_keeps_session_alive(self, tmp_path):
        from agent_orchestrator.dashboard.job_logger import JobLogger
        logger = JobLogger(jobs_dir=tmp_path / "jobs", inactivity_timeout_s=10.0)
        session_id = logger.session_id
        logger.touch()
        assert logger.session_id == session_id
