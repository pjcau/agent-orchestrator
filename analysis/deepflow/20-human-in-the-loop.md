# 20 - Human-in-the-Loop

## Clarification Tool

```python
ask_clarification(
    question="Which environment should I deploy to?",
    clarification_type="approach_choice",
    context="I need to know the target environment",
    options=["development", "staging", "production"]
)
```

### Clarification Types

| Type | When to Use |
|------|-------------|
| `missing_info` | Required details not provided |
| `ambiguous_requirement` | Multiple valid interpretations |
| `approach_choice` | Several valid approaches |
| `risk_confirmation` | Destructive actions need confirmation |
| `suggestion` | Recommendation needs approval |

### Implementation

`ClarificationMiddleware` (always last in chain):
- Intercepts `ask_clarification` tool calls
- Interrupts execution via `Command(goto=END)`
- User sees the question in the UI
- Next message resumes from where it stopped

## Plan Mode (TodoList)

When `is_plan_mode = True`:
- `TodoMiddleware` added to chain
- `write_todos` tool available
- Agent creates structured task lists
- Real-time updates as tasks progress

### Task States
- `pending` — not started
- `in_progress` — currently working
- `completed` — finished

### Rules
- Only for complex tasks (3+ steps)
- Exactly ONE task `in_progress` at a time
- Mark completed IMMEDIATELY after finishing
- Don't batch completions

## Frontend Integration

### Clarification UI
- Question displayed in chat
- Options rendered as buttons (if provided)
- User response continues the conversation

### Todo UI (`todo-list.tsx`)
- Visual task list with status indicators
- Real-time updates during execution
- Progress tracking

## Comparison with Our HITL

| Aspect | DeerFlow | Our Orchestrator |
|--------|----------|-----------------|
| Mechanism | Tool-based interrupt | Graph HITL node |
| Flexibility | 5 clarification types | Generic approval |
| Plan Mode | TodoList middleware | Task tracking in graph |
| Frontend | Rich UI components | Basic HTML |

DeerFlow's clarification system is more structured — explicit types with examples in the prompt guide the model on WHEN to ask. Our HITL is more generic but less guided.
