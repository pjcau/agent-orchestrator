# Agent Orchestrator

## Language

All code, comments, commit messages, documentation, and any written content in this project MUST be in **English**.

## Overview

Provider-agnostic AI agent orchestration framework. Abstracts the concepts of skill, agent, subagent, and inter-agent cooperation away from any single LLM vendor (Claude, GPT, Gemini, Llama, Mistral, etc.).

## Project Structure

```
agent-orchestrator/
├── docs/
│   ├── architecture.md          # Core abstractions & patterns
│   ├── cost-analysis.md         # Provider comparison & cost modeling
│   ├── infrastructure.md        # Cloud vs on-prem decision framework
│   └── migration-from-claude.md # How to abstract away from Claude Code
├── src/
│   └── agent_orchestrator/
│       ├── core/
│       │   ├── provider.py      # LLM provider abstraction (interface)
│       │   ├── agent.py         # Agent base class
│       │   ├── skill.py         # Skill registry & execution
│       │   ├── orchestrator.py  # Main orchestrator (coordination)
│       │   └── cooperation.py   # Inter-agent communication protocols
│       ├── providers/
│       │   ├── anthropic.py     # Claude provider
│       │   ├── openai.py        # GPT provider
│       │   ├── google.py        # Gemini provider
│       │   └── local.py         # Local models (Ollama, vLLM)
│       └── skills/
│           ├── filesystem.py    # File read/write/search
│           ├── shell.py         # Shell command execution
│           └── testing.py       # Test runner skill
├── tests/
├── pyproject.toml
└── README.md
```

## Key Abstractions

- **Provider** — LLM backend (Claude, GPT, Gemini, local). Swappable per agent.
- **Agent** — Autonomous unit with a role, tools, and a provider. Stateless between tasks.
- **Skill** — Reusable capability (test-runner, linter, deployer). Provider-independent.
- **Orchestrator** — Coordinates agents, task decomposition, anti-stall enforcement.
- **Cooperation** — Inter-agent messaging: delegation, results, conflict resolution.

## Agents

```
team-lead (sonnet) ---- orchestrator
  ├── backend (sonnet)
  ├── frontend (sonnet)
  └── devops (sonnet)
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Hooks (auto-guards)

| Trigger | Matcher | Action |
|---------|---------|--------|
| PostToolUse | `Edit` (project source files) | Reminds to run tests |

Config: `.claude/settings.json` · Scripts: `.claude/hooks/`
