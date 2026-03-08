# Research Scout Findings: LLM App Patterns

**Date:** 2026-03-08
**Scout:** backend agent
**Source:** https://github.com/itzharshitmavi/awsome-LLM-model-apps-for-daily-uses
**Description:** Curated collection of 70+ production AI agents and apps demonstrating real-world LLM application patterns.

---

## Evaluation Summary

| Criterion    | Score | Notes                                      |
|--------------|-------|--------------------------------------------|
| Applicable   | 0.7   | Patterns applicable, not direct code       |
| Quality      | 0.5   | Curated list, variable quality per repo    |
| Compatible   | 0.6   | Needs adaptation to Agent Orchestrator arch|
| Safe         | 1.0   | Reference material only, no execution risk |
| **Overall**  | **0.7** |                                          |

---

## Pattern 1: Agent Handoff Protocol (from OpenAI Agents SDK)

### Observation

Multiple repos in the collection build on the OpenAI Agents SDK, which uses an explicit handoff protocol where agents can accept, reject, or partially accept delegated tasks. The current `cooperation.py` handles delegation but only models a fire-and-forget flow — there is no mechanism for a receiving agent to signal that it cannot or will only partially handle a task.

### Current Flow

```
Team-lead → delegates to backend → backend executes → returns result
```

### Proposed Flow (handoff pattern)

```
Team-lead → delegates to backend
  → backend ACCEPTS (has capability)
      → executes → returns result
  → backend REJECTS (lacks capability)
      → team-lead re-routes to another agent
  → backend PARTIALLY accepts
      → requests sub-delegation for the parts it cannot handle
```

### Implementation Concept — `cooperation.py`

```python
from dataclasses import dataclass
from typing import Literal, Optional

@dataclass
class HandoffResponse:
    status: Literal["accepted", "rejected", "partial"]
    reason: str
    suggested_agent: Optional[str] = None  # if rejected, who should handle it
    partial_scope: Optional[str] = None    # if partial, describe what is accepted

class CooperationProtocol:
    async def request_handoff(
        self, from_agent: str, to_agent: str, task: dict
    ) -> HandoffResponse:
        """Send a delegation request and await accept/reject signal."""
        ...

    async def confirm_handoff(self, handoff_id: str) -> None:
        """Receiving agent confirms it is taking ownership of the task."""
        ...

    async def reject_handoff(
        self, handoff_id: str, reason: str, suggestion: Optional[str] = None
    ) -> None:
        """Receiving agent rejects the task and optionally suggests an alternative."""
        ...
```

### Why This Matters

Without explicit accept/reject, a failing agent silently stalls. With handoff responses the orchestrator can immediately re-route, improving the anti-stall guarantees already present in the system.

---

## Pattern 2: Dual-Provider Strategy (from Legal Agent Team)

### Observation

Several legal-domain agents in the collection run the same agent role on two different providers — a cloud LLM for speed and a local LLM for data-sensitivity requirements. The switch is automatic based on task metadata (e.g., a `sensitive` tag).

### Architecture

```
Agent: "backend"
  ├── Cloud mode:  Claude Sonnet  (default — fast, high capability)
  └── Local mode:  Ollama Qwen    (sensitive data, air-gapped environment)

Routing rule:
  if task.has_tag("sensitive") → use local provider
  else                         → use cloud provider
```

### Implementation Concept — `provider_presets.py`

```python
from typing import Callable

DUAL_PROVIDER_PRESET: dict = {
    "name": "dual_provider",
    "description": "Cloud for normal tasks, local for sensitive data",
    "default_provider": "anthropic",
    "sensitive_provider": "local",
    "rule": lambda task: (
        "local" if task.get("tags") and "sensitive" in task["tags"]
        else "anthropic"
    ),
}

# Register alongside existing presets (local_only, cloud_only, hybrid, high_quality)
PRESETS["dual_provider"] = DUAL_PROVIDER_PRESET
```

### Integration Point

`task_queue.py` already carries task metadata; adding a `tags` field and wiring the preset rule into `TaskRouter.route()` would be the minimal change needed.

---

## Pattern 3: Corrective RAG (CRAG) — Self-Validation Loop

### Observation

Several repos implement a Corrective RAG pattern: the agent generates an output, a lightweight validator checks it, and if the check fails the agent regenerates with the validator's feedback attached. A hard cap on correction attempts prevents infinite loops.

### Flow Diagram

```
Agent generates answer
  → Validator checks: correct? complete? safe?
      → CORRECT   → return to user
      → INCORRECT → agent regenerates with feedback appended
                    (max 3 correction loops — anti-stall)
```

### Implementation Concept — new `crag.py` or inline in `graph_patterns.py`

