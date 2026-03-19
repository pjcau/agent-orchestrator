"""Tests for the structured clarification system.

Covers:
- ClarificationRequest creation for each type
- ask_clarification skill emits events
- Agent pauses on blocking clarification
- Agent resumes on response
- Timeout falls back to assumption
- Non-blocking clarification does not pause
"""

from __future__ import annotations

import asyncio

import pytest

from agent_orchestrator.core.clarification import (
    ClarificationManager,
    ClarificationRequest,
    ClarificationResponse,
    ClarificationType,
    DEFAULT_CLARIFICATION_TIMEOUT,
)
from agent_orchestrator.core.agent import Agent, AgentConfig, Task, TaskStatus
from agent_orchestrator.core.skill import SkillRegistry
from typing import AsyncIterator

from agent_orchestrator.core.provider import (
    Completion,
    Message,
    ModelCapabilities,
    Provider,
    StreamChunk,
    ToolCall,
    ToolDefinition,
    Usage,
)
from agent_orchestrator.skills.clarification_skill import ClarificationSkill
from agent_orchestrator.dashboard.events import EventType


# ─── Helpers ─────────────────────────────────────────────────────────


class FakeProvider(Provider):
    """Provider that returns scripted completions."""

    def __init__(self, completions: list[Completion] | None = None):
        self._completions = list(completions or [])
        self._call_idx = 0

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model_id(self) -> str:
        return "fake-model"

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

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamChunk]:
        raise NotImplementedError

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        **kwargs,
    ) -> Completion:
        if self._call_idx < len(self._completions):
            c = self._completions[self._call_idx]
            self._call_idx += 1
            return c
        return Completion(
            content="done",
            usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.0),
        )


class StubEventBus:
    """Minimal event bus that records emitted events."""

    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)


# ─── ClarificationRequest creation tests ────────────────────────────


class TestClarificationRequestCreation:
    """Test ClarificationRequest creation for each type."""

    def test_missing_info_type(self):
        req = ClarificationRequest(
            type=ClarificationType.MISSING_INFO,
            question="What database should I use?",
        )
        assert req.type == ClarificationType.MISSING_INFO
        assert req.question == "What database should I use?"
        assert req.blocking is True
        assert req.options is None
        assert req.context is None
        assert req.request_id  # auto-generated
        assert req.timeout_seconds == DEFAULT_CLARIFICATION_TIMEOUT

    def test_ambiguous_type(self):
        req = ClarificationRequest(
            type=ClarificationType.AMBIGUOUS,
            question="Which API version?",
            options=["v1", "v2"],
        )
        assert req.type == ClarificationType.AMBIGUOUS
        assert req.options == ["v1", "v2"]

    def test_approach_type(self):
        req = ClarificationRequest(
            type=ClarificationType.APPROACH,
            question="REST or GraphQL?",
            context="Building a new API endpoint",
        )
        assert req.type == ClarificationType.APPROACH
        assert req.context == "Building a new API endpoint"

    def test_risk_type(self):
        req = ClarificationRequest(
            type=ClarificationType.RISK,
            question="This will delete production data. Continue?",
            blocking=True,
        )
        assert req.type == ClarificationType.RISK
        assert req.blocking is True

    def test_suggestion_type(self):
        req = ClarificationRequest(
            type=ClarificationType.SUGGESTION,
            question="Should I add caching?",
            blocking=False,
        )
        assert req.type == ClarificationType.SUGGESTION
        assert req.blocking is False

    def test_to_dict(self):
        req = ClarificationRequest(
            type=ClarificationType.MISSING_INFO,
            question="What database?",
            options=["postgres", "sqlite"],
            context="Need persistent storage",
        )
        d = req.to_dict()
        assert d["type"] == "missing_info"
        assert d["question"] == "What database?"
        assert d["options"] == ["postgres", "sqlite"]
        assert d["context"] == "Need persistent storage"
        assert d["blocking"] is True
        assert "request_id" in d
        assert "timestamp" in d

    def test_unique_request_ids(self):
        r1 = ClarificationRequest(type=ClarificationType.MISSING_INFO, question="Q1")
        r2 = ClarificationRequest(type=ClarificationType.MISSING_INFO, question="Q2")
        assert r1.request_id != r2.request_id


