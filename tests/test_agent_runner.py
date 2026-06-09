"""Tests for agent_runner helpers and dynamic team routing (v1.2)."""

from typing import AsyncIterator
from unittest.mock import patch

import pytest
from agent_orchestrator.dashboard.agent_runner import (
    _build_agent_catalog,
    _build_evidence,
    _build_role_for_agent,
    _detect_category,
    _parse_team_plan,
    _AGENT_ALIASES,
    _CATEGORY_FALLBACK_AGENTS,
    run_agent,
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
        {
            "name": "ml-engineer",
            "model": "opus",
            "description": "Model training and evaluation",
            "category": "data-science",
            "skills": [],
        },
        {
            "name": "financial-analyst",
            "model": "sonnet",
            "description": "Financial modeling and valuation",
            "category": "finance",
            "skills": [],
        },
        {
            "name": "risk-analyst",
            "model": "opus",
            "description": "Risk modeling and VaR",
            "category": "finance",
            "skills": [],
        },
        {
            "name": "quant-developer",
            "model": "opus",
            "description": "Algorithmic trading and backtesting",
            "category": "finance",
            "skills": [],
        },
        {
            "name": "compliance-officer",
            "model": "sonnet",
            "description": "Regulatory compliance and audit",
            "category": "finance",
            "skills": [],
        },
        {
            "name": "accountant",
            "model": "sonnet",
            "description": "Bookkeeping and tax prep",
            "category": "finance",
            "skills": [],
        },
        {
            "name": "content-strategist",
            "model": "sonnet",
            "description": "Content planning and brand voice",
            "category": "marketing",
            "skills": [],
        },
        {
            "name": "seo-specialist",
            "model": "sonnet",
            "description": "SEO and keyword research",
            "category": "marketing",
            "skills": [],
        },
        {
            "name": "growth-hacker",
            "model": "opus",
            "description": "Growth funnels and A/B testing",
            "category": "marketing",
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

    def test_fallback_for_missing_fields(self):
        role = _build_role_for_agent({})
        assert "agent" in role

    def test_software_engineering_includes_code_instruction(self):
        role = _build_role_for_agent(
            {"name": "frontend", "description": "UI", "category": "software-engineering"}
        )
        assert "Write actual code" in role

    def test_finance_category_role(self):
        role = _build_role_for_agent(
            {"name": "financial-analyst", "description": "Valuation", "category": "finance"}
        )
        assert "financial-analyst" in role
        assert "financial analysis" in role

    def test_data_science_category_role(self):
        role = _build_role_for_agent(
            {"name": "data-analyst", "description": "EDA", "category": "data-science"}
        )
        assert "data-analyst" in role
        assert "data-driven" in role

    def test_marketing_category_role(self):
        role = _build_role_for_agent(
            {"name": "content-strategist", "description": "Content", "category": "marketing"}
        )
        assert "content-strategist" in role
        assert "marketing" in role


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

    def test_finance_aliases(self):
        assert _AGENT_ALIASES["finance-analyst"] == "financial-analyst"
        assert _AGENT_ALIASES["risk"] == "risk-analyst"
        assert _AGENT_ALIASES["quant"] == "quant-developer"
        assert _AGENT_ALIASES["compliance"] == "compliance-officer"

    def test_data_science_aliases(self):
        assert _AGENT_ALIASES["data-science"] == "data-analyst"
        assert _AGENT_ALIASES["nlp"] == "nlp-specialist"
        assert _AGENT_ALIASES["bi"] == "bi-analyst"

    def test_marketing_aliases(self):
        assert _AGENT_ALIASES["content"] == "content-strategist"
        assert _AGENT_ALIASES["seo"] == "seo-specialist"
        assert _AGENT_ALIASES["growth"] == "growth-hacker"
        assert _AGENT_ALIASES["social"] == "social-media-manager"
        assert _AGENT_ALIASES["email"] == "email-marketer"


# ===== _detect_category =====


class TestDetectCategory:
    def test_finance_keywords(self):
        assert _detect_category("Build a DCF valuation model for portfolio analysis") == "finance"

    def test_data_science_keywords(self):
        assert _detect_category("Perform EDA and build a classification model") == "data-science"

    def test_marketing_keywords(self):
        assert _detect_category("Create an SEO content marketing strategy") == "marketing"

    def test_software_default(self):
        assert _detect_category("Build a REST API with authentication") == "software-engineering"

    def test_no_keywords_defaults_to_software(self):
        assert _detect_category("Do something") == "software-engineering"


# ===== _CATEGORY_FALLBACK_AGENTS =====


class TestCategoryFallbackAgents:
    def test_all_categories_present(self):
        assert "finance" in _CATEGORY_FALLBACK_AGENTS
        assert "data-science" in _CATEGORY_FALLBACK_AGENTS
        assert "marketing" in _CATEGORY_FALLBACK_AGENTS
        assert "software-engineering" in _CATEGORY_FALLBACK_AGENTS

    def test_each_category_has_agents(self):
        for category, agents in _CATEGORY_FALLBACK_AGENTS.items():
            assert len(agents) >= 2, f"{category} should have at least 2 fallback agents"
            for a in agents:
                assert "agent" in a
                assert "task" in a

    def test_task_templates_have_placeholder(self):
        for category, agents in _CATEGORY_FALLBACK_AGENTS.items():
            for a in agents:
                assert "{task}" in a["task"], (
                    f"{category}/{a['agent']} missing {{task}} placeholder"
                )


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
    async def test_team_result_reports_split_tokens_and_steps(self):
        """run_team must return an input/output token split and a step count
        so the agent-host TurnEnd shows real usage instead of ↑0 ↓0 (P2,
        docs/ago-cli-improvements.md)."""
        provider = MockProvider(
            responses=[
                '[{"agent": "backend", "task": "x"}]',
                "done",
                '{"sufficient": true}',
                "Summary.",
            ]
        )
        bus = EventBus()

        with patch(
            "agent_orchestrator.dashboard.agents_registry.get_agent_registry",
            return_value=MOCK_REGISTRY,
        ):
            result = await run_team(task_description="x", provider=provider, event_bus=bus)

        # Keys exist and are populated (team-lead plan/validation/summary plus
        # the backend sub-agent all report usage via MockProvider).
        assert result["input_tokens"] > 0
        assert result["output_tokens"] > 0
        assert result["steps_taken"] > 0
        # The split reconciles with the aggregate the dashboard already used.
        assert result["input_tokens"] + result["output_tokens"] == result["total_tokens"]

    @pytest.mark.asyncio
    async def test_skill_registry_override_reaches_every_sub_agent(self):
        """A client-tools team run must hand the SAME local skill registry to
        every spawned sub-agent, so their file/shell calls execute on the
        operator's machine instead of the server container."""
        plan_response = '[{"agent": "backend", "task": "API"}, {"agent": "frontend", "task": "UI"}]'
        provider = MockProvider(responses=[plan_response, '{"sufficient": true}', "Summary."])
        bus = EventBus()
        sentinel_registry = object()
        captured: list[dict] = []

        async def _fake_run_agent(*args, **kwargs):
            captured.append(kwargs)
            return {
                "success": True,
                "output": "done",
                "steps_taken": 1,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
            }

        with (
            patch(
                "agent_orchestrator.dashboard.agents_registry.get_agent_registry",
                return_value=MOCK_REGISTRY,
            ),
            patch("agent_orchestrator.dashboard.agent_runner.run_agent", _fake_run_agent),
        ):
            await run_team(
                task_description="Build a full-stack app",
                provider=provider,
                event_bus=bus,
                skill_registry_override=sentinel_registry,
            )

        # Both sub-agents ran, and each got the exact same override registry.
        assert len(captured) == 2
        assert all(c.get("skill_registry_override") is sentinel_registry for c in captured)
        # Override path bypasses any server-side sandbox for sub-agents.
        assert all(c.get("sandbox") is None for c in captured)

    @pytest.mark.asyncio
    async def test_no_skill_registry_override_passes_none(self):
        """Without an override, sub-agents must receive None so run_agent
        builds the standard server-side local registry (no behaviour change)."""
        plan_response = '[{"agent": "backend", "task": "API"}]'
        provider = MockProvider(responses=[plan_response, '{"sufficient": true}', "Summary."])
        bus = EventBus()
        captured: list[dict] = []

        async def _fake_run_agent(*args, **kwargs):
            captured.append(kwargs)
            return {"success": True, "output": "done", "steps_taken": 1}

        with (
            patch(
                "agent_orchestrator.dashboard.agents_registry.get_agent_registry",
                return_value=MOCK_REGISTRY,
            ),
            patch("agent_orchestrator.dashboard.agent_runner.run_agent", _fake_run_agent),
        ):
            await run_team(
                task_description="Build API",
                provider=provider,
                event_bus=bus,
            )

        assert len(captured) == 1
        assert captured[0].get("skill_registry_override") is None

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

    @pytest.mark.asyncio
    async def test_finance_dynamic_routing(self):
        """run_team should route finance tasks to finance agents."""
        plan_response = (
            '[{"agent": "financial-analyst", "task": "Build DCF model"}, '
            '{"agent": "risk-analyst", "task": "Assess portfolio risk"}]'
        )
        provider = MockProvider(
            responses=[plan_response, "done", "done", '{"sufficient": true}', "Summary."]
        )
        bus = EventBus()

        with patch(
            "agent_orchestrator.dashboard.agents_registry.get_agent_registry",
            return_value=MOCK_REGISTRY,
        ):
            result = await run_team(
                task_description="Analyze portfolio risk and create DCF valuation",
                provider=provider,
                event_bus=bus,
            )

        assert result["success"]
        assert result["used_fallback"] is False
        assert set(result["agents_selected"]) == {"financial-analyst", "risk-analyst"}

    @pytest.mark.asyncio
    async def test_finance_fallback_routing(self):
        """Unparseable plan for finance task should fallback to finance agents."""
        provider = MockProvider(
            responses=[
                "I think we should analyze this",  # Unparseable plan
                "done",  # financial-analyst
                "done",  # risk-analyst
                '{"sufficient": true}',
                "Summary.",
            ]
        )
        bus = EventBus()

        with patch(
            "agent_orchestrator.dashboard.agents_registry.get_agent_registry",
            return_value=MOCK_REGISTRY,
        ):
            result = await run_team(
                task_description="Calculate VaR for an equity portfolio with stocks and bonds",
                provider=provider,
                event_bus=bus,
            )

        assert result["success"]
        assert result["used_fallback"] is True
        assert set(result["agents_selected"]) == {"financial-analyst", "risk-analyst"}

    @pytest.mark.asyncio
    async def test_data_science_dynamic_routing(self):
        """run_team should route data science tasks to data-science agents."""
        plan_response = (
            '[{"agent": "data-analyst", "task": "Perform EDA"}, '
            '{"agent": "ml-engineer", "task": "Train classifier"}]'
        )
        provider = MockProvider(
            responses=[plan_response, "done", "done", '{"sufficient": true}', "Summary."]
        )
        bus = EventBus()

        with patch(
            "agent_orchestrator.dashboard.agents_registry.get_agent_registry",
            return_value=MOCK_REGISTRY,
        ):
            result = await run_team(
                task_description="Perform EDA and train a classification model on the dataset",
                provider=provider,
                event_bus=bus,
            )

        assert result["success"]
        assert result["used_fallback"] is False
        assert set(result["agents_selected"]) == {"data-analyst", "ml-engineer"}

    @pytest.mark.asyncio
    async def test_data_science_fallback_routing(self):
        """Unparseable plan for data task should fallback to data-science agents."""
        provider = MockProvider(
            responses=[
                "Let me look at the data",  # Unparseable
                "done",
                "done",
                '{"sufficient": true}',
                "Summary.",
            ]
        )
        bus = EventBus()

        with patch(
            "agent_orchestrator.dashboard.agents_registry.get_agent_registry",
            return_value=MOCK_REGISTRY,
        ):
            result = await run_team(
                task_description="Build a prediction model with feature engineering on the dataset",
                provider=provider,
                event_bus=bus,
            )

        assert result["success"]
        assert result["used_fallback"] is True
        assert set(result["agents_selected"]) == {"data-analyst", "ml-engineer"}

    @pytest.mark.asyncio
    async def test_marketing_dynamic_routing(self):
        """run_team should route marketing tasks to marketing agents."""
        plan_response = (
            '[{"agent": "content-strategist", "task": "Plan content"}, '
            '{"agent": "seo-specialist", "task": "Keyword research"}]'
        )
        provider = MockProvider(
            responses=[plan_response, "done", "done", '{"sufficient": true}', "Summary."]
        )
        bus = EventBus()

        with patch(
            "agent_orchestrator.dashboard.agents_registry.get_agent_registry",
            return_value=MOCK_REGISTRY,
        ):
            result = await run_team(
                task_description="Create an SEO content marketing strategy for product launch",
                provider=provider,
                event_bus=bus,
            )

        assert result["success"]
        assert result["used_fallback"] is False
        assert set(result["agents_selected"]) == {"content-strategist", "seo-specialist"}

    @pytest.mark.asyncio
    async def test_marketing_fallback_routing(self):
        """Unparseable plan for marketing task should fallback to marketing agents."""
        provider = MockProvider(
            responses=[
                "Let's think about this campaign",  # Unparseable
                "done",
                "done",
                '{"sufficient": true}',
                "Summary.",
            ]
        )
        bus = EventBus()

        with patch(
            "agent_orchestrator.dashboard.agents_registry.get_agent_registry",
            return_value=MOCK_REGISTRY,
        ):
            result = await run_team(
                task_description="Plan an email marketing campaign with conversion funnel",
                provider=provider,
                event_bus=bus,
            )

        assert result["success"]
        assert result["used_fallback"] is True
        assert set(result["agents_selected"]) == {"content-strategist", "seo-specialist"}

    @pytest.mark.asyncio
    async def test_finance_agents_get_correct_role_prompt(self):
        """Finance agents should get analysis-focused role, not code-writing role."""
        plan_response = '[{"agent": "financial-analyst", "task": "Analyze"}]'
        provider = MockProvider(
            responses=[plan_response, "done", '{"sufficient": true}', "Summary."]
        )
        bus = EventBus()

        with patch(
            "agent_orchestrator.dashboard.agents_registry.get_agent_registry",
            return_value=MOCK_REGISTRY,
        ):
            result = await run_team(
                task_description="Assess investment risk",
                provider=provider,
                event_bus=bus,
            )

        # Check that the finance agent was selected (not software)
        assert "financial-analyst" in result["agents_selected"]

    @pytest.mark.asyncio
    async def test_mixed_category_from_plan(self):
        """run_team should allow team-lead to mix agents from different categories."""
        plan_response = (
            '[{"agent": "financial-analyst", "task": "Financial analysis"}, '
            '{"agent": "quant-developer", "task": "Build trading strategy"}, '
            '{"agent": "risk-analyst", "task": "Risk assessment"}]'
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
                task_description="Design a trading strategy with risk controls",
                provider=provider,
                event_bus=bus,
            )

        assert result["success"]
        assert len(result["agents_selected"]) == 3
        assert set(result["agents_selected"]) == {
            "financial-analyst",
            "quant-developer",
            "risk-analyst",
        }


# ===== Token split (upstream/downstream meter) =====


class TestTokenSplit:
    """run_agent must surface input/output token split for the CLI meter.

    The agent-host token meter (`↑input ↓output`) depends on these fields
    being threaded out of the single-agent loop, both in the returned
    envelope and in the per-step TOKEN_UPDATE events.
    """

    @pytest.mark.asyncio
    async def test_return_envelope_carries_split(self):
        # One completion (no tool calls) → one step with 10 in / 5 out.
        provider = MockProvider(responses=["all done"])
        result = await run_agent(
            agent_name="backend",
            task_description="do a thing",
            provider=provider,
            max_steps=3,
        )
        assert result["success"] is True
        assert result["input_tokens"] == 10
        assert result["output_tokens"] == 5
        # Combined total stays consistent with the split.
        assert result["total_tokens"] == result["input_tokens"] + result["output_tokens"]

    @pytest.mark.asyncio
    async def test_token_update_event_carries_split(self):
        bus = EventBus()
        sub = bus.subscribe()
        provider = MockProvider(responses=["done"])
        await run_agent(
            agent_name="backend",
            task_description="x",
            provider=provider,
            max_steps=2,
            event_bus=bus,
        )
        # Drain the bus and look for a TOKEN_UPDATE that carries the split.
        seen_split = False
        while not sub.empty():
            event = sub.get_nowait()
            if event.event_type == EventType.TOKEN_UPDATE and isinstance(event.data, dict):
                if "input_tokens" in event.data and "output_tokens" in event.data:
                    assert event.data["input_tokens"] >= 0
                    assert event.data["output_tokens"] >= 0
                    seen_split = True
        assert seen_split, "no TOKEN_UPDATE event carried the input/output split"


# ===== _instrumented_execute: parity with the tested core loop =====
# The dashboard/agent-host run THIS loop (not core.Agent.execute). These tests
# assert it now carries the same compaction, circuit breaker, tool-result cap,
# and the minimal-changes steer — so production behaviour matches the core loop
# instead of drifting (a live run peaked at ~251k tokens and ignored the
# minimal-change rule because none of those lived in this loop).

from agent_orchestrator.dashboard.agent_runner import (  # noqa: E402
    _instrumented_execute,
    _MINIMAL_CHANGES_STEER,
)
from agent_orchestrator.core.agent import (  # noqa: E402
    AgentConfig,
    Task,
    TaskStatus,
    estimate_message_tokens,
)
from agent_orchestrator.core.provider import ToolCall  # noqa: E402
from agent_orchestrator.core.skill import Skill, SkillRegistry, SkillResult  # noqa: E402


@pytest.mark.asyncio
async def test_instrumented_system_prompt_carries_minimal_changes_steer():
    captured: dict = {}

    class _Capture(MockProvider):
        async def complete(self, messages, tools=None, system=None, **kwargs):
            captured["system"] = system
            return Completion(content="ok", tool_calls=[], usage=Usage(10, 5, 0.0))

    config = AgentConfig(name="a", role="You are backend.", provider_key="x", tools=[], max_steps=3)
    res = await _instrumented_execute(
        config=config,
        provider=_Capture(),
        skill_registry=SkillRegistry(),
        task=Task(description="hi"),
        event_bus=EventBus.get(),
    )
    assert res.status == TaskStatus.COMPLETED
    assert "You are backend." in captured["system"]
    assert _MINIMAL_CHANGES_STEER in captured["system"]


class _SpawnFailSkill(Skill):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "shell_exec"

    @property
    def description(self) -> str:
        return "shell"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"argv": {"type": "array"}}}

    async def execute(self, params: dict) -> SkillResult:
        self.calls += 1
        return SkillResult(success=False, output=None, error="shell_spawn_failed")


