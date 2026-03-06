---
sidebar_position: 7
title: Migration from Claude
---

# Migration Guide: From Claude Code to Provider-Agnostic Orchestration

## What Claude Code Does Well (and what we keep)

1. **Agent specialization** — domain experts (backend, frontend, devops)
2. **Team-lead pattern** — one coordinator, many specialists
3. **Anti-stall protocol** — retry limits, step caps, timeout enforcement
4. **Skills as tools** — reusable capabilities agents can invoke
5. **Memory across sessions** — persistent context store
6. **Hooks/guards** — automated checks on agent actions

## What We Change

### Provider coupling → Provider interface

**Before (Claude Code)**:
```yaml
# .claude/agents/backend.md
name: backend
model: sonnet    # locked to Claude
```

**After (Orchestrator)**:
```yaml
# agents/backend.yaml
name: backend
provider: default          # resolved at runtime
min_capability: coding     # minimum model capability needed
```

### Markdown agents → Structured config

```
agents/
├── backend.yaml         # config: provider, tools, limits
├── backend.prompt.md    # system prompt (portable)
└── backend.tools.yaml   # tool/skill allowlist
```

### Claude-specific tools → Skill registry

```python
class FileReadSkill(Skill):
    name = "file_read"
    async def execute(self, params):
        return Path(params["file_path"]).read_text()
```

The orchestrator translates to each provider's tool format automatically.

### CLAUDE.md → project.yaml

```yaml
name: "My Project"
agents:
  team-lead:
    provider: claude-sonnet
    tools: [file_read, file_write, shell_exec, delegate]
routing:
  strategy: cost-optimized
  rules:
    - complexity: low → provider: gemini-flash
    - complexity: high → provider: claude-opus
```

## Migration Steps

1. Install the orchestrator
2. Convert agent `.md` files to `.yaml` + `.prompt.md` pairs
3. Replace `CLAUDE.md` with `project.yaml`
4. Configure providers (start with Anthropic, add others later)
5. Test with same provider to verify identical behavior
6. Gradually add providers — route simple tasks to cheaper models
7. Monitor costs with built-in tracking
