# Feature: Structured Clarification System

## Context

From DeerFlow analysis (analysis/deepflow/20-human-in-the-loop.md, 19-prompt-engineering.md, 29-learnings.md L5).
DeerFlow's 5-type clarification system with CLARIFY → PLAN → ACT ordering prevents agents from doing work based on assumptions. The `ask_clarification` tool interrupts cleanly.

## What to Build

### 1. Clarification Types

```python
# src/agent_orchestrator/core/clarification.py

from enum import Enum
from dataclasses import dataclass

class ClarificationType(Enum):
    MISSING_INFO = "missing_info"     # Required info not provided
    AMBIGUOUS = "ambiguous"           # Multiple interpretations possible
    APPROACH = "approach"             # Multiple valid approaches, need preference
    RISK = "risk"                     # Action has significant risk, confirm before proceeding
    SUGGESTION = "suggestion"         # Agent has a recommendation, wants validation

@dataclass
class ClarificationRequest:
    type: ClarificationType
    question: str
    options: list[str] | None = None   # Suggested answers (if applicable)
    context: str | None = None          # Why this clarification is needed
    blocking: bool = True               # If True, agent pauses until answered

@dataclass
class ClarificationResponse:
    answer: str
    request_id: str
```

### 2. ask_clarification Tool

Create a tool that agents can call to ask the user a structured question:

```python
# src/agent_orchestrator/skills/clarification_skill.py

class ClarificationSkill(Skill):
    @property
    def name(self) -> str:
        return "ask_clarification"

    @property
    def parameters(self) -> dict:
        return {
            "type": {"type": "string", "enum": ["missing_info", "ambiguous", "approach", "risk", "suggestion"]},
            "question": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}, "optional": True},
            "context": {"type": "string", "optional": True},
        }

    async def execute(self, params: dict) -> SkillResult:
        # Creates a ClarificationRequest and emits it via EventBus
        # The dashboard picks it up and shows it to the user
        # Agent execution pauses until response is received
        ...
```

### 3. CLARIFY → PLAN → ACT Workflow

Update agent system prompts to enforce the ordering:

```
## Workflow Protocol

Before executing any task, follow this strict ordering:

1. **CLARIFY**: If ANYTHING is unclear, use ask_clarification FIRST.
   - Missing information → type: missing_info
   - Ambiguous requirements → type: ambiguous
   - Multiple valid approaches → type: approach
   - Risky action → type: risk
   Do NOT guess. Do NOT assume. Ask.

2. **PLAN**: State your plan in 3-5 bullet points. Do not ask for plan approval.

3. **ACT**: Execute the plan using available tools.

NEVER skip CLARIFY. NEVER start ACT without a PLAN.
```

### 4. Dashboard Integration

- **WebSocket event**: `clarification.request` sent to frontend with the ClarificationRequest
- **UI component**: Modal dialog showing the question, type badge, options (if any), and free-text input
- **WebSocket response**: `clarification.response` sent back with the user's answer
- **Agent resume**: Agent receives the response and continues execution

### 5. Timeout Handling

If no response within 5 minutes:
- Emit `clarification.timeout` event
- Agent falls back to best-guess approach with a WARNING log
- Response is logged as `[No response — agent proceeded with assumption: ...]`

## Files to Modify

- **Create**: `src/agent_orchestrator/core/clarification.py` (types and data classes)
- **Create**: `src/agent_orchestrator/skills/clarification_skill.py` (the tool)
- **Modify**: `src/agent_orchestrator/core/agent.py` (support pause/resume on clarification)
- **Modify**: `src/agent_orchestrator/dashboard/events.py` (add clarification event types)
- **Modify**: `src/agent_orchestrator/dashboard/app.py` (WebSocket handler for clarification responses)
- **Modify**: Agent system prompts in `.claude/agents/` (add CLARIFY → PLAN → ACT)

## Tests

- Test ClarificationRequest creation for each type
- Test ask_clarification tool emits event
- Test agent pauses on blocking clarification
- Test agent resumes on response
- Test timeout falls back to assumption
- Test non-blocking clarification doesn't pause
- Test dashboard receives clarification event
- Test end-to-end: agent asks → user answers → agent continues

## Acceptance Criteria

- [ ] 5 clarification types defined
- [ ] ask_clarification tool implemented
- [ ] Agent pause/resume on clarification
- [ ] Dashboard UI for clarification dialog
- [ ] Timeout fallback with warning
- [ ] System prompts updated with CLARIFY → PLAN → ACT
- [ ] All tests pass
- [ ] Existing tests still pass