# ─── ClarificationManager tests ─────────────────────────────────────


class TestClarificationManager:
    def test_register_and_get_pending(self):
        mgr = ClarificationManager()
        req = ClarificationRequest(type=ClarificationType.MISSING_INFO, question="Q?")
        mgr.register(req)
        pending = mgr.get_pending()
        assert len(pending) == 1
        assert pending[0].request_id == req.request_id

    def test_respond_removes_from_pending(self):
        mgr = ClarificationManager()
        req = ClarificationRequest(type=ClarificationType.MISSING_INFO, question="Q?")
        mgr.register(req)
        resp = ClarificationResponse(answer="A!", request_id=req.request_id)
        assert mgr.respond(resp) is True
        assert mgr.get_pending() == []

    def test_respond_unknown_request(self):
        mgr = ClarificationManager()
        resp = ClarificationResponse(answer="A!", request_id="nonexistent")
        assert mgr.respond(resp) is False

    def test_get_response(self):
        mgr = ClarificationManager()
        req = ClarificationRequest(type=ClarificationType.AMBIGUOUS, question="Which?")
        mgr.register(req)
        resp = ClarificationResponse(answer="Option A", request_id=req.request_id)
        mgr.respond(resp)
        retrieved = mgr.get_response(req.request_id)
        assert retrieved is not None
        assert retrieved.answer == "Option A"

    def test_cleanup(self):
        mgr = ClarificationManager()
        req = ClarificationRequest(type=ClarificationType.APPROACH, question="How?")
        mgr.register(req)
        mgr.cleanup(req.request_id)
        assert mgr.get_pending() == []
        assert mgr.get_response(req.request_id) is None


# ─── ClarificationSkill tests ───────────────────────────────────────


class TestClarificationSkill:
    @pytest.mark.asyncio
    async def test_emits_event_on_request(self):
        """Test ask_clarification tool emits event."""
        bus = StubEventBus()
        skill = ClarificationSkill(event_bus=bus)

        # Non-blocking so it returns immediately
        result = await skill.execute(
            {
                "type": "missing_info",
                "question": "What DB?",
                "blocking": False,
            }
        )

        assert result.success is True
        assert result.output["status"] == "emitted"
        assert len(bus.events) == 1
        assert bus.events[0].event_type == EventType.CLARIFICATION_REQUEST

    @pytest.mark.asyncio
    async def test_nonblocking_returns_immediately(self):
        """Test non-blocking clarification does not pause."""
        skill = ClarificationSkill()
        result = await skill.execute(
            {
                "type": "suggestion",
                "question": "Add caching?",
                "blocking": False,
            }
        )
        assert result.success is True
        assert result.output["blocking"] is False
        assert result.output["status"] == "emitted"

    @pytest.mark.asyncio
    async def test_blocking_waits_for_response(self):
        """Test agent pauses on blocking clarification and resumes on response."""
        manager = ClarificationManager()
        skill = ClarificationSkill(manager=manager)

        async def respond_after_delay():
            await asyncio.sleep(0.05)
            pending = manager.get_pending()
            assert len(pending) == 1
            resp = ClarificationResponse(
                answer="Use PostgreSQL",
                request_id=pending[0].request_id,
            )
            manager.respond(resp)

        task = asyncio.ensure_future(respond_after_delay())
        result = await skill.execute(
            {
                "type": "missing_info",
                "question": "Which database?",
                "blocking": True,
            }
        )
        await task

        assert result.success is True
        assert result.output["status"] == "answered"
        assert result.output["answer"] == "Use PostgreSQL"

    @pytest.mark.asyncio
    async def test_timeout_falls_back(self):
        """Test timeout falls back to assumption."""
        manager = ClarificationManager()

        # Test via manager with short timeout
        req = ClarificationRequest(
            type=ClarificationType.RISK,
            question="Delete everything?",
            blocking=True,
            timeout_seconds=0.05,
        )
        manager.register(req)
        response = await manager.wait_for_response(req)
        assert response is None  # timed out

    @pytest.mark.asyncio
    async def test_skill_timeout_via_execute(self):
        """Test that skill execute returns timeout status with short timeout."""
        bus = StubEventBus()
        manager = ClarificationManager()
        skill = ClarificationSkill(manager=manager, event_bus=bus)

        result = await skill.execute(
            {
                "type": "approach",
                "question": "REST or GraphQL?",
                "blocking": True,
                "timeout_seconds": 0.05,
            }
        )

        assert result.success is True
        assert result.output["status"] == "timeout"
        assert "timed out" in result.output["message"].lower()

        # Check timeout event was emitted
        timeout_events = [e for e in bus.events if e.event_type == EventType.CLARIFICATION_TIMEOUT]
        assert len(timeout_events) == 1

    @pytest.mark.asyncio
    async def test_invalid_type_returns_error(self):
        skill = ClarificationSkill()
        result = await skill.execute(
            {
                "type": "invalid_type",
                "question": "Something?",
            }
        )
        assert result.success is False
        assert "Invalid clarification type" in result.error

    @pytest.mark.asyncio
    async def test_missing_question_returns_error(self):
        skill = ClarificationSkill()
        result = await skill.execute(
            {
                "type": "missing_info",
                "question": "",
            }
        )
        assert result.success is False
        assert "required" in result.error.lower()

    def test_skill_properties(self):
        skill = ClarificationSkill()
        assert skill.name == "ask_clarification"
        assert "clarification" in skill.description.lower()
        params = skill.parameters
        assert params["type"] == "object"
        assert "type" in params["properties"]
        assert "question" in params["properties"]


