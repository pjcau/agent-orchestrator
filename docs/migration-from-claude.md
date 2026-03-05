# Migration Guide: From Claude Code to Provider-Agnostic Orchestration

## What Claude Code Does Well (and what we keep)

Claude Code established solid patterns that this framework preserves:

1. **Agent specialization** — domain experts (backend, frontend, devops)
2. **Team-lead pattern** — one coordinator, many specialists
3. **Anti-stall protocol** — retry limits, step caps, timeout enforcement
4. **Skills as tools** — reusable capabilities agents can invoke
5. **Memory across sessions** — persistent context store
6. **Hooks/guards** — automated checks on agent actions

## What We Change

### 1. Provider coupling → Provider interface

**Before (Claude Code)**:
```yaml
# .claude/agents/backend.md
---
name: backend
model: sonnet    # <-- locked to Claude
---
```

**After (Orchestrator)**:
```yaml
# agents/backend.yaml
name: backend
provider: default          # resolved at runtime
provider_override: null    # can force a specific one
min_capability: coding     # minimum model capability needed
```

The orchestrator resolves `default` to the configured provider for this capability level.

### 2. Markdown agents → Structured config + prompt

**Before**: Everything in one `.md` file (frontmatter + prompt + rules)

**After**: Separate concerns:
```
agents/
├── backend.yaml         # config: provider, tools, limits
├── backend.prompt.md    # system prompt (portable across providers)
└── backend.tools.yaml   # tool/skill allowlist
```

This separation lets you change provider without touching the prompt, or change the prompt without touching the config.

### 3. Claude-specific tools → Skill registry

**Before**: Agents use Claude's built-in tools (Read, Write, Bash, etc.)

**After**: Skills are defined once in a registry and adapted per provider:

```python
# The skill definition is provider-agnostic
class FileReadSkill(Skill):
    name = "file_read"
    description = "Read a file from the filesystem"
    parameters = {"file_path": {"type": "string", "required": True}}

    async def execute(self, params):
        return Path(params["file_path"]).read_text()
```

The orchestrator translates this to each provider's tool format:
- Anthropic: `{"name": "file_read", "input_schema": ...}`
- OpenAI: `{"type": "function", "function": {"name": "file_read", ...}}`
- Local: Same as OpenAI (most follow OpenAI's tool format)

### 4. CLAUDE.md → project.yaml

**Before**: `CLAUDE.md` is the project instruction file, Claude-namespaced.

**After**: `project.yaml` — provider-neutral project configuration:

```yaml
name: "My Project"
description: "..."

agents:
  team-lead:
    provider: claude-sonnet
    prompt: agents/team-lead.prompt.md
    tools: [file_read, file_write, shell_exec, delegate]

  backend:
    provider: default
    prompt: agents/backend.prompt.md
    tools: [file_read, file_write, shell_exec, test_runner]

routing:
  strategy: cost-optimized
  rules:
    - complexity: low → provider: gemini-flash
    - complexity: medium → provider: claude-sonnet
    - complexity: high → provider: claude-opus

providers:
  claude-sonnet:
    type: anthropic
    model: claude-sonnet-4-6
  claude-opus:
    type: anthropic
    model: claude-opus-4-6
  gpt-4o:
    type: openai
    model: gpt-4o
  gemini-flash:
    type: google
    model: gemini-2.0-flash
  local-llama:
    type: ollama
    model: llama3.3:70b
    base_url: http://gpu-server:11434
```

### 5. Memory files → Context store

**Before**: `MEMORY.md` in `~/.claude/projects/`

**After**: Structured context store (still file-based, but with schema):

```yaml
# .orchestrator/memory/session.yaml
last_updated: 2026-03-05
decisions:
  - date: 2026-03-05
    decision: "Use PostgreSQL for persistence"
    context: "Need JSONB support for flexible schemas"

known_issues:
  - id: 1
    status: open
    description: "Auth middleware doesn't handle token refresh"

patterns:
  - "All API responses use {data, error, meta} envelope"
  - "Tests use pytest with async fixtures"
```

## Migration Steps

1. **Install the orchestrator** in your project
2. **Convert agent `.md` files** to `.yaml` + `.prompt.md` pairs
3. **Replace `CLAUDE.md`** with `project.yaml`
4. **Configure providers** (start with just Anthropic, add others later)
5. **Move memory** from `MEMORY.md` to structured `session.yaml`
6. **Test with same provider** (Claude) to verify behavior is identical
7. **Gradually add providers** — route simple tasks to cheaper models
8. **Monitor costs** — use built-in cost tracking to measure savings

## Compatibility

The orchestrator can run alongside Claude Code during migration. You don't need to switch all at once:

- Keep using Claude Code for interactive development
- Use the orchestrator for CI/CD, batch tasks, or specific workflows
- Gradually shift more work to the orchestrator as it proves itself
