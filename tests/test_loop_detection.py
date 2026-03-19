"""Tests for loop detection middleware.

Covers: LoopDetector, LoopStatus, LoopDetectedError, _hash_tool_call,
LRU eviction, session reset, and integration with Agent.execute().
"""

import pytest

from agent_orchestrator.core.loop_detection import (
    LoopDetectedError,
    LoopDetector,
    LoopStatus,
    _hash_tool_call,
)
from agent_orchestrator.core.agent import (
    Agent,
    AgentConfig,
    Task,
    TaskStatus,
)
from agent_orchestrator.core.provider import (
    Completion,
    ModelCapabilities,
    Provider,
    StreamChunk,
    ToolCall,
    Usage,
)
from agent_orchestrator.core.skill import Skill, SkillRegistry, SkillResult


class _ReadFileSkill(Skill):
    """Fake skill for integration tests."""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read a file"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    async def execute(self, params: dict) -> SkillResult:
        return SkillResult(success=True, output=f"contents of {params.get('path', '/')}")


# ─── Hash Function ───────────────────────────────────────────────────


class TestHashToolCall:
    def test_same_params_same_hash(self):
        h1 = _hash_tool_call("read_file", {"path": "/a.txt"})
        h2 = _hash_tool_call("read_file", {"path": "/a.txt"})
        assert h1 == h2

    def test_different_params_different_hash(self):
        h1 = _hash_tool_call("read_file", {"path": "/a.txt"})
        h2 = _hash_tool_call("read_file", {"path": "/b.txt"})
        assert h1 != h2

    def test_different_tool_different_hash(self):
        h1 = _hash_tool_call("read_file", {"path": "/a.txt"})
        h2 = _hash_tool_call("write_file", {"path": "/a.txt"})
        assert h1 != h2

    def test_param_order_does_not_matter(self):
        h1 = _hash_tool_call("search", {"query": "test", "limit": 10})
        h2 = _hash_tool_call("search", {"limit": 10, "query": "test"})
        assert h1 == h2

    def test_nested_param_order_does_not_matter(self):
        h1 = _hash_tool_call("run", {"config": {"b": 2, "a": 1}})
        h2 = _hash_tool_call("run", {"config": {"a": 1, "b": 2}})
        assert h1 == h2

    def test_empty_params(self):
        h1 = _hash_tool_call("ping", {})
        h2 = _hash_tool_call("ping", {})
        assert h1 == h2


# ─── LoopDetector ────────────────────────────────────────────────────


