---
sidebar_position: 6
title: Cooperation
---

# Inter-Agent Cooperation

How agents communicate when tasks span multiple domains.

```
┌─────────────┐       TaskAssignment        ┌─────────────┐
│  Team Lead  │ ──────────────────────────→  │  Backend    │
│             │ ←──────────────────────────  │  Agent      │
│             │       TaskResult             └─────────────┘
│             │
│             │       TaskAssignment        ┌─────────────┐
│             │ ──────────────────────────→  │  Frontend   │
│             │ ←──────────────────────────  │  Agent      │
│             │       TaskResult             └─────────────┘
└─────────────┘
       │
       │  ArtifactShare (shared context)
       ▼
┌─────────────────────────────────────────────────┐
│              Shared Context Store                │
│  (file changes, API contracts, test results)    │
└─────────────────────────────────────────────────┘
```

## Cooperation Patterns

- **Delegation** — team-lead assigns sub-tasks to specialists
- **Artifact sharing** — agents publish outputs (code, specs) to a shared store
- **Dependency ordering** — orchestrator ensures backend runs before frontend when needed
- **Conflict resolution** — when two agents modify the same file, team-lead resolves
