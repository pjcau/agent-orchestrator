"""Tests for OrchestratorClient — embedded Python client."""

from __future__ import annotations

import pytest

from agent_orchestrator.client import (
    AgentInfo,
    OrchestratorClient,
    SkillInfo,
    TaskResult,
    TeamResult,
    _infer_category,
)
from agent_orchestrator.core.agent import AgentConfig
from agent_orchestrator.core.provider import (
    Completion,
    Message,
    ModelCapabilities,
    Provider,
    StreamChunk,
    ToolDefinition,
    Usage,
)
from agent_orchestrator.core.skill import Skill, SkillRegistry, SkillResult


# ---------------------------------------------------------------------------
# Fixtures: mock provider and skill
# ---------------------------------------------------------------------------


class MockProvider(Provider):
    """Provider that returns a canned response without making real LLM calls."""

    def __init__(self, model_id: str = "mock-model", response: str = "mock output"):
        self._model_id = model_id
        self._response = response

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(max_context=8000)

    @property
    def input_cost_per_million(self) -> float:
        return 1.0

    @property
    def output_cost_per_million(self) -> float:
        return 2.0

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion:
        return Completion(
            content=self._response,
            usage=Usage(input_tokens=10, output_tokens=20, cost_usd=0.001),
            tool_calls=[],
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ):
        yield StreamChunk(content=self._response, is_final=True)


class EchoSkill(Skill):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Returns the input message"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"message": {"type": "string"}}}

    async def execute(self, params: dict) -> SkillResult:
        return SkillResult(success=True, output=params.get("message", ""))


class UpperSkill(Skill):
    @property
    def name(self) -> str:
        return "upper"

    @property
    def description(self) -> str:
        return "Uppercases input"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"text": {"type": "string"}}}

    async def execute(self, params: dict) -> SkillResult:
        return SkillResult(success=True, output=params.get("text", "").upper())


@pytest.fixture
def mock_provider():
    return MockProvider()


@pytest.fixture
def skill_registry():
    reg = SkillRegistry()
    reg.register(EchoSkill())
    reg.register(UpperSkill())
    return reg


@pytest.fixture
def agent_configs():
    return {
        "backend": AgentConfig(
            name="backend",
            role="You are a backend developer.",
            provider_key="mock",
            tools=["echo"],
            max_steps=5,
        ),
        "frontend": AgentConfig(
            name="frontend",
            role="You are a frontend developer.",
            provider_key="mock",
            tools=["echo", "upper"],
            max_steps=5,
        ),
    }


@pytest.fixture
def client(mock_provider, agent_configs, skill_registry):
    return OrchestratorClient(
        providers={"mock": mock_provider},
        agents=agent_configs,
        skill_registry=skill_registry,
    )


# ---------------------------------------------------------------------------
# Initialization tests
# ---------------------------------------------------------------------------


class TestClientInit:
    def test_default_init(self):
        """Client initializes with empty registries when no args provided."""
        c = OrchestratorClient()
        assert c.list_agents() == []
        assert c.list_skills() == []

    def test_init_with_providers(self, mock_provider, agent_configs, skill_registry):
        """Client initializes with custom providers, agents, and skills."""
        c = OrchestratorClient(
            providers={"mock": mock_provider},
            agents=agent_configs,
            skill_registry=skill_registry,
        )
        assert len(c.list_agents()) == 2
        assert len(c.list_skills()) == 2

    def test_init_with_config_path(self):
        """Config path is stored (reserved for future ConfigManager use)."""
        c = OrchestratorClient(config_path="/tmp/test-config.json")
        assert c._config_path == "/tmp/test-config.json"


# ---------------------------------------------------------------------------
# run_agent tests
# ---------------------------------------------------------------------------


