---
sidebar_position: 6
title: Cooperation
---

# Inter-Agent Cooperation

How agents communicate when tasks span multiple domains.

```mermaid
sequenceDiagram
    participant TL as Team Lead
    participant BE as Backend Agent
    participant FE as Frontend Agent
    participant SC as Shared Context Store

    TL->>BE: TaskAssignment
    BE-->>TL: TaskResult
    BE->>SC: ArtifactShare (code, API contracts)

    TL->>FE: TaskAssignment
    FE-->>TL: TaskResult
    FE->>SC: ArtifactShare (UI changes, test results)

    Note over SC: file changes, API contracts, test results
```

## Cooperation Patterns

- **Delegation** — team-lead assigns sub-tasks to specialists
- **Artifact sharing** — agents publish outputs (code, specs) to a shared store
- **Dependency ordering** — orchestrator ensures backend runs before frontend when needed
- **Conflict resolution** — when two agents modify the same file, team-lead resolves
