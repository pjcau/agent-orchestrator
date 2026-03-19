# Feature: Progressive Skill Loading

## Context

From DeerFlow analysis (analysis/deepflow/09-skills-system.md, 29-learnings.md L2).
Currently all skill instructions are loaded into the system prompt (~20K tokens). DeerFlow lists skills by name/description only and lets the agent load full instructions on-demand via a `load_skill` tool. This reduces base prompt from ~20K to ~4K tokens.

## What to Build

### 1. Skill Summary Registry

Create a compact skill index that fits in the system prompt:

```python
# In src/agent_orchestrator/core/skill.py

class SkillSummary:
    """Lightweight skill descriptor for system prompt injection."""
    name: str           # e.g., "file_read"
    description: str    # e.g., "Read file contents by path"
    category: str       # e.g., "filesystem", "analysis", "devops"

class SkillRegistry:
    def get_summaries(self) -> list[SkillSummary]:
        """Return compact summaries for system prompt (name + 1-line description only)."""
        ...

    def get_full_instructions(self, skill_name: str) -> str | None:
        """Return full skill instructions (SKILL.md content) for on-demand loading."""
        ...
```

### 2. load_skill Tool

Create a new built-in skill that agents can call to load full instructions:

```python
# In src/agent_orchestrator/skills/skill_loader.py

class SkillLoaderSkill(Skill):
    """Meta-skill: loads full instructions for another skill on demand."""

    @property
    def name(self) -> str:
        return "load_skill"

    @property
    def parameters(self) -> dict:
        return {
            "skill_name": {"type": "string", "description": "Name of the skill to load instructions for"},
        }

    async def execute(self, params: dict) -> SkillResult:
        instructions = self.registry.get_full_instructions(params["skill_name"])
        if instructions is None:
            return SkillResult(success=False, output=f"Unknown skill: {params['skill_name']}")
        return SkillResult(success=True, output=instructions)
```

### 3. System Prompt Changes

Modify system prompt construction to use compact summaries instead of full instructions:

**Before** (~20K tokens):
```
You have these tools:
- file_read: [500 words of detailed instructions]
- file_write: [500 words of detailed instructions]
- shell_exec: [500 words of detailed instructions]
...
```

**After** (~4K tokens):
```
Available skills (call load_skill to get full instructions before first use):
- file_read: Read file contents by path
- file_write: Write or create files
- shell_exec: Execute shell commands
- web_read: Fetch web page content
- glob_search: Search files by pattern
...

Call load_skill(skill_name) to read detailed instructions before using an unfamiliar skill.
```

### 4. Token Tracking

Track token savings:
- Log base system prompt size (tokens) before and after
- Track `load_skill` invocations per session
- Add metric: `skill_loads_total` counter

## Files to Modify

- **Create**: `src/agent_orchestrator/skills/skill_loader.py`
- **Modify**: `src/agent_orchestrator/core/skill.py` (add SkillSummary, get_summaries, get_full_instructions)
- **Modify**: `src/agent_orchestrator/dashboard/agent_runner.py` (change system prompt construction)
- **Modify**: `src/agent_orchestrator/core/metrics.py` (add skill_loads_total counter)

## Tests

- Test get_summaries returns compact list
- Test get_full_instructions returns full content for valid skill
- Test get_full_instructions returns None for unknown skill
- Test load_skill tool returns instructions
- Test load_skill tool returns error for unknown skill
- Test system prompt is under 5K tokens with summaries
- Test agents can still use tools after loading instructions
- Test metric increments on each load

## Acceptance Criteria

- [ ] SkillSummary class and registry methods
- [ ] load_skill tool implemented and registered
- [ ] System prompt uses compact summaries
- [ ] Token savings measurable (target: 75%+ reduction in base prompt)
- [ ] Metrics tracked
- [ ] All tests pass
- [ ] Existing tests still pass