class _VaryingFailProvider(MockProvider):
    def __init__(self) -> None:
        super().__init__()
        self._n = 0

    async def complete(self, messages, tools=None, system=None, **kwargs):
        self._n += 1
        return Completion(
            content="trying",
            tool_calls=[
                ToolCall(
                    id=f"c{self._n}", name="shell_exec", arguments={"argv": ["x", f"#{self._n}"]}
                )
            ],
            usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.0),
        )


@pytest.mark.asyncio
async def test_instrumented_breaker_stops_varying_failure_grind():
    skill = _SpawnFailSkill()
    reg = SkillRegistry()
    reg.register(skill)
    config = AgentConfig(
        name="b",
        role="r",
        provider_key="x",
        tools=["shell_exec"],
        max_steps=40,
        max_retries_per_approach=99,
        max_tool_failures_per_approach=99,
        max_consecutive_tool_failures=4,
    )
    res = await _instrumented_execute(
        config=config,
        provider=_VaryingFailProvider(),
        skill_registry=reg,
        task=Task(description="run x"),
        event_bus=EventBus.get(),
    )
    assert res.status == TaskStatus.STALLED
    assert "Circuit breaker" in (res.error or "")
    assert skill.calls == 4  # stopped at the threshold, not 40
    assert res.steps_taken < 40
    assert "sandbox" in res.output or "jail" in res.output