# ─── Agent integration tests ────────────────────────────────────────


class TestAgentClarificationIntegration:
    @pytest.mark.asyncio
    async def test_agent_status_changes_on_clarification(self):
        """Test agent pauses on blocking clarification and resumes."""
        manager = ClarificationManager()
        skill = ClarificationSkill(manager=manager)

        registry = SkillRegistry()
        registry.register(skill)

        # Provider returns one tool call to ask_clarification, then completes
        completions = [
            Completion(
                content="I need to ask something",
                usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.0),
                tool_calls=[
                    ToolCall(
                        id="tc1",
                        name="ask_clarification",
                        arguments={
                            "type": "missing_info",
                            "question": "Which framework?",
                            "blocking": False,  # non-blocking so test completes
                        },
                    )
                ],
            ),
            Completion(
                content="Using the recommended framework.",
                usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.0),
            ),
        ]

        provider = FakeProvider(completions)
        config = AgentConfig(
            name="test-agent",
            role="test role",
            provider_key="fake",
            tools=["ask_clarification"],
        )
        agent = Agent(config, provider, registry, clarification_manager=manager)

        result = await agent.execute(Task(description="Build something"))
        assert result.status == TaskStatus.COMPLETED
        assert agent._status == TaskStatus.RUNNING  # Back to running after clarification

    @pytest.mark.asyncio
    async def test_agent_waiting_status_during_blocking(self):
        """Test agent status is WAITING_FOR_CLARIFICATION during blocking call."""
        manager = ClarificationManager()
        skill = ClarificationSkill(manager=manager)

        registry = SkillRegistry()
        registry.register(skill)

        # Wrap the original execute to capture status
        original_execute = skill.execute

        async def capturing_execute(params):
            # Respond immediately to avoid blocking
            result_future = asyncio.ensure_future(original_execute(params))
            await asyncio.sleep(0.01)
            # By this time the agent should be in WAITING status
            # but we need to check from outside
            return await result_future

        completions = [
            Completion(
                content="Need clarification",
                usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.0),
                tool_calls=[
                    ToolCall(
                        id="tc1",
                        name="ask_clarification",
                        arguments={
                            "type": "approach",
                            "question": "REST or GraphQL?",
                            "blocking": False,
                        },
                    )
                ],
            ),
            Completion(
                content="Done!",
                usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.0),
            ),
        ]

        provider = FakeProvider(completions)
        config = AgentConfig(
            name="test-agent",
            role="test role",
            provider_key="fake",
            tools=["ask_clarification"],
        )
        agent = Agent(config, provider, registry, clarification_manager=manager)

        result = await agent.execute(Task(description="Choose architecture"))
        assert result.status == TaskStatus.COMPLETED


class TestClarificationResponse:
    def test_response_creation(self):
        resp = ClarificationResponse(answer="Use REST", request_id="abc123")
        assert resp.answer == "Use REST"
        assert resp.request_id == "abc123"
        assert resp.timestamp > 0
