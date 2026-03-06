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

## Skills Map (Agent Assignments)

| Skill | Agent | Description |
|-------|-------|-------------|
| `/docker-build` | devops | Build and manage containers |
| `/test-runner` | all | Run pytest suite via Docker |
| `/lint-check` | all | Ruff linting and formatting |
| `/code-review` | all | Automated quality/security review |
| `/deploy` | devops | Container deployment |
| `/scout` | scout | GitHub pattern discovery |
| `/website-dev` | frontend | Documentation site development |

## Skill Registry

```python
registry = SkillRegistry()
registry.register(FileReadSkill())
registry.register(ShellExecSkill(timeout=120))

# Export as tool definitions for any LLM
tools = registry.to_tool_definitions()
```
