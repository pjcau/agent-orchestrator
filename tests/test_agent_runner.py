"""Tests for agent_runner helpers and dynamic team routing (v1.2)."""

from typing import AsyncIterator
from unittest.mock import patch

import pytest
from agent_orchestrator.dashboard.agent_runner import (
    _build_agent_catalog,
    _build_evidence,
    _build_role_for_agent,
    _parse_team_plan,
    _AGENT_ALIASES,
    run_team,
)
from agent_orchestrator.dashboard.events import EventBus, EventType
from agent_orchestrator.core.provider import (
    Completion,
    ModelCapabilities,
    Provider,
    StreamChunk,
    Usage,
)


# --- Mock registry ---

MOCK_REGISTRY = {
    "agents": [
        {
            "name": "team-lead",
            "model": "sonnet",
            "description": "Orchestrator",
            "category": "general",
            "skills": [],
        },
        {
            "name": "backend",
            "model": "sonnet",
            "description": "API and database",
            "category": "software-engineering",
            "skills": ["test-runner", "lint-check"],
        },
        {
            "name": "frontend",
            "model": "sonnet",
            "description": "UI and styling",
            "category": "software-engineering",
            "skills": ["website-dev"],
        },
        {
            "name": "devops",
            "model": "sonnet",
            "description": "Docker and CI/CD",
            "category": "software-engineering",
            "skills": ["docker-build"],
        },
        {
            "name": "scout",
            "model": "opus",
            "description": "GitHub pattern discovery",
            "category": "software-engineering",
            "skills": ["scout"],
        },
        {
            "name": "research-scout",
            "model": "opus",
            "description": "Web content analysis",
            "category": "software-engineering",
            "skills": [],
        },
        {
            "name": "skillkit-scout",
            "model": "opus",
            "description": "SkillKit marketplace",
            "category": "tooling",
            "skills": [],
        },
        {
            "name": "data-analyst",
            "model": "sonnet",
            "description": "EDA and visualization",
            "category": "data-science",
            "skills": [],
        },
    ],
}


class MockProvider(Provider):
    """Provider that returns configurable text completions."""

    def __init__(self, responses: list[str] | None = None):
        self._responses = responses or ["done"]
        self._call_count = 0

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
        idx = min(self._call_count, len(self._responses) - 1)
        content = self._responses[idx]
        self._call_count += 1
        return Completion(
            content=content,
            tool_calls=[],
            usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.001),
        )

    async def stream(
        self, messages, tools=None, system=None, **kwargs
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content="done", is_final=True)


# ===== _build_agent_catalog =====


class TestBuildAgentCatalog:
    def test_excludes_system_agents(self):
        catalog = _build_agent_catalog(MOCK_REGISTRY)
        assert "team-lead" not in catalog
        assert "scout" not in catalog
        assert "research-scout" not in catalog
        assert "skillkit-scout" not in catalog

    def test_includes_regular_agents(self):
        catalog = _build_agent_catalog(MOCK_REGISTRY)
        assert "backend" in catalog
        assert "frontend" in catalog
        assert "devops" in catalog
        assert "data-analyst" in catalog

    def test_includes_description_and_category(self):
        catalog = _build_agent_catalog(MOCK_REGISTRY)
        assert "API and database" in catalog
        assert "software-engineering" in catalog

    def test_includes_skills(self):
        catalog = _build_agent_catalog(MOCK_REGISTRY)
        assert "test-runner" in catalog
        assert "lint-check" in catalog

    def test_empty_registry(self):
        catalog = _build_agent_catalog({"agents": []})
        assert catalog == "Available agents:"

    def test_format_is_markdown_list(self):
        catalog = _build_agent_catalog(MOCK_REGISTRY)
        lines = catalog.strip().split("\n")
        assert lines[0] == "Available agents:"
        for line in lines[1:]:
            assert line.startswith("- ")


# ===== _parse_team_plan =====


