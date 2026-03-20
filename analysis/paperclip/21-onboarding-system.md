# 21 - Onboarding System

## Overview

Paperclip ships default agent templates for company onboarding. The flagship template is a CEO agent with a rich persona, execution protocol, and structured memory system.

## CEO Agent Template

### SOUL.md — Persona Definition

```markdown
# Strategic Posture
- You own the P&L. Every decision rolls up to revenue, margin, and cash.
- Default to action. Ship over deliberate.
- Think in constraints, not wishes. Ask "what do we stop?" before "what do we add?"
- Hire slow, fire fast, and avoid leadership vacuums.

# Voice and Tone
- Be direct. Lead with the point, then give context.
- Write like you talk in a board meeting, not a blog post.
- Confident but not performative.
- Own uncertainty when it exists.
- No exclamation points unless something is genuinely on fire.
```

This is a masterclass in prompt engineering for organizational personas.

### AGENTS.md — Instructions

```markdown
Your home directory is $AGENT_HOME. Everything personal to you lives there.
Company-wide artifacts (plans, shared docs) live in the project root.

## Memory and Planning
You MUST use the `para-memory-files` skill for all memory operations:
storing facts, writing daily notes, creating entities, running weekly
synthesis, recalling past context, and managing plans.
```

Key elements:
- Home directory concept (per-agent filesystem space)
- Memory system via skills (knowledge graph, daily notes, tacit knowledge)
- Safety considerations (no exfiltration, no destructive commands)
- Reference files (HEARTBEAT.md, SOUL.md, TOOLS.md)

### HEARTBEAT.md — Execution Protocol

Defines what the CEO does every heartbeat:
1. Check for new issues/goals
2. Review agent status
3. Update plans
4. Delegate work
5. Report to board

## Onboarding Flow

```bash
npx paperclipai onboard --yes
```

1. Creates default company
2. Creates CEO agent with template files
3. Sets up default goal
4. Configures adapter (interactive or `--yes` for defaults)

## Key Patterns
- Rich persona definition (SOUL.md) for organizational behavior
- Structured memory via skills (not just conversation history)
- Home directory per agent (filesystem isolation)
- Execution protocol (HEARTBEAT.md) as a checklist
- Reference file pattern (agents read their own docs)

## Relevance to Our Project
Our agent definitions are minimal (role, capabilities, system prompt). Paperclip's CEO template shows how rich agent personas can be — strategic posture, voice/tone, execution protocols. The SOUL.md pattern is essentially a detailed system prompt, but organized as a readable document. The per-agent home directory concept is interesting — our agents share a working directory.
