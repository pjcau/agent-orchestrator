"""Orchestrator — coordinates agents, routes tasks, manages lifecycle."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from .agent import Agent, AgentConfig, Task, TaskResult, TaskStatus
from .cooperation import (
    AgentMessage,
    Artifact,
    CooperationProtocol,
    TaskAssignment,
    TaskReport,
)
from .mcp_server import MCPResource, MCPServerRegistry
from .provider import Provider
from .router import RoutingStrategy, TaskComplexity
from .skill import SkillRegistry

logger = logging.getLogger(__name__)

__all__ = [
    "Orchestrator",
    "OrchestratorConfig",
    "OrchestratorResult",
    "RoutingStrategy",
    "TaskComplexity",
]


@dataclass
class OrchestratorConfig:
    routing_strategy: RoutingStrategy = RoutingStrategy.FIXED
    max_concurrent_agents: int = 3
    cost_budget_usd: float | None = None
    fallback_chain: list[str] = field(default_factory=list)
    enable_escalation: bool = True


@dataclass
class OrchestratorResult:
    success: bool
    output: str
    agent_results: dict[str, TaskResult] = field(default_factory=dict)
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    artifacts: dict[str, Any] = field(default_factory=dict)
    conflicts: list[dict] = field(default_factory=list)


# Callback type for progress events
ProgressCallback = Any  # Callable[[str, str, dict], Awaitable[None]] in practice


class Orchestrator:
    """Main coordinator that decomposes tasks and routes them to agents.

    Supports:
    - Team-lead task decomposition
    - Parallel agent execution via asyncio.gather
    - Dependency graph (topological ordering)
    - Shared context store (artifacts)
    - Cloud escalation on agent stall
    - Conflict detection and resolution
    - Progress callbacks for dashboard events
    """

    def __init__(
        self,
        config: OrchestratorConfig,
        agents: dict[str, AgentConfig],
        providers: dict[str, Provider],
        skill_registry: SkillRegistry,
        on_progress: ProgressCallback | None = None,
    ):
        self.config = config
        self.agent_configs = agents
        self.providers = providers
        self.skills = skill_registry
        self.protocol = CooperationProtocol()
        self._cost_tracker: float = 0.0
        self._on_progress = on_progress

    async def _emit(self, event: str, agent: str = "", data: dict | None = None) -> None:
        if self._on_progress:
            await self._on_progress(event, agent, data or {})

    async def run(
        self, task_description: str, context: dict[str, Any] | None = None
    ) -> OrchestratorResult:
        """Execute a high-level task by decomposing and delegating to agents."""
        await self._emit("orchestrator.start", data={"task": task_description})

        team_lead_config = self.agent_configs.get("team-lead")
        if not team_lead_config:
            result = await self._run_single_agent(task_description, context)
            await self._emit("orchestrator.end", data={"success": result.success})
            return result

        # Phase 1: Team-lead decomposes the task
        await self._emit("decomposition.start", agent="team-lead")
        team_lead = self._create_agent(team_lead_config)
        decomposition = await team_lead.execute(
            Task(
                description=f"Decompose this task into sub-tasks for the available agents "
                f"({', '.join(a for a in self.agent_configs if a != 'team-lead')}). "
                f"For each sub-task, specify: agent name, description, and dependencies "
                f"(which other sub-tasks must complete first).\n\nTask: {task_description}",
                context=context or {},
            )
        )
        await self._emit(
            "decomposition.end",
            agent="team-lead",
            data={
                "status": decomposition.status.value,
                "steps": decomposition.steps_taken,
            },
        )

        if decomposition.status != TaskStatus.COMPLETED:
            result = OrchestratorResult(
                success=False,
                output=f"Team lead failed to decompose task: {decomposition.error}",
                total_cost_usd=decomposition.total_cost_usd,
            )
            await self._emit("orchestrator.end", data={"success": False})
            return result

        # Phase 2: Execute sub-tasks with parallel batches
        agent_results: dict[str, TaskResult] = {"team-lead-decompose": decomposition}
        self._cost_tracker = decomposition.total_cost_usd

        while True:
            batches = self.protocol.get_parallel_batches()
            if not batches and self.protocol.all_complete():
                break
            if not batches:
                # Deadlock: pending tasks but none are ready
                conflicts = self.protocol.store.get_conflicts(unresolved_only=True)
                result = OrchestratorResult(
                    success=False,
                    output="Deadlock detected: pending tasks have unresolvable dependencies",
                    agent_results=agent_results,
                    total_cost_usd=self._cost_tracker,
                    conflicts=[{"resource": c.resource, "agents": c.agents} for c in conflicts],
                )
                await self._emit("orchestrator.end", data={"success": False, "deadlock": True})
                return result

            for batch in batches:
                # Limit concurrency
                batch = batch[: self.config.max_concurrent_agents]

                # Mark all as running
                for assignment in batch:
                    self.protocol.mark_running(assignment.task_id)

                # Execute batch in parallel
                await self._emit(
                    "batch.start",
                    data={
                        "tasks": [a.task_id for a in batch],
                        "agents": [a.to_agent for a in batch],
                    },
                )

                results = await asyncio.gather(
                    *(self._execute_assignment(a) for a in batch),
                    return_exceptions=True,
                )

                for assignment, result in zip(batch, results):
                    if isinstance(result, Exception):
                        task_result = TaskResult(
                            status=TaskStatus.FAILED,
                            output=str(result),
                            error=str(result),
                        )
                    else:
                        task_result = result

                    agent_results[assignment.task_id] = task_result

                    # Publish agent output as artifact
                    if task_result.status == TaskStatus.COMPLETED:
                        self.protocol.store.publish(
                            Artifact(
                                name=f"result:{assignment.task_id}",
                                type="output",
                                content=task_result.output,
                                produced_by=assignment.to_agent,
                            )
                        )

                    self.protocol.complete(
                        TaskReport(
                            task_id=assignment.task_id,
                            agent_name=assignment.to_agent,
                            success=task_result.status == TaskStatus.COMPLETED,
                            output=task_result.output,
                            artifacts=task_result.artifacts,
                            cost_usd=task_result.total_cost_usd,
                        )
                    )

                    self._cost_tracker += task_result.total_cost_usd

                    await self._emit(
                        "task.complete",
                        agent=assignment.to_agent,
                        data={
                            "task_id": assignment.task_id,
                            "status": task_result.status.value,
                            "cost_usd": task_result.total_cost_usd,
                            "tokens": task_result.total_tokens,
                            "escalated": task_result.escalated,
                        },
                    )

                    # Budget check
                    if (
                        self.config.cost_budget_usd
                        and self._cost_tracker > self.config.cost_budget_usd
                    ):
                        result = OrchestratorResult(
                            success=False,
                            output="Cost budget exceeded",
                            agent_results=agent_results,
                            total_cost_usd=self._cost_tracker,
                        )
                        await self._emit(
                            "orchestrator.end", data={"success": False, "reason": "budget_exceeded"}
                        )
                        return result

                await self._emit(
                    "batch.end",
                    data={
                        "tasks": [a.task_id for a in batch],
                    },
                )

        # Phase 3: Collect results and check for conflicts
        total_cost = sum(r.total_cost_usd for r in agent_results.values())
        total_tokens = sum(r.total_tokens for r in agent_results.values())
        conflicts = self.protocol.store.get_conflicts()
        all_artifacts = self.protocol.store.get_all_artifacts()

        result = OrchestratorResult(
            success=all(r.status == TaskStatus.COMPLETED for r in agent_results.values()),
            output=self._summarize_results(agent_results),
            agent_results=agent_results,
            total_cost_usd=total_cost,
            total_tokens=total_tokens,
            artifacts={k: a.content for k, a in all_artifacts.items()},
            conflicts=[
                {"resource": c.resource, "agents": c.agents, "resolved": c.resolved}
                for c in conflicts
            ],
        )
        await self._emit(
            "orchestrator.end",
            data={
                "success": result.success,
                "total_cost_usd": total_cost,
                "total_tokens": total_tokens,
                "conflict_count": len(conflicts),
            },
        )
        return result

    def resolve_provider(
        self, provider_key: str, complexity: TaskComplexity | None = None
    ) -> Provider:
        """Resolve a provider key to an actual provider, applying routing strategy."""
        if self.config.routing_strategy == RoutingStrategy.FIXED:
            return self.providers[provider_key]

        if self.config.routing_strategy == RoutingStrategy.COST_OPTIMIZED and complexity:
            sorted_providers = sorted(
                self.providers.values(),
                key=lambda p: p.output_cost_per_million,
            )
            if complexity.level == "low":
                return sorted_providers[0]
            elif complexity.level == "high":
                return sorted_providers[-1]
            else:
                return sorted_providers[len(sorted_providers) // 2]

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

        await self._emit(
            "agent.start",
            agent=assignment.to_agent,
            data={
                "task_id": assignment.task_id,
                "description": assignment.description[:200],
            },
        )

        # Inject artifacts from completed dependencies into task context
        dep_context = dict(assignment.context)
        for dep_id in assignment.depends_on:
            artifact = self.protocol.store.get_artifact(f"result:{dep_id}")
            if artifact:
                dep_context[f"dep:{dep_id}"] = artifact.content

        agent = self._create_agent(config)

        # Send inter-agent message about task start
        self.protocol.store.send_message(
            AgentMessage(
                from_agent="orchestrator",
                to_agent=assignment.to_agent,
                content=f"Starting task: {assignment.description}",
                message_type="info",
                related_task_id=assignment.task_id,
            )
        )

        result = await agent.execute(
            Task(
                description=assignment.description,
                context=dep_context,
                parent_task_id=assignment.task_id,
            )
        )

        # Send completion message
        self.protocol.store.send_message(
            AgentMessage(
                from_agent=assignment.to_agent,
                to_agent="orchestrator",
                content=f"Task {'completed' if result.status == TaskStatus.COMPLETED else 'failed'}: {result.output[:200]}",
                message_type="response",
                related_task_id=assignment.task_id,
            )
        )

        return result

    def _create_agent(self, config: AgentConfig) -> Agent:
        provider = self.resolve_provider(config.provider_key)
        escalation_provider = None
        if (
            self.config.enable_escalation
            and config.escalation_provider_key
            and config.escalation_provider_key in self.providers
        ):
            escalation_provider = self.providers[config.escalation_provider_key]
        return Agent(
            config=config,
            provider=provider,
            skill_registry=self.skills,
            escalation_provider=escalation_provider,
        )

    # ------------------------------------------------------------------
    # MCP integration
    # ------------------------------------------------------------------

    def register_mcp_tools(
        self,
        server: MCPServerRegistry | None = None,
    ) -> MCPServerRegistry:
        """Register all agents and skills as MCP tools on *server*.

        If *server* is ``None`` a fresh ``MCPServerRegistry`` is created.
        The registry is also stored on ``self.mcp`` for later access.

        Returns the populated ``MCPServerRegistry``.
        """
        if server is None:
            server = MCPServerRegistry()

        # Register agents as MCP tools
        agent_configs: dict[str, Any] = {}
        for name, cfg in self.agent_configs.items():
            agent_configs[name] = {"role": cfg.role} if isinstance(cfg, AgentConfig) else cfg
        server.register_agent_tools(agent_configs)

        # Register skills as MCP tools
        skill_names = self.skills.list_skills()
        server.register_skill_tools(skill_names, self.skills)

        # Expose orchestrator itself as a resource
        server.register_resource(
            MCPResource(
                uri="orchestrator://status",
                name="orchestrator_status",
                description="Current orchestrator configuration and agent list",
            )
        )

        self.mcp = server
        return server

    def _summarize_results(self, results: dict[str, TaskResult]) -> str:
        lines = []
        for task_id, result in results.items():
            status = "OK" if result.status == TaskStatus.COMPLETED else result.status.value.upper()
            provider_info = f" [{result.provider_used}]" if result.provider_used else ""
            escalated = " (escalated)" if result.escalated else ""
            lines.append(f"[{status}]{provider_info}{escalated} {task_id}: {result.output[:200]}")
        return "\n".join(lines)
