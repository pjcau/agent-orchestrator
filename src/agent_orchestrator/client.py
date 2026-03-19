"""OrchestratorClient — programmatic Python client for the agent orchestrator.

No HTTP server required. Wraps the core Orchestrator, Agent, SkillRegistry,
and StateGraph APIs into a single ergonomic interface suitable for embedding
in scripts, notebooks, and application code.

Usage::

    from agent_orchestrator.client import OrchestratorClient

    client = OrchestratorClient()
    result = await client.run_agent("backend", "Build a REST API for users")
    print(result.output)

    # Synchronous wrapper (creates its own event loop)
    result = client.run_agent_sync(agent="backend", task="Build a REST API")
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .core.agent import Agent, AgentConfig, Task, TaskStatus
from .core.agent import TaskResult as CoreTaskResult
from .core.graph import GraphResult as CoreGraphResult
from .core.graph_templates import GraphTemplateStore
from .core.orchestrator import (
    Orchestrator,
    OrchestratorConfig,
    OrchestratorResult,
    RoutingStrategy,
)
from .core.provider import Provider
from .core.skill import Skill, SkillRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class TaskResult:
    """Result of a single agent execution."""

    success: bool
    output: str
    agent: str
    tokens_used: int
    cost: float
    duration_seconds: float
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)


@dataclass
class TeamResult:
    """Result of a multi-agent team execution."""

    success: bool
    summary: str
    agent_results: list[TaskResult]
    total_tokens: int
    total_cost: float
    duration_seconds: float


@dataclass
class GraphResult:
    """Result of a graph execution."""

    success: bool
    state: dict[str, Any]
    steps: int
    error: str | None = None


@dataclass
class AgentInfo:
    """Metadata about a registered agent."""

    name: str
    description: str
    category: str
    model: str
    skills: list[str] = field(default_factory=list)


@dataclass
class SkillInfo:
    """Metadata about a registered skill."""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class OrchestratorClient:
    """Programmatic access to the agent orchestrator. No HTTP, no server required.

    Args:
        config_path: Optional path to a JSON configuration file (reserved for
            future use with ConfigManager).
        providers: Optional dict mapping provider keys to Provider instances.
            If not given, an empty registry is used.
        agents: Optional dict mapping agent names to AgentConfig instances.
        skill_registry: Optional pre-populated SkillRegistry.
        routing_strategy: Routing strategy for the orchestrator (default: FIXED).
        cost_budget_usd: Optional cost budget for orchestrator runs.
    """

    def __init__(
        self,
        config_path: str | None = None,
        providers: dict[str, Provider] | None = None,
        agents: dict[str, AgentConfig] | None = None,
        skill_registry: SkillRegistry | None = None,
        routing_strategy: RoutingStrategy = RoutingStrategy.FIXED,
        cost_budget_usd: float | None = None,
    ):
        self._providers: dict[str, Provider] = providers or {}
        self._agent_configs: dict[str, AgentConfig] = agents or {}
        self._skill_registry = skill_registry or SkillRegistry()
        self._template_store = GraphTemplateStore()

        self._orchestrator_config = OrchestratorConfig(
            routing_strategy=routing_strategy,
            cost_budget_usd=cost_budget_usd,
        )

        self._orchestrator = Orchestrator(
            config=self._orchestrator_config,
            agents=self._agent_configs,
            providers=self._providers,
            skill_registry=self._skill_registry,
        )

        self._config_path = config_path
        logger.info(
            "OrchestratorClient initialized with %d providers, %d agents, %d skills",
            len(self._providers),
            len(self._agent_configs),
            len(self._skill_registry.list_skills()),
        )

    # ------------------------------------------------------------------
    # Agent management
    # ------------------------------------------------------------------

    def register_agent(self, config: AgentConfig) -> None:
        """Register an agent configuration."""
        self._agent_configs[config.name] = config
        # Rebuild orchestrator with updated agents
        self._orchestrator = Orchestrator(
            config=self._orchestrator_config,
            agents=self._agent_configs,
            providers=self._providers,
            skill_registry=self._skill_registry,
        )

    def register_provider(self, key: str, provider: Provider) -> None:
        """Register a provider under the given key."""
        self._providers[key] = provider
        # Rebuild orchestrator with updated providers
        self._orchestrator = Orchestrator(
            config=self._orchestrator_config,
            agents=self._agent_configs,
            providers=self._providers,
            skill_registry=self._skill_registry,
        )

    def register_skill(self, skill: Skill) -> None:
        """Register a skill in the skill registry."""
        self._skill_registry.register(skill)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def run_agent(
        self,
        agent: str,
        task: str,
        model: str | None = None,
        max_steps: int = 10,
        **kwargs: Any,
    ) -> TaskResult:
        """Execute a task with a specific agent.

        Args:
            agent: Name of the registered agent to use.
            task: Task description for the agent.
            model: Optional model override (not used if agent config has a
                fixed provider_key).
            max_steps: Maximum execution steps (overrides agent config).
            **kwargs: Additional context passed to the task.

        Returns:
            TaskResult with output, cost, token, and duration information.

        Raises:
            ValueError: If the agent name is not registered.
        """
        config = self._agent_configs.get(agent)
        if config is None:
            raise ValueError(f"Unknown agent: '{agent}'. Available: {list(self._agent_configs)}")

        provider_key = config.provider_key
        if provider_key not in self._providers:
            raise ValueError(
                f"Provider '{provider_key}' for agent '{agent}' not registered. "
                f"Available: {list(self._providers)}"
            )

        # Override max_steps if provided
        effective_config = AgentConfig(
            name=config.name,
            role=config.role,
            provider_key=config.provider_key,
            tools=config.tools,
            max_steps=max_steps,
            max_retries_per_approach=config.max_retries_per_approach,
            timeout_seconds=config.timeout_seconds,
            escalation_provider_key=config.escalation_provider_key,
        )

        provider = self._providers[provider_key]
        escalation_provider = None
        if (
            effective_config.escalation_provider_key
            and effective_config.escalation_provider_key in self._providers
        ):
            escalation_provider = self._providers[effective_config.escalation_provider_key]

        agent_instance = Agent(
            config=effective_config,
            provider=provider,
            skill_registry=self._skill_registry,
            escalation_provider=escalation_provider,
        )

        start_time = time.monotonic()
        core_result: CoreTaskResult = await agent_instance.execute(
            Task(description=task, context=kwargs.get("context", {}))
        )
        duration = time.monotonic() - start_time

        return TaskResult(
            success=core_result.status == TaskStatus.COMPLETED,
            output=core_result.output,
            agent=agent,
            tokens_used=core_result.total_tokens,
            cost=core_result.total_cost_usd,
            duration_seconds=round(duration, 3),
            files_created=list(core_result.artifacts.get("files_created", [])),
            files_modified=list(core_result.artifacts.get("files_modified", [])),
        )

    async def run_team(
        self,
        task: str,
        agents: list[str] | None = None,
        max_steps: int = 30,
        **kwargs: Any,
    ) -> TeamResult:
        """Execute a task using the orchestrator's team coordination.

        If *agents* is provided, only those agents are included in the team.
        Otherwise all registered agents are used.

        Args:
            task: High-level task description.
            agents: Optional subset of agent names to include.
            max_steps: Maximum steps (reserved for future per-agent control).
            **kwargs: Additional context for the orchestrator.

        Returns:
            TeamResult with per-agent results and aggregate metrics.
        """
        # Filter agents if a subset is specified
        if agents is not None:
            unknown = set(agents) - set(self._agent_configs)
            if unknown:
                raise ValueError(
                    f"Unknown agents: {unknown}. Available: {list(self._agent_configs)}"
                )
            filtered_agents = {k: v for k, v in self._agent_configs.items() if k in agents}
        else:
            filtered_agents = dict(self._agent_configs)

        orchestrator = Orchestrator(
            config=self._orchestrator_config,
            agents=filtered_agents,
            providers=self._providers,
            skill_registry=self._skill_registry,
        )

        start_time = time.monotonic()
        orch_result: OrchestratorResult = await orchestrator.run(
            task, context=kwargs.get("context")
        )
        duration = time.monotonic() - start_time

        agent_results: list[TaskResult] = []
        for task_id, core_result in orch_result.agent_results.items():
            agent_results.append(
                TaskResult(
                    success=core_result.status == TaskStatus.COMPLETED,
                    output=core_result.output,
                    agent=task_id,
                    tokens_used=core_result.total_tokens,
                    cost=core_result.total_cost_usd,
                    duration_seconds=0.0,  # Individual durations not tracked by orchestrator
                )
            )

        return TeamResult(
            success=orch_result.success,
            summary=orch_result.output,
            agent_results=agent_results,
            total_tokens=orch_result.total_tokens,
            total_cost=orch_result.total_cost_usd,
            duration_seconds=round(duration, 3),
        )

    async def run_graph(
        self,
        graph_type: str,
        input_data: dict[str, Any],
        **kwargs: Any,
    ) -> GraphResult:
        """Execute a named graph template.

        Args:
            graph_type: Name of the template registered in the template store.
            input_data: Initial state for the graph.
            **kwargs: Additional options (e.g. thread_id).

        Returns:
            GraphResult with final state, step count, and success status.

        Raises:
            ValueError: If the graph template is not found.
        """
        template = self._template_store.get(graph_type)
        if template is None:
            raise ValueError(
                f"Unknown graph template: '{graph_type}'. "
                f"Available: {self._template_store.list_templates()}"
            )

        state_graph = self._template_store.build_graph(
            graph_type,
            providers=self._providers,
        )
        compiled = state_graph.compile()
        core_result: CoreGraphResult = await compiled.invoke(
            input_data,
            thread_id=kwargs.get("thread_id"),
        )

        return GraphResult(
            success=core_result.success,
            state=core_result.state,
            steps=len(core_result.steps),
            error=core_result.error,
        )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def list_agents(self) -> list[AgentInfo]:
        """Return metadata for all registered agents."""
        result: list[AgentInfo] = []
        for name, config in self._agent_configs.items():
            result.append(
                AgentInfo(
                    name=name,
                    description=config.role,
                    category=_infer_category(name),
                    model=config.provider_key,
                    skills=list(config.tools),
                )
            )
        return result

    def list_skills(self) -> list[SkillInfo]:
        """Return metadata for all registered skills."""
        result: list[SkillInfo] = []
        for skill_name in self._skill_registry.list_skills():
            skill = self._skill_registry.get(skill_name)
            if skill is not None:
                result.append(
                    SkillInfo(
                        name=skill.name,
                        description=skill.description,
                        parameters=skill.parameters,
                    )
                )
        return result

    # ------------------------------------------------------------------
    # Sync wrappers
    # ------------------------------------------------------------------

    def run_agent_sync(self, **kwargs: Any) -> TaskResult:
        """Synchronous wrapper for run_agent(). Creates a new event loop."""
        return asyncio.run(self.run_agent(**kwargs))

    def run_team_sync(self, **kwargs: Any) -> TeamResult:
        """Synchronous wrapper for run_team(). Creates a new event loop."""
        return asyncio.run(self.run_team(**kwargs))

    def run_graph_sync(self, **kwargs: Any) -> GraphResult:
        """Synchronous wrapper for run_graph(). Creates a new event loop."""
        return asyncio.run(self.run_graph(**kwargs))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Category keywords mapped to category names (mirrors dashboard routing logic)
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "tooling": [
        "skillkit",
    ],
    "finance": [
        "financial", "risk", "quant", "compliance", "accountant",
    ],
    "data-science": [
        "data-analyst", "ml-engineer", "data-engineer", "nlp", "bi-analyst",
    ],
    "marketing": [
        "content", "seo", "growth", "social-media", "email",
    ],
    "software-engineering": [
        "backend", "frontend", "devops", "platform", "ai-engineer",
        "scout", "research-scout", "security",
    ],
}


def _infer_category(agent_name: str) -> str:
    """Infer an agent's category from its name using keyword matching."""
    lower = agent_name.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return category
    if lower == "team-lead":
        return "orchestration"
    return "software-engineering"  # default fallback
