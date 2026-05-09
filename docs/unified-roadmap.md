# Unified Roadmap — Agent Orchestrator

**Source of truth for "what's done, what's next, why."**
Consolidates the 5 deep-dive analyses under `analysis/` (deepflow, langgraph, paperclip, llm-use, harnessed-llm-agent) and the canonical `docs/roadmap.md` into a single dependency-aware view.

This file replaces fragmented per-analysis roadmaps for prioritisation purposes; the per-analysis files remain as research notes.

---

## TL;DR

- **Coverage of the harnessed-LLM-agent reference model**: **~95 %** (18 of 19 components ✅, only **#9 RAG** flipped from ❌ to ✅ during the Q1+Q2+Q3 sprint described below).
- **Q1+Q2 priorities P1–P6 all shipped** in this sprint, parallelised across 5 worktree agents and converged into main:
  - P1 RAG (knowledge subsystem + skill + UI toggle + log highlighting)
  - P2 Evaluator framework (LLMJudge + RubricEvaluator + EvalSuite + REST)
  - P3 Guardrails layer (PII / Secrets / PromptInjection / Schema / Cost)
  - P4 Personalized Memory namespace (`("user", id)` + profile extractor)
  - P5a Cooperation typed messages + protocol spec
  - P6 Observability polish (Langfuse + Phoenix optional exporters)
- **Single biggest gap remaining**: nothing critical. P5b (A2A adapter) is parked until the Google A2A spec stabilises.
- **Test count grew** from 1865 → 2065 (+200 new tests across 5 priorities) with 0 failures.

The "Growth graph" section below traces how each priority evolved the system and which downstream capabilities each one unlocks.

---

## Why RAG matters for every execution mode

A common misconception is that retrieval is only useful for "Simple Prompt" Q&A over an attached document. The truth is the opposite — RAG benefits **scale with agent complexity**:

| Mode | RAG benefit | Why |
|---|---|---|
| **Simple Prompt** | Low–Medium | A single file already fits via `file_context`. RAG kicks in only when content > context window. |
| **Single Agent** | **High** | Tool-using agents (code-reviewer, security-auditor, …) can call `retrieve(query)` to fetch only the relevant chunks instead of stuffing the whole repo into the prompt. |
| **Multi-Agent** | **Very High** | Per-agent and shared knowledge namespaces let team-lead delegate without manual context plumbing — each sub-agent pulls what it needs from its own namespace. |

The P1 design uses three namespaces: `("agent", name)`, `("shared",)`, `("user", id)`. The same store powers per-agent expertise, organisation-wide policy docs, and personalised memory (P4).

---

## Status snapshot (today)

```mermaid
graph TD
    classDef done fill:#1e5e2a,stroke:#2dba4e,color:#fff,stroke-width:1px
    classDef partial fill:#7a5b00,stroke:#e6a000,color:#fff,stroke-width:1px
    classDef missing fill:#5e1e1e,stroke:#e04848,color:#fff,stroke-width:1px
    classDef new fill:#1e2e5e,stroke:#5e8eff,color:#fff,stroke-width:2px

    subgraph Harness["Harness (runtime)"]
        H1["Runtime loop &amp; Agent"]:::done
        H2["StateGraph + channels"]:::done
        H3["Checkpointing<br/>InMem + Postgres"]:::done
        H4["Embedded client.py"]:::done
    end

    subgraph Skills["Skills"]
        S1["Operational<br/>(19 skills + middleware)"]:::done
        S2["Decision Heuristics<br/>(router, presets, health)"]:::done
        S3["Normative Constraints<br/>core/guardrails.py — P3"]:::new
    end

    subgraph Memory["Memory"]
        M1["Working Context<br/>(conversation, threads, fork, restore)"]:::done
        M2["Episodic<br/>(store, 30d TTL)"]:::done
        M3["Personalized<br/>core/personalized_memory.py — P4"]:::new
        M4["Semantic / RAG<br/>core/knowledge/* — P1"]:::new
    end

    subgraph Protocols["Protocols"]
        Pu["Agent &harr; User<br/>(clarification, SSE HITL)"]:::done
        Pa["Agent &harr; Agent<br/>core/cooperation_messages.py + spec — P5a"]:::new
    end

    subgraph Orbital["Orbital modules"]
        O1["Sub-Agent Orchestration<br/>(team-lead, 30 agents)"]:::done
        O2["Sandbox<br/>(Docker + local)"]:::done
        O3["Observability<br/>OTel + Prom + Tempo + Langfuse + Phoenix — P6"]:::new
        O4["Compression<br/>(SummarizationConfig)"]:::done
        O5["Approval Loop<br/>(clarification + RunManager)"]:::done
        O6["Evaluator<br/>core/evaluator.py + evals/ — P2"]:::new
    end

    subgraph Recent["UI work (earlier this session)"]
        R1["A2 Conversation persist"]:::done
        R2["B Full Reset"]:::done
        R3["C2 Document upload"]:::done
        R4["C2.1 Image OCR<br/>(tesseract)"]:::done
        R5["D File transparency"]:::done
        R6["Removed vanilla UI<br/>(React-only)"]:::done
    end
```

Legend: green = ✅ done before this sprint, blue = ✅ shipped in this Q1+Q2 sprint.

Legend: green = ✅ done, yellow = ⚠️ partial, red = ❌ missing.

---

## Growth graph (how the system grew this sprint)

How each priority evolved the system, with the downstream capability each one enables. P1–P6 were built in **parallel across 5 worktree agents** rather than sequentially, then converged into main as separate commits.

```mermaid
graph LR
    classDef shipped fill:#1e2e5e,stroke:#5e8eff,color:#fff,stroke-width:2px
    classDef benefit fill:#1e3e1e,stroke:#3fb950,color:#fff,stroke-width:1px
    classDef parked fill:#3a3a3a,stroke:#888,color:#ccc,stroke-width:1px

    BASE["Pre-sprint baseline<br/>(82% match-matrix coverage)"]

    BASE --> P1["P1 — RAG<br/>core/knowledge/* + skill + UI toggle<br/>+ /api/knowledge/* + log highlighting"]:::shipped
    BASE --> P2["P2 — Evaluator<br/>core/evaluator.py + evals/<br/>+ /api/evals/* + smoke dataset"]:::shipped
    BASE --> P3["P3 — Guardrails<br/>core/guardrails.py + 5 built-ins<br/>+ Agent.execute() pre/post hooks"]:::shipped
    BASE --> P4["P4 — Personalized Memory<br/>core/personalized_memory.py<br/>+ profile_extractor + system-prompt block"]:::shipped
    BASE --> P5a["P5a — Cooperation typed messages<br/>core/cooperation_messages.py<br/>+ docs/cooperation-protocol.md"]:::shipped
    BASE --> P6["P6 — Observability<br/>core/observability/{langfuse,phoenix}<br/>+ docs/trace-schema.md"]:::shipped

    P1 --> AGENTS["Every agent category gains retrieval<br/>code-reviewer, finance, data-sci, marketing"]:::benefit
    P1 --> CTX["Context-window ceiling lifted"]:::benefit
    P3 --> SAFETY["Multi-tenant + untrusted-input safe"]:::benefit
    P3 --> COMPLIANCE["PII redaction, secrets scan, schema enforcement"]:::benefit
    P2 --> QUALITY["Regression detection on every PR"]:::benefit
    P2 --> TUNING["Data-driven prompt &amp; model A/B"]:::benefit
    P4 --> UX["Per-user style + recurring topics<br/>across sessions"]:::benefit
    P4 --> RGPD["GDPR-style wipe per user"]:::benefit
    P5a --> ONBOARD["Lower onboarding cost for new agents<br/>(typed messages auto-document)"]:::benefit
    P6 --> DEBUG["LLM-native trace UI<br/>prompt/completion pairs in Langfuse/Phoenix"]:::benefit

    P1 --- P4_link["shares ('user', id) namespace"]:::benefit
    P4 --- P4_link
    P2 --- P3_link["measures false-positive rate"]:::benefit
    P3 --- P3_link

    P5b["P5b — A2A adapter<br/>(parked: Google A2A spec moving)"]:::parked
```

**Why this order across 5 parallel worktrees:**

- The 5 priorities touch mostly disjoint files. Where they overlap (`core/agent.py`, `dashboard/app.py`, `dashboard/events.py`, `CLAUDE.md`, `docs/abstractions.md`), the edits are additive — each agent ADDS without rewriting. Convergence was a 3-way merge with mechanical conflict resolution.
- Worktree isolation guarantees no agent interferes with another's running tests.
- After convergence, **2065 / 2065 pytest pass** (was 1865 before this sprint — +200 tests).

---

## Improvement graph (original priority order, now archived for reference)

P1–P6 are the priorities from `analysis/harnessed-llm-agent/07-roadmap.md`. Arrows show **enabling relationships**, not strict prerequisites — every node can be built independently. **All shipped in this sprint** — kept for archaeology.

```mermaid
graph LR
    classDef now fill:#7a3b00,stroke:#e07b00,color:#fff,stroke-width:2px
    classDef next fill:#3b3b7a,stroke:#7b7be0,color:#fff,stroke-width:1px
    classDef later fill:#3a3a3a,stroke:#888,color:#ccc,stroke-width:1px
    classDef cross fill:#5a3a5a,stroke:#c060c0,color:#fff,stroke-width:1px

    P1["P1 — Semantic Knowledge / RAG<br/>Effort: M · Impact: 🔥🔥🔥"]:::now
    P3["P3 — Guardrails layer<br/>Effort: S · Impact: 🔥🔥"]:::now
    P2["P2 — Evaluator framework<br/>Effort: M · Impact: 🔥🔥🔥<br/><i>cross-cutting</i>"]:::cross

    P4["P4 — Personalized Memory<br/>Effort: S · Impact: 🔥🔥"]:::next
    P5a["P5a — A2A protocol docs<br/>Effort: S · Impact: 🔥"]:::next
    P6["P6 — Observability polish<br/>Effort: S · Impact: 🔥"]:::next

    P5b["P5b — A2A adapter (Google A2A)<br/>Wait until standard stable"]:::later

    AGENTS["Every agent category<br/>(software, finance, data-sci, marketing)"]
    SAFETY["Multi-tenant safety<br/>+ untrusted-input workloads"]
    QUALITY["Regression detection<br/>+ data-driven tuning"]
    UX["Per-user personalisation"]
    INTEROP["Cross-system agent calls"]
    OBS["Better LLM trace UX"]

    P1 --> AGENTS
    P3 --> SAFETY
    P2 --> QUALITY
    P2 -.-> P1
    P2 -.-> P3
    P2 -.-> P4
    P4 --> UX
    P4 -.-> P1
    P5a --> INTEROP
    P5b --> INTEROP
    P6 --> OBS
```

**Reading the graph:**

- Solid arrows = "this priority unlocks/produces this benefit".
- Dotted arrows = "P2 *measures* the quality of these other priorities" (cross-cutting, not a hard prerequisite).
- P4 dotted-arrow into P1 = personalised memory becomes meaningful only when you can retrieve from it (which is the same `KnowledgeStore` infrastructure as P1).

---

## Priority cards

### P1 — Semantic Knowledge / RAG  🔥🔥🔥

| | |
|---|---|
| **Effort** | M (1–2 weeks) |
| **Risk** | Low (additive; no existing feature depends on it) |
| **Source** | `analysis/harnessed-llm-agent/07-roadmap.md` §P1 |
| **Status** | ❌ Not started |

**What it adds**
- `core/knowledge.py`: `EmbeddingProvider` ABC + `OpenAIEmbeddings`, `LocalEmbeddings`, `ClaudeEmbeddings`; `KnowledgeStore` ABC + `PgVectorStore` impl; namespaces `("agent", name)`, `("shared",)`, `("user", id)`.
- `skills/retrieval_skill.py`: `retrieve(query, namespace, k=5)` tool wired into the skill middleware chain.
- Ingestion pipeline: existing `core/document_converter.py` → chunks → embeddings → store.
- API: `POST /api/knowledge/ingest`, `POST /api/knowledge/search`, `GET /api/knowledge/namespaces`.
- Dashboard: knowledge tab — list namespaces, upload docs, test retrieval.

**Benefits**
- Unlocks **every agent category**: code-reviewer searches the codebase, finance pulls filings, data-scientist looks up schemas, marketer queries brand docs.
- Removes the context-window ceiling that today caps how much an agent can "know about" a project.
- Combined with P4 namespaces, gives per-user personalisation for free.

**Reference repos**: `pgvector/pgvector`, `run-llama/llama_index`, `chroma-core/chroma`.

---

### P2 — Evaluator framework  🔥🔥🔥  *(cross-cutting)*

| | |
|---|---|
| **Effort** | M |
| **Risk** | Low (new subsystem, opt-in in CI) |
| **Source** | `analysis/harnessed-llm-agent/07-roadmap.md` §P2 |
| **Status** | ⚠️ Partial — `core/benchmark.py`, `conformance.py`, `smoke_tester.py` exist but no LLM-judge, no datasets, no CI gate. |

**What it adds**
- `core/evaluator.py`: `Evaluator` ABC, `LLMJudge`, `RubricEvaluator` (regex / contains / JSON schema / length), `EvalSuite`.
- `evals/` directory with YAML/JSON golden datasets and CLI runners.
- API + dashboard tab: score-over-time, side-by-side diffs.
- CI: GitHub Action that runs a smoke eval on every PR; fail if any metric regresses > 5 %.

**Benefits**
- Closes the feedback loop. Without it, P1 retrieval quality, P3 false-positive rate, prompt-tuning experiments, model swaps — **all are blind flights**.
- Makes "did this PR make the agent smarter or dumber?" a yes/no answer.

**Why labelled cross-cutting**: P2 is independent of P1/P3/P4 to *build*, but it MEASURES them. You can ship it before, after, or in parallel with the others; the impact compounds.

**Reference repos**: `openai/evals`, `confident-ai/deepeval` (pytest-friendly, drop-in), `explodinggradients/ragas` (best paired with P1).

---

### P3 — Guardrails layer  🔥🔥

| | |
|---|---|
| **Effort** | S (< 1 week) |
| **Risk** | Medium (wraps `Agent.execute()`) |
| **Source** | `analysis/harnessed-llm-agent/07-roadmap.md` §P3 |
| **Status** | ⚠️ Partial — input/output filtering exists in `audit.py`, `loop_detection.py`, `memory_filter.py`, but no unified pre/post layer. |

**What it adds**
- `core/guardrails.py`: `Guardrail` ABC + `GuardrailManager` + built-ins:
  - `PIIScanner`, `SecretsScanner`, `PromptInjectionDetector`, `OutputSchemaGuard`, `CostGuard`.
- Integration in `Agent.execute()`: pre-LLM input check, post-LLM output check, `guardrail.blocked` event.
- YAML config per-agent.

**Benefits**
- Required for **multi-tenant** deployment or **untrusted user input** in any production scenario.
- Cheap to add now (S effort), painful to retrofit once agents are wired into customer paths.
- Independent of P1: deploy as soon as the team has a free week.

**Reference repos**: `guardrails-ai/guardrails`, `NVIDIA/NeMo-Guardrails`, `protectai/llm-guard`.

---

### P4 — Personalized Memory  🔥🔥

| | |
|---|---|
| **Effort** | S |
| **Risk** | Very Low (additive namespace in existing store) |
| **Source** | `analysis/harnessed-llm-agent/07-roadmap.md` §P4 |
| **Status** | ⚠️ Partial — `core/users.py` and `store.py` exist; no `("user", id)` namespace, no auto-injection into system prompt. |

**What it adds**
- Extend `store_postgres.py` write paths to accept `user_id`.
- In `Agent._build_system_prompt()`: append `<user_profile>` block with top-N user memories.
- Async `profile_extractor` skill: scans recent messages, persists preferences.
- API: `GET /api/memory/users/{user_id}`, `DELETE /api/memory/users/{user_id}/{key}`.

**Benefits**
- Per-user style/preferences without manual prompt engineering.
- Foundation for any "recall what we discussed last week" UX.
- Cheap; reuses the storage you already have.

**Reference repos**: `mem0ai/mem0`, `letta-ai/letta`.

---

### P5 — Agent ↔ Agent protocol  🔥

| | |
|---|---|
| **Effort** | S (5a — docs) / L (5b — A2A adapter) |
| **Risk** | Low |
| **Source** | `analysis/harnessed-llm-agent/07-roadmap.md` §P5 |
| **Status** | ⚠️ Partial — `core/cooperation.py` works but is undocumented. |

**5a — Tactical (recommended now)**: write the spec for the existing `cooperation.py` — message types (`delegate`, `result`, `conflict`, `capability_query`), state transitions, error handling. Add typed message classes. 2–3 days.

**5b — Strategic (Q3 2026 at earliest)**: build an adapter that exposes our agents over Google's [A2A](https://github.com/google/A2A) protocol once the spec stabilises. **Not recommended yet** — protocol still moving as of April 2026.

**Benefits**
- 5a immediately reduces onboarding cost for new agents.
- 5b enables cross-system agent delegation (later).

---

### P6 — Observability polish  🔥

| | |
|---|---|
| **Effort** | S |
| **Risk** | None |
| **Source** | `analysis/harnessed-llm-agent/07-roadmap.md` §P6 |
| **Status** | ⚠️ Polish on top of solid OTel/Prometheus/Grafana foundation. |

- Add **Langfuse** exporter alongside OTel — nicer LLM-native trace viewer for prompt/completion pairs.
- Add **Phoenix** (Arize) exporter — free hosted alternative.
- Document the trace schema (span attributes, event naming).

**Benefits**: better UX for debugging individual agent runs. Pure additive; no risk.

---

## Items already shipped (don't re-do)

These appeared as "improvements" in older analyses (langgraph, llm-use, deepflow, paperclip) but are now in main and don't need work. Listed here so they don't sneak back into a "new roadmap" by accident.

| Item | Source | Where it lives now |
|---|---|---|
| Channel-based state with reducers | langgraph Phase 1 | `core/channels.py`, `core/graph.py` |
| Conformance test suite for Provider | langgraph Phase 1 | `core/conformance.py` |
| Task-level result caching | langgraph Phase 1 | `core/cache.py` |
| Interrupt/resume HITL | langgraph Phase 2 | `core/clarification.py`, `dashboard/sse.py` |
| Store abstraction (cross-agent) | langgraph Phase 2 | `core/store.py`, `core/store_postgres.py` |
| Skill middleware pattern | langgraph Phase 2 | `core/skill.py` middleware chain |
| Loop detection middleware | llm-use 1.1 | `core/loop_detection.py` |
| Tool description parameter | llm-use 1.3 | provider message types |
| Progressive skill loading | llm-use 2.1 | implemented |
| Configurable context summarisation | llm-use 2.2 | `SummarizationConfig` |
| Embedded client | llm-use 3.1 | `client.py` |
| File upload & conversion | deepflow 4.3 | `core/document_converter.py` + `/api/upload` |
| Image OCR (tesseract) | this session | `_convert_image` (commit `edaa0be`) |
| Sandbox execution | deepflow 4.2 | `core/sandbox.py`, `dashboard/sandbox_manager.py` |
| Slack / Telegram integration | deepflow 5 | `integrations/slack_bot.py`, `telegram_bot.py` |
| Harness/App boundary | deepflow 6.1 | enforced by `tests/test_import_boundary.py` |
| Conversation persistence (multi-turn) | this session | A2 — commit `53ea2a0` |
| Full Reset (chat + memory + files + graph) | this session | B — commit `2a48e43` |
| File context transparency | this session | D — commit `7736ef5` |
| Removed dual UI fallback | this session | commit `1719a54` |
| Agent ↔ Agent cooperation spec + typed messages (P5a) | `analysis/harnessed-llm-agent/07-roadmap.md` §P5a | `core/cooperation_messages.py`, [`docs/cooperation-protocol.md`](cooperation-protocol.md) |

---

## Sprint history (what actually happened)

Originally planned as a quarterly sequence. In practice, P1–P6 were built **in parallel across 5 worktree agents** and converged in a single afternoon:

```mermaid
gantt
    title Actual sprint timeline
    dateFormat  HH:mm
    axisFormat  %H:%M
    section Sequential (main)
    P1 RAG core (M)             :done, p1c, 11:00, 30m
    P1 RAG skill+API (M)        :done, p1s, after p1c, 25m
    P1 RAG UI toggle (M)        :done, p1u, after p1s, 25m
    section Parallel (worktrees)
    P3 Guardrails (S)           :done, p3, 11:50, 30m
    P2 Evaluator (M)            :done, p2, 11:50, 35m
    P4 Personalized Memory (S)  :done, p4, 11:50, 40m
    P5a Cooperation spec (S)    :done, p5a, 11:50, 20m
    P6 Observability (S)        :done, p6, 11:50, 25m
    section Convergence
    Merge worktrees + tests     :done, conv, 12:35, 15m
    Final docs + push           :done, fin, after conv, 5m
    section Parked
    P5b A2A adapter (L)         :crit, 2026-09-01, 21d
```

**Why parallel beat sequential here:**

1. The 5 priorities are **mostly disjoint**: each owns its own module(s) and tests. Shared file edits (CLAUDE.md, abstractions.md, agent.py kwargs, app.py router includes, events.py event types) are **additive** by design — every agent appends, none rewrite.
2. SOLID compliance pays off at convergence: the new abstractions (`KnowledgeStore`, `Guardrail`, `Evaluator`, `PersonalizedMemory`) plug into existing seams (`Agent.__init__`, `app.state`, EventBus) without colliding.
3. Convergence = three-way merges + a short conflict resolution on `agent.py` (combined kwargs) and `app.py` (combined router includes). Less than 15 minutes of manual work.
4. The original "P1 → P3 → P2 → P4 → P5a → P6" sequencing is preserved as the **archived improvement graph** above for historical context.

---

## Definition of done (per priority)

Every roadmap item is "done" only when:

1. Implementation + unit tests
2. Integration test through `Orchestrator` or `Agent`
3. Docs updated: `CLAUDE.md`, relevant `docs/*.md`, this file
4. Metrics exported to Prometheus
5. Example in `examples/` or the embedded client

---

## Pointers

- Per-component status: `analysis/harnessed-llm-agent/06-match-matrix.md` (now 18/19 ✅)
- Original roadmap with full implementation plans: `analysis/harnessed-llm-agent/07-roadmap.md`
- Older domain-specific roadmaps (mostly already shipped): `analysis/{deepflow,langgraph,llm-use,paperclip}/`
- Canonical product roadmap (Phase 0 / Phase 2 / Phase 3 etc.): `docs/roadmap.md`
- Cooperation protocol spec: `docs/cooperation-protocol.md`
- Trace schema (Tempo / Langfuse / Phoenix): `docs/trace-schema.md`

## What's next

The match matrix has only one ⚠️ row left and zero ❌. Realistic next steps:

1. **Hook P3 Guardrails into the production agents** (currently optional kwarg). Pick a default-on safe set (PII redact + Secrets block) for multi-tenant deployments.
2. **Wire P2 Evaluator into CI**: add a small smoke suite as a GitHub Action gate that fails on regression > 5%.
3. **Swap RAG defaults**: HashEmbedder is dev-only; production should use `LocalEmbeddingProvider` (sentence-transformers) or `OpenAIEmbeddingProvider`. PgVector backend instead of `InMemoryKnowledgeStore` once usage grows.
4. **Re-evaluate P5b A2A** in Q3 once the Google A2A spec stabilises.