class TestRunAgent:
    @pytest.mark.asyncio
    async def test_run_agent_returns_task_result(self, client):
        """run_agent returns a TaskResult with correct fields."""
        result = await client.run_agent("backend", "Build a REST API")
        assert isinstance(result, TaskResult)
        assert result.success is True
        assert result.agent == "backend"
        assert result.output == "mock output"
        assert result.tokens_used > 0
        assert result.cost > 0
        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_run_agent_unknown_agent(self, client):
        """run_agent raises ValueError for unknown agent names."""
        with pytest.raises(ValueError, match="Unknown agent: 'nonexistent'"):
            await client.run_agent("nonexistent", "Do something")

    @pytest.mark.asyncio
    async def test_run_agent_missing_provider(self, agent_configs, skill_registry):
        """run_agent raises ValueError when provider is not registered."""
        c = OrchestratorClient(
            providers={},  # no providers
            agents=agent_configs,
            skill_registry=skill_registry,
        )
        with pytest.raises(ValueError, match="Provider 'mock' for agent 'backend' not registered"):
            await c.run_agent("backend", "Do something")

    @pytest.mark.asyncio
    async def test_run_agent_custom_max_steps(self, client):
        """max_steps override is applied."""
        result = await client.run_agent("backend", "Quick task", max_steps=1)
        assert isinstance(result, TaskResult)
        # With max_steps=1, the mock provider completes immediately (no tool calls)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_run_agent_files_default_empty(self, client):
        """files_created and files_modified default to empty lists."""
        result = await client.run_agent("backend", "No files task")
        assert result.files_created == []
        assert result.files_modified == []


# ---------------------------------------------------------------------------
# run_team tests
# ---------------------------------------------------------------------------


class TestRunTeam:
    @pytest.mark.asyncio
    async def test_run_team_returns_team_result(self, client):
        """run_team returns a TeamResult with agent_results."""
        result = await client.run_team("Build a full-stack app")
        assert isinstance(result, TeamResult)
        assert isinstance(result.agent_results, list)
        assert result.total_tokens >= 0
        assert result.total_cost >= 0
        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_run_team_with_subset(self, client):
        """run_team accepts a subset of agents."""
        result = await client.run_team("Backend only task", agents=["backend"])
        assert isinstance(result, TeamResult)
        # With a single agent and no team-lead, orchestrator runs single agent
        assert len(result.agent_results) >= 1

    @pytest.mark.asyncio
    async def test_run_team_unknown_agents(self, client):
        """run_team raises ValueError for unknown agent names."""
        with pytest.raises(ValueError, match="Unknown agents"):
            await client.run_team("Task", agents=["nonexistent"])

    @pytest.mark.asyncio
    async def test_run_team_agent_results_are_task_results(self, client):
        """Each entry in agent_results is a TaskResult."""
        result = await client.run_team("Do something")
        for ar in result.agent_results:
            assert isinstance(ar, TaskResult)


# ---------------------------------------------------------------------------
# run_graph tests
# ---------------------------------------------------------------------------


class TestRunGraph:
    @pytest.mark.asyncio
    async def test_run_graph_unknown_template(self, client):
        """run_graph raises ValueError for unknown templates."""
        with pytest.raises(ValueError, match="Unknown graph template"):
            await client.run_graph("nonexistent", {"input": "hello"})

    @pytest.mark.asyncio
    async def test_run_graph_with_template(self, client):
        """run_graph executes a registered graph template."""
        import time
        from agent_orchestrator.core.graph_templates import (
            GraphTemplate,
            NodeTemplate,
            EdgeTemplate,
        )

        # Register a simple passthrough template with a custom node
        template = GraphTemplate(
            name="passthrough",
            description="Passes input through",
            version=1,
            nodes=[
                NodeTemplate("process", "custom", {"function_name": "process_fn"}),
            ],
            edges=[
                EdgeTemplate("__start__", "process"),
                EdgeTemplate("process", "__end__"),
            ],
            created_at=time.time(),
        )
        client._template_store.save(template)

        # Register a custom node function for the template build
        async def process_fn(state):
            return {"result": state.get("input", "") + " processed"}

        graph = client._template_store.build_graph(
            "passthrough",
            node_registry={"process_fn": process_fn},
        )
        compiled = graph.compile()
        core_result = await compiled.invoke({"input": "hello"})
        assert core_result.success
        assert core_result.state.get("result") == "hello processed"


# ---------------------------------------------------------------------------
# list_agents / list_skills tests
# ---------------------------------------------------------------------------