class TestLoopDetector:
    def test_ok_on_first_call(self):
        detector = LoopDetector()
        status = detector.check("s1", "read_file", {"path": "/a.txt"})
        assert status == LoopStatus.OK

    def test_ok_on_two_identical_calls(self):
        detector = LoopDetector()
        for _ in range(2):
            status = detector.check("s1", "read_file", {"path": "/a.txt"})
        assert status == LoopStatus.OK

    def test_warning_at_three_identical_calls(self):
        detector = LoopDetector()
        for _ in range(2):
            detector.check("s1", "read_file", {"path": "/a.txt"})
        status = detector.check("s1", "read_file", {"path": "/a.txt"})
        assert status == LoopStatus.WARNING

    def test_hard_stop_at_five_identical_calls(self):
        detector = LoopDetector()
        for _ in range(4):
            detector.check("s1", "read_file", {"path": "/a.txt"})
        status = detector.check("s1", "read_file", {"path": "/a.txt"})
        assert status == LoopStatus.HARD_STOP

    def test_different_params_no_warning(self):
        detector = LoopDetector()
        for i in range(10):
            status = detector.check("s1", "read_file", {"path": f"/file_{i}.txt"})
        assert status == LoopStatus.OK

    def test_different_sessions_independent(self):
        detector = LoopDetector()
        for _ in range(3):
            detector.check("s1", "read_file", {"path": "/a.txt"})
        # s2 should be OK, not affected by s1
        status = detector.check("s2", "read_file", {"path": "/a.txt"})
        assert status == LoopStatus.OK

    def test_warning_between_warn_and_stop(self):
        detector = LoopDetector()
        statuses = []
        for _ in range(4):
            statuses.append(detector.check("s1", "read_file", {"path": "/a.txt"}))
        assert statuses == [
            LoopStatus.OK,
            LoopStatus.OK,
            LoopStatus.WARNING,
            LoopStatus.WARNING,
        ]

    def test_custom_thresholds(self):
        detector = LoopDetector(warn_threshold=2, stop_threshold=4, window_size=20)
        statuses = []
        for _ in range(4):
            statuses.append(detector.check("s1", "ping", {}))
        assert statuses == [
            LoopStatus.OK,
            LoopStatus.WARNING,
            LoopStatus.WARNING,
            LoopStatus.HARD_STOP,
        ]

    def test_sliding_window_evicts_old_entries(self):
        # Window of 5, warn at 3. Fill with 2 identical, then 3 different,
        # then the identical one again => only 1 in window => OK
        detector = LoopDetector(warn_threshold=3, stop_threshold=5, window_size=5)
        detector.check("s1", "read_file", {"path": "/a.txt"})
        detector.check("s1", "read_file", {"path": "/a.txt"})
        # Push 3 different calls to evict the first two from the window
        for i in range(3):
            detector.check("s1", "other_tool", {"idx": i})
        # Now only 1 identical call should be in the window
        status = detector.check("s1", "read_file", {"path": "/a.txt"})
        assert status == LoopStatus.OK

    def test_reset_clears_session(self):
        detector = LoopDetector()
        for _ in range(4):
            detector.check("s1", "read_file", {"path": "/a.txt"})
        detector.reset("s1")
        # After reset, should be back to OK
        status = detector.check("s1", "read_file", {"path": "/a.txt"})
        assert status == LoopStatus.OK

    def test_reset_nonexistent_session_no_error(self):
        detector = LoopDetector()
        detector.reset("nonexistent")  # should not raise

    def test_active_sessions_count(self):
        detector = LoopDetector()
        assert detector.active_sessions == 0
        detector.check("s1", "tool", {})
        assert detector.active_sessions == 1
        detector.check("s2", "tool", {})
        assert detector.active_sessions == 2
        detector.reset("s1")
        assert detector.active_sessions == 1


# ─── LRU Eviction ───────────────────────────────────────────────────


class TestLRUEviction:
    def test_eviction_at_max_sessions(self):
        detector = LoopDetector(max_sessions=3)
        detector.check("s1", "tool", {})
        detector.check("s2", "tool", {})
        detector.check("s3", "tool", {})
        assert detector.active_sessions == 3

        # Adding s4 should evict s1 (oldest)
        detector.check("s4", "tool", {})
        assert detector.active_sessions == 3
        assert "s1" not in detector._sessions
        assert "s4" in detector._sessions

    def test_lru_access_refreshes_session(self):
        detector = LoopDetector(max_sessions=3)
        detector.check("s1", "tool", {})
        detector.check("s2", "tool", {})
        detector.check("s3", "tool", {})

        # Access s1 to move it to the end (most recently used)
        detector.check("s1", "tool", {})

        # Adding s4 should evict s2 (now the oldest)
        detector.check("s4", "tool", {})
        assert "s1" in detector._sessions
        assert "s2" not in detector._sessions

    def test_eviction_at_500_sessions(self):
        detector = LoopDetector(max_sessions=500)
        for i in range(500):
            detector.check(f"s{i}", "tool", {})
        assert detector.active_sessions == 500

        # 501st session evicts the first
        detector.check("s500", "tool", {})
        assert detector.active_sessions == 500
        assert "s0" not in detector._sessions
        assert "s500" in detector._sessions


# ─── Validation ──────────────────────────────────────────────────────