class _BigReadSkill(Skill):
    @property
    def name(self) -> str:
        return "file_read"

    @property
    def description(self) -> str:
        return "read"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, params: dict) -> SkillResult:
        return SkillResult(success=True, output="D" * 40_000)


class _TokenizerProvider(MockProvider):
    def __init__(self, rounds: int) -> None:
        super().__init__()
        self._rounds = rounds
        self._n = 0
        self.sent: list[int] = []

    async def complete(self, messages, tools=None, system=None, **kwargs):
        sent = estimate_message_tokens(list(messages))
        self.sent.append(sent)
        self._n += 1
        usage = Usage(input_tokens=sent, output_tokens=5, cost_usd=0.0)
        if self._n <= self._rounds:
            return Completion(
                content="w",
                tool_calls=[ToolCall(id=f"c{self._n}", name="file_read", arguments={"p": self._n})],
                usage=usage,
            )
        return Completion(content="done", tool_calls=[], usage=usage)


@pytest.mark.asyncio
async def test_instrumented_compaction_bounds_sent_context():
    reg = SkillRegistry()
    reg.register(_BigReadSkill())
    threshold = 4000
    config = AgentConfig(
        name="c",
        role="r",
        provider_key="x",
        tools=["file_read"],
        max_steps=25,
        max_tool_result_chars=8000,
        compaction_token_threshold=threshold,
        compaction_target_ratio=0.6,
        compaction_keep_head=1,
        compaction_keep_tail=20,
        compaction_min_keep_tail=2,
    )
    provider = _TokenizerProvider(rounds=20)
    res = await _instrumented_execute(
        config=config,
        provider=provider,
        skill_registry=reg,
        task=Task(description="read"),
        event_bus=EventBus.get(),
    )
    assert res.status == TaskStatus.COMPLETED
    peak = max(provider.sent)
    # Bounded near the threshold instead of climbing with run length.
    assert peak < threshold * 2.5, f"context not bounded: peak={peak}"