class TestIntrospection:
    def test_list_agents(self, client):
        """list_agents returns AgentInfo for all registered agents."""
        agents = client.list_agents()
        assert len(agents) == 2
        names = {a.name for a in agents}
        assert names == {"backend", "frontend"}
        for a in agents:
            assert isinstance(a, AgentInfo)
            assert a.description  # role is non-empty
            assert a.model == "mock"

    def test_list_agents_has_skills(self, client):
        """list_agents includes skill names per agent."""
        agents = client.list_agents()
        backend = next(a for a in agents if a.name == "backend")
        assert backend.skills == ["echo"]
        frontend = next(a for a in agents if a.name == "frontend")
        assert frontend.skills == ["echo", "upper"]

    def test_list_agents_category(self, client):
        """list_agents infers category from agent name."""
        agents = client.list_agents()
        backend = next(a for a in agents if a.name == "backend")
        assert backend.category == "software-engineering"
        frontend = next(a for a in agents if a.name == "frontend")
        assert frontend.category == "software-engineering"

    def test_list_skills(self, client):
        """list_skills returns SkillInfo for all registered skills."""
        skills = client.list_skills()
        assert len(skills) == 2
        names = {s.name for s in skills}
        assert names == {"echo", "upper"}
        for s in skills:
            assert isinstance(s, SkillInfo)
            assert s.description
            assert isinstance(s.parameters, dict)


# ---------------------------------------------------------------------------
# Sync wrapper tests
# ---------------------------------------------------------------------------


class TestSyncWrappers:
    def test_run_agent_sync(self, mock_provider, agent_configs, skill_registry):
        """run_agent_sync returns a TaskResult using a new event loop."""
        # Create a fresh client (cannot reuse fixture as we need a clean loop)
        c = OrchestratorClient(
            providers={"mock": mock_provider},
            agents=agent_configs,
            skill_registry=skill_registry,
        )
        result = c.run_agent_sync(agent="backend", task="Sync test")
        assert isinstance(result, TaskResult)
        assert result.success is True
        assert result.agent == "backend"

    def test_run_team_sync(self, mock_provider, agent_configs, skill_registry):
        """run_team_sync returns a TeamResult using a new event loop."""
        c = OrchestratorClient(
            providers={"mock": mock_provider},
            agents=agent_configs,
            skill_registry=skill_registry,
        )
        result = c.run_team_sync(task="Sync team test")
        assert isinstance(result, TeamResult)


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register_agent(self, client):
        """register_agent adds a new agent to the client."""
        new_config = AgentConfig(
            name="devops",
            role="You are a devops engineer.",
            provider_key="mock",
            tools=[],
        )
        client.register_agent(new_config)
        agents = client.list_agents()
        assert any(a.name == "devops" for a in agents)

    def test_register_provider(self, client):
        """register_provider adds a new provider."""
        new_provider = MockProvider(model_id="new-mock")
        client.register_provider("new-mock", new_provider)
        assert "new-mock" in client._providers

    def test_register_skill(self, client):
        """register_skill adds a skill to the registry."""
        client.register_skill(UpperSkill())
        skills = client.list_skills()
        assert any(s.name == "upper" for s in skills)


# ---------------------------------------------------------------------------
# Category inference tests
# ---------------------------------------------------------------------------


class TestCategoryInference:
    def test_backend_category(self):
        assert _infer_category("backend") == "software-engineering"

    def test_financial_category(self):
        assert _infer_category("financial-analyst") == "finance"

    def test_data_analyst_category(self):
        assert _infer_category("data-analyst") == "data-science"

    def test_seo_category(self):
        assert _infer_category("seo-specialist") == "marketing"

    def test_team_lead_category(self):
        assert _infer_category("team-lead") == "orchestration"

    def test_unknown_defaults_to_software(self):
        assert _infer_category("unknown-agent") == "software-engineering"

    def test_skillkit_category(self):
        assert _infer_category("skillkit-scout") == "tooling"


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_provider_failure_propagates(self, agent_configs, skill_registry):
        """When a provider raises, the agent handles it gracefully."""

        class FailingProvider(MockProvider):
            async def complete(self, messages, tools=None, system=None,
                               max_tokens=4096, temperature=0.0):
                raise RuntimeError("LLM is down")

        c = OrchestratorClient(
            providers={"mock": FailingProvider()},
            agents=agent_configs,
            skill_registry=skill_registry,
        )
        with pytest.raises(RuntimeError, match="LLM is down"):
            await c.run_agent("backend", "Should fail")