class TestValidation:
    def test_warn_threshold_must_be_positive(self):
        with pytest.raises(ValueError, match="warn_threshold"):
            LoopDetector(warn_threshold=0)

    def test_stop_must_be_greater_than_warn(self):
        with pytest.raises(ValueError, match="stop_threshold"):
            LoopDetector(warn_threshold=3, stop_threshold=3)

    def test_window_must_be_at_least_stop(self):
        with pytest.raises(ValueError, match="window_size"):
            LoopDetector(stop_threshold=5, window_size=4)


# ─── LoopDetectedError ──────────────────────────────────────────────


class TestLoopDetectedError:
    def test_error_attributes(self):
        err = LoopDetectedError("read_file", 5, "session-1")
        assert err.tool_name == "read_file"
        assert err.count == 5
        assert err.session_id == "session-1"
        assert "read_file" in str(err)
        assert "5" in str(err)


# ─── Integration with Agent ─────────────────────────────────────────


class _FakeProvider(Provider):
    """Fake provider that returns tool calls N times then completes."""

    def __init__(self, tool_calls_count: int = 10):
        self._call_num = 0
        self._tool_calls_count = tool_calls_count

    @property
    def model_id(self) -> str:
        return "fake"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(max_context=4096, max_output_tokens=1024)

    @property
    def input_cost_per_million(self) -> float:
        return 1.0

    @property
    def output_cost_per_million(self) -> float:
        return 2.0

    async def complete(self, messages, tools=None, system=None, **kw):
        self._call_num += 1
        if self._call_num <= self._tool_calls_count:
            return Completion(
                content="calling tool",
                tool_calls=[
                    ToolCall(id=f"tc{self._call_num}", name="read_file", arguments={"path": "/same.txt"})
                ],
                usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.001),
            )
        return Completion(
            content="done",
            tool_calls=[],
            usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.001),
        )

    async def stream(self, messages, tools=None, system=None, **kw):
        yield StreamChunk(content="done", finish_reason="stop")


class TestAgentIntegration:
    def _make_agent(self, loop_detector: LoopDetector | None = None, tool_calls: int = 10):
        registry = SkillRegistry()
        registry.register(_ReadFileSkill())
        config = AgentConfig(
            name="test-agent",
            role="test",
            provider_key="fake",
            tools=["read_file"],
            max_steps=20,
            max_retries_per_approach=20,  # high to let loop detector trigger first
        )
        provider = _FakeProvider(tool_calls_count=tool_calls)
        return Agent(config, provider, registry, loop_detector=loop_detector)

    @pytest.mark.asyncio
    async def test_agent_without_loop_detector_completes(self):
        agent = self._make_agent(loop_detector=None, tool_calls=3)
        result = await agent.execute(Task(description="test"))
        assert result.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_agent_with_loop_detector_hard_stops(self):
        detector = LoopDetector(warn_threshold=2, stop_threshold=4, window_size=20)
        agent = self._make_agent(loop_detector=detector, tool_calls=10)
        result = await agent.execute(Task(description="test"), session_id="test-session")
        assert result.status == TaskStatus.FAILED
        assert "Loop" in result.error

    @pytest.mark.asyncio
    async def test_agent_loop_detector_needs_session_id(self):
        """Without session_id, loop detector is skipped even if present."""
        detector = LoopDetector(warn_threshold=2, stop_threshold=4, window_size=20)
        agent = self._make_agent(loop_detector=detector, tool_calls=3)
        result = await agent.execute(Task(description="test"))  # no session_id
        assert result.status == TaskStatus.COMPLETED


# ─── Event Types ─────────────────────────────────────────────────────


class TestEventTypes:
    def test_loop_event_types_exist(self):
        from agent_orchestrator.dashboard.events import EventType

        assert EventType.LOOP_WARNING.value == "loop.warning"
        assert EventType.LOOP_HARD_STOP.value == "loop.hard_stop"


# ─── Metrics Counters ───────────────────────────────────────────────


class TestMetricsCounters:
    def test_loop_metrics_registered(self):
        from agent_orchestrator.core.metrics import default_metrics

        reg = default_metrics()
        all_metrics = reg.get_all()
        assert "loop_warnings_total" in all_metrics
        assert "loop_hard_stops_total" in all_metrics
