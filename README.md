# Agent Orchestrator

Provider-agnostic AI agent orchestration framework. Swap LLM providers per agent, route tasks by complexity, mix cloud and local models.

## Why

Current agent tools (Claude Code, Cursor, Copilot) lock you into one provider. This framework:

- **Abstracts the provider** — same agent runs on Claude, GPT, Gemini, or local Llama
- **Routes by cost** — simple tasks go to cheap models, complex ones to frontier
- **Mixes cloud + local** — sensitive code stays on your hardware
- **Built-in anti-stall** — retry caps, timeouts, deadlock detection

## Quick Start

```bash
pip install -e ".[all]"
```

```python
from agent_orchestrator.core import Agent, AgentConfig, Orchestrator, SkillRegistry
from agent_orchestrator.providers.anthropic import AnthropicProvider
from agent_orchestrator.skills import FileReadSkill, ShellExecSkill

# Setup
registry = SkillRegistry()
registry.register(FileReadSkill())
registry.register(ShellExecSkill())

provider = AnthropicProvider(model="claude-sonnet-4-6")

config = AgentConfig(
    name="backend",
    role="You are a backend developer.",
    provider_key="claude-sonnet",
    tools=["file_read", "shell_exec"],
)

agent = Agent(config=config, provider=provider, skill_registry=registry)
result = await agent.execute(Task(description="List all Python files in src/"))
```

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full design.

### Core Abstractions

| Concept | Description |
|---------|-------------|
| **Provider** | LLM backend (Claude, GPT, Gemini, local). Swappable. |
| **Agent** | Autonomous unit with role, tools, provider. Stateless. |
| **Skill** | Reusable capability. Provider-independent. |
| **Orchestrator** | Coordinates agents, routes tasks, enforces anti-stall. |
| **Cooperation** | Inter-agent delegation, artifact sharing, conflict resolution. |

### Routing Strategies

- **Fixed** — each agent always uses one provider
- **Cost-optimized** — cheap models for simple tasks, expensive for complex
- **Capability-based** — match task needs to model strengths
- **Fallback chain** — try provider A, fall back to B on failure

## Documentation

- [Architecture](docs/architecture.md) — core design and abstractions
- [Cost Analysis](docs/cost-analysis.md) — provider comparison, cost modeling, break-even analysis
- [Infrastructure](docs/infrastructure.md) — cloud vs physical machines decision framework
- [Migration from Claude](docs/migration-from-claude.md) — how to abstract away from Claude Code

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Status

v0.1.0 — Foundation. Core abstractions, 4 provider implementations, basic skills, tests passing.