class TestParseTeamPlan:
    VALID_NAMES = {"backend", "frontend", "devops", "data-analyst"}

    def test_valid_json(self):
        plan = (
            '[{"agent": "backend", "task": "Build API"}, {"agent": "frontend", "task": "Build UI"}]'
        )
        result = _parse_team_plan(plan, self.VALID_NAMES)
        assert result is not None
        assert len(result) == 2
        assert result[0] == {"agent": "backend", "task": "Build API"}
        assert result[1] == {"agent": "frontend", "task": "Build UI"}

    def test_markdown_fenced_json(self):
        plan = '```json\n[{"agent": "backend", "task": "Build API"}]\n```'
        result = _parse_team_plan(plan, self.VALID_NAMES)
        assert result is not None
        assert len(result) == 1
        assert result[0]["agent"] == "backend"

    def test_json_with_surrounding_text(self):
        plan = 'Here is my plan:\n[{"agent": "devops", "task": "Set up Docker"}]\nEnd of plan.'
        result = _parse_team_plan(plan, self.VALID_NAMES)
        assert result is not None
        assert result[0]["agent"] == "devops"

    def test_invalid_json(self):
        result = _parse_team_plan("not json at all", self.VALID_NAMES)
        assert result is None

    def test_empty_array(self):
        result = _parse_team_plan("[]", self.VALID_NAMES)
        assert result is None

    def test_unknown_agent_filtered(self):
        plan = '[{"agent": "unknown-agent", "task": "Do something"}]'
        result = _parse_team_plan(plan, self.VALID_NAMES)
        assert result is None

    def test_mixed_valid_invalid_agents(self):
        plan = '[{"agent": "backend", "task": "API"}, {"agent": "unknown", "task": "X"}]'
        result = _parse_team_plan(plan, self.VALID_NAMES)
        assert result is not None
        assert len(result) == 1
        assert result[0]["agent"] == "backend"

    def test_alias_resolution(self):
        plan = '[{"agent": "backend-dev", "task": "Build API"}]'
        result = _parse_team_plan(plan, self.VALID_NAMES)
        assert result is not None
        assert result[0]["agent"] == "backend"

    def test_frontend_dev_alias(self):
        plan = '[{"agent": "frontend-dev", "task": "Build UI"}]'
        result = _parse_team_plan(plan, self.VALID_NAMES)
        assert result is not None
        assert result[0]["agent"] == "frontend"

    def test_caps_at_5(self):
        items = [{"agent": "backend", "task": f"Task {i}"} for i in range(10)]
        import json

        plan = json.dumps(items)
        result = _parse_team_plan(plan, self.VALID_NAMES)
        assert result is not None
        assert len(result) <= 5

    def test_missing_task_skipped(self):
        plan = '[{"agent": "backend"}, {"agent": "frontend", "task": "Build UI"}]'
        result = _parse_team_plan(plan, self.VALID_NAMES)
        assert result is not None
        assert len(result) == 1
        assert result[0]["agent"] == "frontend"

    def test_case_insensitive_agent_name(self):
        plan = '[{"agent": "Backend", "task": "Build API"}]'
        result = _parse_team_plan(plan, self.VALID_NAMES)
        assert result is not None
        assert result[0]["agent"] == "backend"

    def test_no_array_found(self):
        result = _parse_team_plan('{"agent": "backend", "task": "X"}', self.VALID_NAMES)
        assert result is None


# ===== _build_role_for_agent =====


class TestBuildRoleForAgent:
    def test_builds_role_with_name_and_description(self):
        agent_info = {"name": "backend", "description": "API and database"}
        role = _build_role_for_agent(agent_info)
        assert "backend" in role
        assert "API and database" in role
        assert "file_write" in role

    def test_fallback_for_missing_fields(self):
        role = _build_role_for_agent({})
        assert "agent" in role

    def test_includes_code_writing_instruction(self):
        role = _build_role_for_agent({"name": "frontend", "description": "UI"})
        assert "Write actual code" in role


# ===== _build_evidence =====


class TestBuildEvidence:
    def test_builds_evidence_from_outputs(self):
        outputs = {"backend": "Created API endpoints", "frontend": "Built React components"}
        files = {"backend": ["src/api.py"], "frontend": ["src/App.tsx"]}
        steps = {"backend": ["wrote src/api.py"], "frontend": ["wrote src/App.tsx"]}

        parts = _build_evidence(outputs, files, steps)
        assert len(parts) == 2
        assert "**backend**" in parts[0]
        assert "src/api.py" in parts[0]
        assert "**frontend**" in parts[1]

    def test_handles_empty_files_and_steps(self):
        outputs = {"agent1": "Did work"}
        files: dict[str, list[str]] = {}
        steps: dict[str, list[str]] = {}

        parts = _build_evidence(outputs, files, steps)
        assert len(parts) == 1
        assert "**agent1**" in parts[0]
        assert "Agent message: Did work" in parts[0]

    def test_truncates_long_output(self):
        outputs = {"agent1": "x" * 1000}
        parts = _build_evidence(outputs, {}, {})
        # Output should be truncated to 500 chars
        assert len(parts[0]) < 1000


# ===== _AGENT_ALIASES =====


class TestAgentAliases:
    def test_backend_aliases(self):
        assert _AGENT_ALIASES["backend-dev"] == "backend"
        assert _AGENT_ALIASES["backend-developer"] == "backend"

    def test_frontend_aliases(self):
        assert _AGENT_ALIASES["frontend-dev"] == "frontend"
        assert _AGENT_ALIASES["frontend-developer"] == "frontend"

    def test_devops_alias(self):
        assert _AGENT_ALIASES["devops-engineer"] == "devops"

    def test_ml_alias(self):
        assert _AGENT_ALIASES["ml-eng"] == "ml-engineer"


# ===== run_team (integration) =====