```python
from dataclasses import dataclass, field
from typing import List

@dataclass
class ValidationResult:
    passed: bool
    feedback: List[str] = field(default_factory=list)

class CRAGValidator:
    MAX_LOOPS = 3

    async def validate(self, output: str, task: dict) -> ValidationResult:
        """
        Run a battery of checks against the agent's output.
        Returns ValidationResult with pass/fail and actionable feedback.
        """
        checks = await asyncio.gather(
            self._check_syntax(output),
            self._check_tests(output, task),
            self._check_conventions(output, task),
        )
        failed = [fb for ok, fb in checks if not ok]
        return ValidationResult(passed=len(failed) == 0, feedback=failed)

    async def _check_syntax(self, output: str) -> tuple[bool, str]:
        ...

    async def _check_tests(self, output: str, task: dict) -> tuple[bool, str]:
        ...

    async def _check_conventions(self, output: str, task: dict) -> tuple[bool, str]:
        ...
```

### Graph Integration

`graph_patterns.py` already has retry and loop sub-graphs. CRAG is a specialised loop node that feeds validator feedback back into the LLM prompt before the next attempt. The `MAX_LOOPS = 3` cap aligns with the existing anti-stall rules.

---

## Pattern 4: Memory-Aware Routing

### Observation

The current `router.py` implements 6 routing strategies (local-first, cost-optimised, complexity-based, etc.). None of them consider an agent's already-loaded context. Multiple repos in the collection show that re-using a warm agent (one that already holds relevant schema or conversation context in memory) reduces latency and token usage significantly.

### Problem

```
Current: every task routes to the "cheapest" or "most capable" agent
         without checking whether any agent already has relevant context.

Example gap:
  Task A: backend agent loads full API schema (expensive context window)
  Task B: another API task arrives → routed to a fresh backend instance
          → must reload the full API schema again (wasted tokens + latency)
```

### Proposed Strategy — `router.py`

```python
class MemoryAwareStrategy(RoutingStrategy):
    """
    Prefer agents that already hold context relevant to the incoming task.
    Falls back to a configurable secondary strategy when no warm agent exists.
    """

    def __init__(self, fallback: RoutingStrategy) -> None:
        self.fallback = fallback

    def route(self, task: dict, agents: list) -> object:
        domain = task.get("domain")
        if domain:
            for agent in agents:
                if hasattr(agent, "memory") and agent.memory.has_context_for(domain):
                    return agent  # reuse warm context
        return self.fallback.route(task, agents)
```

### Required Supporting Change

`agent.py` needs a lightweight `memory` attribute exposing a `has_context_for(domain: str) -> bool` method. This could be as simple as tracking the set of domains seen in the current session.

---

## Pattern 5: MCP-First Tool Architecture

### Observation

A significant portion of the collection (especially the agentic coding assistants) exposes all agent capabilities as MCP (Model Context Protocol) tools from the outset, rather than treating MCP as an afterthought. This lets external editors (Cursor, VS Code Copilot, Claude Desktop) discover and call skills directly.

### Current State in Agent Orchestrator

`mcp_server.py` provides an MCP registry but skills are registered internally as Python classes. External consumers cannot discover skills unless they are explicitly wired into the MCP server.

### Architecture Gap

```
External IDE (Cursor, VS Code)
  → connects to Agent Orchestrator MCP server
  → discovers available skills as MCP tools    ← currently manual/incomplete
  → calls skill via MCP protocol
  → gets result back
```

### Proposed Enhancement — `mcp_server.py`

```python
from agent_orchestrator.core.skill import SkillRegistry

def auto_register_skills(mcp_registry: MCPServerRegistry, skill_registry: SkillRegistry) -> None:
    """
    Walk all registered skills and expose each one as an MCP tool automatically.
    Skill docstrings become MCP tool descriptions.
    Skill input/output schemas become MCP input/output schemas.
    """
    for name, skill in skill_registry.skills.items():
        mcp_registry.register_tool(
            name=name,
            description=skill.__doc__ or "",
            input_schema=skill.input_schema(),
            handler=skill.execute,
        )
```

### Value

Any new skill added to `skills/` would automatically appear in MCP without a separate registration step, reducing the maintenance surface and making the orchestrator a first-class MCP server for IDE integrations.

---

## Recommended Implementation Order

| Priority | Pattern                  | Effort | Impact |
|----------|--------------------------|--------|--------|
| 1        | Agent Handoff Protocol   | Medium | High — directly improves anti-stall and routing |
| 2        | Memory-Aware Routing     | Low    | High — reduces token waste on repeated context |
| 3        | MCP-First Tools          | Low    | Medium — improves external discoverability |
| 4        | CRAG Self-Validation     | Medium | Medium — improves output quality for coding tasks |
| 5        | Dual-Provider Strategy   | Low    | Medium — useful for sensitive-data use cases |

---

## Files to Modify per Pattern

| Pattern                | Primary File(s)                            |
|------------------------|--------------------------------------------|
| Agent Handoff Protocol | `cooperation.py`                           |
| Dual-Provider Strategy | `provider_presets.py`, `task_queue.py`     |
| CRAG Self-Validation   | `graph_patterns.py` (new `CRAGValidator`)  |
| Memory-Aware Routing   | `router.py`, `agent.py`                    |
| MCP-First Tools        | `mcp_server.py`                            |

---

*This document is reference material. Each pattern should be implemented incrementally with full tests and documentation per CLAUDE.md requirements.*
