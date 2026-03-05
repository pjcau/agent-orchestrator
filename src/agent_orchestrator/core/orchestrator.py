"""Orchestrator — coordinates agents, routes tasks, manages lifecycle."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .agent import Agent, AgentConfig, Task, TaskResult, TaskStatus
from .cooperation import CooperationProtocol, Priority, TaskAssignment, TaskReport
from .provider import Provider
from .skill import SkillRegistry


class RoutingStrategy(str, Enum):
    FIXED = "fixed"
    COST_OPTIMIZED = "cost_optimized"
    CAPABILITY_BASED = "capability_based"
    FALLBACK_CHAIN = "fallback_chain"


@dataclass
class TaskComplexity:
    level: str  # "low", "medium", "high"
    estimated_tokens: int = 2000
    requires_tools: bool = True
    requires_reasoning: bool = False


@dataclass
class OrchestratorConfig:
    routing_strategy: RoutingStrategy = RoutingStrategy.FIXED
    max_concurrent_agents: int = 3
    cost_budget_usd: float | None = None
    fallback_chain: list[str] = field(default_factory=list)


@dataclass
class OrchestratorResult:
    success: bool
    output: str
    agent_results: dict[str, TaskResult] = field(default_factory=dict)
    total_cost_usd: float = 0.0
    total_tokens: int = 0


class Orchestrator:
    """Main coordinator that decomposes tasks and routes them to agents."""

    def __init__(
        self,
        config: OrchestratorConfig,
        agents: dict[str, AgentConfig],
        providers: dict[str, Provider],
        skill_registry: SkillRegistry,
    ):
        self.config = config
        self.agent_configs = agents
        self.providers = providers
        self.skills = skill_registry
        self.protocol = CooperationProtocol()
        self._cost_tracker: float = 0.0

    async def run(self, task_description: str, context: dict[str, Any] | None = None) -> OrchestratorResult:
        """Execute a high-level task by decomposing and delegating to agents."""
        # Use team-lead to decompose the task
        team_lead_config = self.agent_configs.get("team-lead")
        if not team_lead_config:
            # No team lead — run directly with first available agent
            return await self._run_single_agent(task_description, context)

        team_lead = self._create_agent(team_lead_config)
        decomposition = await team_lead.execute(
            Task(
                description=f"Decompose this task into sub-tasks for the available agents "
                f"({', '.join(self.agent_configs.keys())}): {task_description}",
                context=context or {},
            )
        )

        if decomposition.status != TaskStatus.COMPLETED:
            return OrchestratorResult(
                success=False,
                output=f"Team lead failed to decompose task: {decomposition.error}",
                total_cost_usd=decomposition.total_cost_usd,
            )

        # Execute sub-tasks (respecting dependency order)
        agent_results: dict[str, TaskResult] = {"team-lead-decompose": decomposition}
        ready_tasks = self.protocol.get_ready_tasks()

        while ready_tasks or not self.protocol.all_complete():
            for assignment in ready_tasks[:self.config.max_concurrent_agents]:
                result = await self._execute_assignment(assignment)
                agent_results[assignment.task_id] = result

                self.protocol.complete(
                    TaskReport(
                        task_id=assignment.task_id,
                        agent_name=assignment.to_agent,
                        success=result.status == TaskStatus.COMPLETED,
                        output=result.output,
                        cost_usd=result.total_cost_usd,
                    )
                )

                self._cost_tracker += result.total_cost_usd
                if self.config.cost_budget_usd and self._cost_tracker > self.config.cost_budget_usd:
                    return OrchestratorResult(
                        success=False,
                        output="Cost budget exceeded",
                        agent_results=agent_results,
                        total_cost_usd=self._cost_tracker,
                    )

            ready_tasks = self.protocol.get_ready_tasks()
            if not ready_tasks and not self.protocol.all_complete():
                # Deadlock detection
                return OrchestratorResult(
                    success=False,
                    output="Deadlock detected: pending tasks have unresolvable dependencies",
                    agent_results=agent_results,
                    total_cost_usd=self._cost_tracker,
                )

        total_cost = sum(r.total_cost_usd for r in agent_results.values())
        total_tokens = sum(r.total_tokens for r in agent_results.values())

        return OrchestratorResult(
            success=all(r.status == TaskStatus.COMPLETED for r in agent_results.values()),
            output=self._summarize_results(agent_results),
            agent_results=agent_results,
            total_cost_usd=total_cost,
            total_tokens=total_tokens,
        )

    def resolve_provider(self, provider_key: str, complexity: TaskComplexity | None = None) -> Provider:
        """Resolve a provider key to an actual provider, applying routing strategy."""
        if self.config.routing_strategy == RoutingStrategy.FIXED:
            return self.providers[provider_key]

        if self.config.routing_strategy == RoutingStrategy.COST_OPTIMIZED and complexity:
            # Route based on task complexity
            sorted_providers = sorted(
                self.providers.values(),
                key=lambda p: p.output_cost_per_million,
            )
            if complexity.level == "low":
                return sorted_providers[0]  # cheapest
            elif complexity.level == "high":
                return sorted_providers[-1]  # most expensive (presumably best)
            else:
                return sorted_providers[len(sorted_providers) // 2]  # middle

        if self.config.routing_strategy == RoutingStrategy.FALLBACK_CHAIN:
            for key in self.config.fallback_chain:
                if key in self.providers:
                    return self.providers[key]

        return self.providers[provider_key]

    async def _run_single_agent(
        self, task_description: str, context: dict[str, Any] | None
    ) -> OrchestratorResult:
        config = next(iter(self.agent_configs.values()))
        agent = self._create_agent(config)
        result = await agent.execute(Task(description=task_description, context=context or {}))
        return OrchestratorResult(
            success=result.status == TaskStatus.COMPLETED,
            output=result.output,
            agent_results={"default": result},
            total_cost_usd=result.total_cost_usd,
            total_tokens=result.total_tokens,
        )

    async def _execute_assignment(self, assignment: TaskAssignment) -> TaskResult:
        config = self.agent_configs.get(assignment.to_agent)
        if not config:
            return TaskResult(
                status=TaskStatus.FAILED,
                output=f"Unknown agent: {assignment.to_agent}",
                error=f"No agent config for '{assignment.to_agent}'",
            )
        agent = self._create_agent(config)
        return await agent.execute(
            Task(description=assignment.description, context=assignment.context)
        )

    def _create_agent(self, config: AgentConfig) -> Agent:
        provider = self.resolve_provider(config.provider_key)
        return Agent(config=config, provider=provider, skill_registry=self.skills)

    def _summarize_results(self, results: dict[str, TaskResult]) -> str:
        lines = []
        for task_id, result in results.items():
            status = "OK" if result.status == TaskStatus.COMPLETED else result.status.value.upper()
            lines.append(f"[{status}] {task_id}: {result.output[:200]}")
        return "\n".join(lines)
