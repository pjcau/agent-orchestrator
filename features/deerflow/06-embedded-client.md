# Feature: Embedded Client (OrchestratorClient)

## Context

From DeerFlow analysis (analysis/deepflow/25-embedded-client.md, 29-learnings.md L12).
DeerFlow's `DeerFlowClient` enables Python scripts, Jupyter notebooks, and CLI tools to use agents directly without HTTP. This opens new use cases and enables easier testing.

## What to Build

Create `src/agent_orchestrator/client.py` — a programmatic Python client that wraps the orchestrator core:

### API

```python
from agent_orchestrator.client import OrchestratorClient

# Initialize
client = OrchestratorClient(
    config_path="config.json",      # Optional: load config
    providers={"local": ollama},     # Optional: pre-configured providers
)

# Run a single agent
result = await client.run_agent(
    agent="backend",
    task="Add pagination to the /api/jobs endpoint",
    model="qwen2.5-coder",
)
print(result.output)
print(result.tokens_used, result.cost)

# Run a team
result = await client.run_team(
    task="Build a REST API for user management",
    agents=["backend", "frontend"],   # Optional: auto-select if omitted
    max_steps=30,
)
for agent_result in result.agent_results:
    print(f"{agent_result.agent}: {agent_result.output[:200]}")

# Run a graph
result = await client.run_graph(
    graph_type="review",
    input_data={"code": open("app.py").read()},
)

# List available agents/skills
agents = client.list_agents()
skills = client.list_skills()

# Sync API (for scripts/notebooks)
result = client.run_agent_sync(agent="backend", task="...")
```

### Implementation

```python
class OrchestratorClient:
    """Programmatic access to the agent orchestrator. No HTTP, no server required."""

    def __init__(self, config_path: str | None = None, providers: dict | None = None):
        self._orchestrator = Orchestrator()
        self._skill_registry = SkillRegistry()
        # Load config, register providers, setup agents
        ...

    async def run_agent(self, agent: str, task: str, model: str | None = None,
                        max_steps: int = 10, **kwargs) -> TaskResult:
        """Run a single agent on a task."""
        ...

    async def run_team(self, task: str, agents: list[str] | None = None,
                       max_steps: int = 30, **kwargs) -> TeamResult:
        """Run a multi-agent team on a task."""
        ...

    async def run_graph(self, graph_type: str, input_data: dict, **kwargs) -> GraphResult:
        """Execute a graph workflow."""
        ...

    def list_agents(self) -> list[AgentInfo]:
        """List available agents with their descriptions and categories."""
        ...

    def list_skills(self) -> list[SkillInfo]:
        """List available skills."""
        ...

    # Sync wrappers
    def run_agent_sync(self, **kwargs) -> TaskResult:
        return asyncio.run(self.run_agent(**kwargs))

    def run_team_sync(self, **kwargs) -> TeamResult:
        return asyncio.run(self.run_team(**kwargs))
```

### Result Types

```python
@dataclass
class TaskResult:
    success: bool
    output: str
    agent: str
    tokens_used: int
    cost: float
    duration_seconds: float
    files_created: list[str]
    files_modified: list[str]

@dataclass
class TeamResult:
    success: bool
    summary: str
    agent_results: list[TaskResult]
    total_tokens: int
    total_cost: float
    duration_seconds: float

@dataclass
class AgentInfo:
    name: str
    description: str
    category: str
    model: str
    skills: list[str]
```

## Files to Modify

- **Create**: `src/agent_orchestrator/client.py`
- **Modify**: `pyproject.toml` (export client in package)

## Tests

- Test client initialization with default config
- Test client initialization with custom providers
- Test run_agent returns TaskResult
- Test run_team returns TeamResult with agent_results
- Test list_agents returns all registered agents
- Test list_skills returns all registered skills
- Test sync wrappers work
- Test error handling (unknown agent, provider failure)

## Acceptance Criteria

- [ ] OrchestratorClient class with async + sync APIs
- [ ] run_agent, run_team, run_graph methods
- [ ] list_agents, list_skills discovery methods
- [ ] Typed result dataclasses (TaskResult, TeamResult)
- [ ] No HTTP dependency — direct Python API
- [ ] All tests pass
- [ ] Existing tests still pass
