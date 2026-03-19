# Feature: Tool Description Parameter

## Context

From DeerFlow analysis (analysis/deepflow/07-tool-system.md, 29-learnings.md L6).
Requiring agents to provide a `description` parameter when calling tools forces the LLM to articulate WHY it's calling the tool. This improves audit log readability and debugging.

## What to Build

Add an optional `description` parameter to all skill interfaces in `src/agent_orchestrator/core/skill.py` and the built-in skills.

### Core Changes

1. **Skill base class**: Add `description` as an optional string parameter to `Skill.execute()`:
   ```python
   async def execute(self, params: dict) -> SkillResult:
       description = params.pop("_description", None)
       # Log the description if present
       if description:
           logger.info(f"Tool {self.name}: {description}")
       # ... existing execution logic
   ```

2. **Tool schema generation**: When generating tool schemas for the LLM, add `_description` to every tool's parameter list:
   ```python
   {
       "_description": {
           "type": "string",
           "description": "Brief explanation of WHY you are calling this tool and what you expect to achieve."
       }
   }
   ```

3. **Audit log integration**: Include the description in `AuditLog` entries (`src/agent_orchestrator/core/audit.py`):
   - Add `tool_description` field to tool-related audit events
   - Display in audit log queries

4. **Dashboard display**: Show the tool description in the real-time tool call events on the dashboard (existing `tool.call` events).

### Key Design Decisions

- Parameter name is `_description` (underscore prefix) to avoid collision with tools that might have their own `description` param
- It's OPTIONAL — tools still work without it. Don't break existing integrations.
- The LLM is encouraged but not forced to provide it (via tool schema description text)

## Files to Modify

- **Modify**: `src/agent_orchestrator/core/skill.py` (extract and log _description)
- **Modify**: `src/agent_orchestrator/core/audit.py` (add tool_description field)
- **Modify**: `src/agent_orchestrator/dashboard/agent_runner.py` (include in tool event emissions)
- **Modify**: Tool schema generation (wherever tools are serialized for LLM)

## Tests

- Test _description is extracted from params before execution
- Test tool works without _description (backward compat)
- Test _description appears in audit log entry
- Test _description appears in dashboard events
- Test _description doesn't interfere with tool's own parameters

## Acceptance Criteria

- [ ] `_description` parameter accepted by all skills
- [ ] Logged to audit trail when present
- [ ] Shown in dashboard tool call events
- [ ] Backward compatible (optional parameter)
- [ ] All tests pass
- [ ] Existing tests still pass