class TestRunTeam:
    @pytest.mark.asyncio
    async def test_dynamic_routing_with_valid_plan(self):
        """run_team should use team-lead's plan to select agents dynamically."""
        plan_response = '[{"agent": "backend", "task": "Build API"}]'
        validation_response = '{"sufficient": true}'
        summary_response = "All done. Backend built the API."

        provider = MockProvider(
            responses=[plan_response, "done", validation_response, summary_response]
        )
        bus = EventBus()

        with patch(
            "agent_orchestrator.dashboard.agents_registry.get_agent_registry",
            return_value=MOCK_REGISTRY,
        ):
            result = await run_team(
                task_description="Build a REST API",
                provider=provider,
                event_bus=bus,
            )

        assert result["success"]
        assert result["agents_selected"] == ["backend"]
        assert result["used_fallback"] is False
        assert "output" in result

    @pytest.mark.asyncio
    async def test_fallback_on_invalid_plan(self):
        """run_team should fall back to backend+frontend when plan parsing fails."""
        provider = MockProvider(
            responses=[
                "I think we need some agents",  # Unparseable plan
                "done",  # backend agent
                "done",  # frontend agent
                '{"sufficient": true}',  # validation
                "Summary done.",  # summary
            ]
        )
        bus = EventBus()

        with patch(
            "agent_orchestrator.dashboard.agents_registry.get_agent_registry",
            return_value=MOCK_REGISTRY,
        ):
            result = await run_team(
                task_description="Build something",
                provider=provider,
                event_bus=bus,
            )

        assert result["success"]
        assert result["used_fallback"] is True
        assert set(result["agents_selected"]) == {"backend", "frontend"}

    @pytest.mark.asyncio
    async def test_multiple_agents_selected(self):
        """run_team should support multiple agents from the plan."""
        plan_response = (
            '[{"agent": "backend", "task": "API"}, '
            '{"agent": "devops", "task": "Docker"}, '
            '{"agent": "frontend", "task": "UI"}]'
        )
        provider = MockProvider(
            responses=[plan_response, "done", "done", "done", '{"sufficient": true}', "Summary."]
        )
        bus = EventBus()

        with patch(
            "agent_orchestrator.dashboard.agents_registry.get_agent_registry",
            return_value=MOCK_REGISTRY,
        ):
            result = await run_team(
                task_description="Full stack app",
                provider=provider,
                event_bus=bus,
            )

        assert result["success"]
        assert len(result["agents_selected"]) == 3
        assert set(result["agents_selected"]) == {"backend", "devops", "frontend"}

    @pytest.mark.asyncio
    async def test_emits_task_assigned_events(self):
        """run_team should emit TASK_ASSIGNED for each sub-agent."""
        plan_response = '[{"agent": "backend", "task": "Build API"}]'
        provider = MockProvider(
            responses=[plan_response, "done", '{"sufficient": true}', "Summary."]
        )
        bus = EventBus()

        with patch(
            "agent_orchestrator.dashboard.agents_registry.get_agent_registry",
            return_value=MOCK_REGISTRY,
        ):
            await run_team(
                task_description="Build API",
                provider=provider,
                event_bus=bus,
            )

        assigned = [e for e in bus.get_history() if e.event_type == EventType.TASK_ASSIGNED]
        assert len(assigned) >= 1
        assert assigned[0].data["to_agent"] == "backend"
        assert assigned[0].data["from_agent"] == "team-lead"

    @pytest.mark.asyncio
    async def test_tracks_costs_per_agent(self):
        """run_team should report per-agent cost breakdowns."""
        plan_response = '[{"agent": "backend", "task": "Build API"}]'
        provider = MockProvider(
            responses=[plan_response, "done", '{"sufficient": true}', "Summary."]
        )
        bus = EventBus()

        with patch(
            "agent_orchestrator.dashboard.agents_registry.get_agent_registry",
            return_value=MOCK_REGISTRY,
        ):
            result = await run_team(
                task_description="Build API",
                provider=provider,
                event_bus=bus,
            )

        assert "agent_costs" in result
        assert "team-lead (plan)" in result["agent_costs"]
        assert "team-lead (validation)" in result["agent_costs"]
        assert "team-lead (summary)" in result["agent_costs"]

    @pytest.mark.asyncio
    async def test_max_sub_agents_cap(self):
        """run_team should respect max_sub_agents parameter."""
        items = [{"agent": "backend", "task": f"Task {i}"} for i in range(10)]
        import json

        plan_response = json.dumps(items)
        provider = MockProvider(
            responses=[plan_response] + ["done"] * 5 + ['{"sufficient": true}', "Summary."]
        )
        bus = EventBus()

        with patch(
            "agent_orchestrator.dashboard.agents_registry.get_agent_registry",
            return_value=MOCK_REGISTRY,
        ):
            result = await run_team(
                task_description="Many tasks",
                provider=provider,
                event_bus=bus,
                max_sub_agents=2,
            )

        assert result["success"]
        assert len(result["agents_selected"]) <= 2

    @pytest.mark.asyncio
    async def test_result_includes_elapsed_time(self):
        """run_team result should include elapsed_s."""
        plan_response = '[{"agent": "backend", "task": "Build API"}]'
        provider = MockProvider(
            responses=[plan_response, "done", '{"sufficient": true}', "Summary."]
        )
        bus = EventBus()

        with patch(
            "agent_orchestrator.dashboard.agents_registry.get_agent_registry",
            return_value=MOCK_REGISTRY,
        ):
            result = await run_team(
                task_description="Build API",
                provider=provider,
                event_bus=bus,
            )

        assert "elapsed_s" in result
        assert isinstance(result["elapsed_s"], float)
