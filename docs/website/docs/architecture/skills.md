---
sidebar_position: 4
title: Skills
---

# Skills

A skill is a reusable, provider-independent capability that agents can invoke.

```python
class Skill(ABC):
    @property
    def name(self) -> str: ...
    @property
    def description(self) -> str: ...
    @property
    def parameters(self) -> dict: ...  # JSON Schema

    async def execute(self, params: dict) -> SkillResult: ...
```

Skills map directly to "tools" in LLM APIs but are defined once and work across all providers.

## Built-in Skills

| Skill | Description |
|-------|-------------|
| `file_read` | Read file contents |
| `file_write` | Write content to file |
| `glob_search` | Search files by pattern |
| `shell_exec` | Execute shell commands |
| `sandboxed_shell` | Execute commands in Docker sandbox |
| `web_read` | Fetch web page content |
| `doc_sync` | Documentation sync checker |
| `github` | GitHub integration via gh CLI |
| `webhook` | Outgoing webhook skill |
| `load_skill` | On-demand full skill instruction loading |
| `ask_clarification` | Request human clarification (blocking/non-blocking) |

## Skills Map (Agent Assignments)

| Skill | Agent | Description |
|-------|-------|-------------|
| `/docker-build` | devops | Build and manage containers via OrbStack |
| `/test-runner` | all | Run pytest suite via Docker |
| `/lint-check` | all | Ruff linting and formatting checks |
| `/code-review` | all | Automated quality/security review |
| `/deploy` | devops | Container deployment via docker-compose |
| `/scout` | scout | GitHub pattern discovery |
| `/website-dev` | frontend | Documentation site development |
| `/verify` | all | Pre-PR quality gate (tests, lint, format, security, diff review) |
| `/cost-optimization` | ai-engineer | Review LLM API costs, routing, budget, retry efficiency |
| `/ship` | all | Full pipeline: test, lint, docs sync, commit, push |
| `/feature` | all | End-to-end feature dev: implement, tests, SOLID review, docs, commit |
| `/fix` | all | Bug fix with mandatory regression tests, lint, deploy |
| `/doc` | all | Full docs review: audit all docs/ against codebase |
| `/fetch-star-repos` | scout | Fetch GitHub starred repos for research scout |
| `/research-scout` | research-scout | Analyze starred repos and propose code improvements |
| `/web-research` | all | Search the internet for solutions, docs, and best practices |

## Skill Registry

```python
registry = SkillRegistry()
registry.register(FileReadSkill())
registry.register(ShellExecSkill(timeout=120))

# Export as tool definitions for any LLM
tools = registry.to_tool_definitions()
```
