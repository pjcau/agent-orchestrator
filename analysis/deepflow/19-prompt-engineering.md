# 19 - Prompt Engineering

## System Prompt Structure

```xml
<role>You are {agent_name}, an open-source super agent.</role>

<soul>{agent personality from SOUL.md}</soul>

<memory>{injected memory context}</memory>

<thinking_style>
  - Think concisely before acting
  - PRIORITY CHECK: clarify if anything unclear
  - Never write full answer in thinking
  - Must provide actual response after thinking
</thinking_style>

<clarification_system>
  WORKFLOW: CLARIFY → PLAN → ACT
  5 clarification types: missing_info, ambiguous_requirement,
    approach_choice, risk_confirmation, suggestion
</clarification_system>

<skill_system>
  Progressive loading pattern
  Available skills listed with paths
</skill_system>

<subagent_system> (if enabled)
  DECOMPOSE → DELEGATE → SYNTHESIZE
  Hard limit: max N task calls per response
  Multi-batch execution pattern
</subagent_system>

<working_directory>
  /mnt/user-data/{uploads,workspace,outputs}
</working_directory>

<response_style>
  Clear, Natural, Action-Oriented
</response_style>

<citations>
  [citation:TITLE](URL) format
</citations>

<critical_reminders>
  Clarification first, Skill first, Progressive loading,
  Output files in /outputs, Multi-task parallel calls,
  Language consistency, Always respond after thinking
</critical_reminders>

<current_date>2026-03-14, Friday</current_date>
```

## Key Prompt Patterns

### 1. Clarification-First Workflow
The prompt enforces a strict CLARIFY → PLAN → ACT sequence. Five clarification types with explicit examples for each. The `ask_clarification` tool interrupts execution via `Command(goto=END)`.

### 2. Progressive Skill Loading
Skills are listed by name/description but NOT loaded into context. Agent reads SKILL.md on demand. This keeps the system prompt lean (~2-4K tokens vs potentially 50K+ if all skills were embedded).

### 3. Sub-agent Batching
Explicit instructions for counting sub-tasks and batching:
```
MUST count sub-tasks in thinking:
  If count <= 3: Launch all in this response
  If count > 3: Pick first 3, save rest for next turn
```

With concrete examples and counter-examples.

### 4. Tool Description Requirement
Every tool requires a `description` parameter first:
```
Args:
    description: Explain why you are running this command. ALWAYS PROVIDE FIRST.
    command: The bash command to execute.
```

Forces the LLM to articulate intent before acting.

### 5. Memory Injection
Memory context wrapped in `<memory>` tags, max 2000 tokens. Top 15 facts + context summaries.

## Prompt Template Variables

| Variable | Source |
|----------|--------|
| `agent_name` | Config or "DeerFlow 2.0" |
| `soul` | SOUL.md file content |
| `memory_context` | Memory system |
| `skills_section` | Enabled skills list |
| `subagent_section` | Sub-agent instructions |
| `subagent_reminder` | Concurrency reminder |
| `subagent_thinking` | Decomposition check |

## Key Insight

DeerFlow's prompts are extremely well-structured with XML tags, clear sections, and explicit rules. The clarification-first pattern is particularly well-designed — preventing the agent from starting work before understanding the task.
