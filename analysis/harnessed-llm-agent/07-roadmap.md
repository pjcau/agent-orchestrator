# 07 — Roadmap

Prioritized build plan to close the gaps identified in the match matrix.

## Ranking criteria

Each item is scored on:

- **Effort** — S (<1 week), M (1-2 weeks), L (>2 weeks)
- **Impact** — 🔥 (useful), 🔥🔥 (high), 🔥🔥🔥 (unlock)
- **Risk** — likelihood of regression on existing features

## Priority order

### P1 — Semantic Knowledge / RAG

- **Effort**: M
- **Impact**: 🔥🔥🔥
- **Risk**: Low (additive, no existing feature depends on it)

**Why first**: the only `❌ MISSING` in the matrix, and it unlocks every agent category (software needs code search, finance needs filings, data-science needs schemas, marketing needs brand docs).

**Plan**:

1. Add `pgvector` extension to the Postgres service (already deployed)
2. Create `core/knowledge.py`:
   - `EmbeddingProvider` abstract class
   - `OpenAIEmbeddings`, `LocalEmbeddings` (sentence-transformers), `ClaudeEmbeddings` (when available)
   - `KnowledgeStore` abstract class + `PgVectorStore` implementation
   - Namespaces similar to `BaseStore`: `("agent", name)`, `("shared",)`, `("user", id)`
3. Add `skills/retrieval_skill.py` — `retrieve(query, namespace, k=5)` tool
4. Wire ingestion path: `document_converter.py` → chunks → embeddings → store
5. API endpoints: `POST /api/knowledge/ingest`, `POST /api/knowledge/search`, `GET /api/knowledge/namespaces`
6. Dashboard UI: knowledge tab (list namespaces, upload docs, test retrieval)
7. Tests + docs (CLAUDE.md, `docs/architecture.md`)

**Reference repos to study**:
- [pgvector/pgvector](https://github.com/pgvector/pgvector) (schema, indexes)
- [run-llama/llama_index](https://github.com/run-llama/llama_index) (retrieval API)
- [chroma-core/chroma](https://github.com/chroma-core/chroma) (client simplicity)

---

### P2 — Evaluator Framework

- **Effort**: M
- **Impact**: 🔥🔥🔥
- **Risk**: Low (new subsystem, opt-in in CI)

**Why second**: closes the feedback loop. Without evals, P1, P3, and every future change is a blind flight.

**Plan**:

1. Create `core/evaluator.py`:
   - `Evaluator` ABC with `evaluate(run, rubric) -> EvalResult`
   - `LLMJudge` — uses a strong model + rubric template
   - `RubricEvaluator` — deterministic checks (regex, contains, JSON schema, length)
   - `EvalSuite` — runs a dataset against an agent config
2. Add `evals/` directory:
   - `evals/datasets/` — YAML/JSON golden sets (prompt, expected, rubric)
   - `evals/runners/` — CLI entrypoints
3. API: `POST /api/evals/run`, `GET /api/evals/runs`, `GET /api/evals/runs/{id}`, `GET /api/evals/compare?a=X&b=Y`
4. Dashboard: Evals tab with score over time, side-by-side diff
5. CI: GitHub Action that runs a "smoke eval suite" on every PR; fail if any metric regresses >5%
6. Consider adopting [deepeval](https://github.com/confident-ai/deepeval) instead of building from scratch — pytest-friendly, batteries included

**Reference repos**:
- [openai/evals](https://github.com/openai/evals)
- [confident-ai/deepeval](https://github.com/confident-ai/deepeval)
- [explodinggradients/ragas](https://github.com/explodinggradients/ragas) (especially once P1 is done)

---

### P3 — Guardrails layer

- **Effort**: S
- **Impact**: 🔥🔥
- **Risk**: Medium (wraps `Agent.execute()` — needs careful integration)

**Why third**: prerequisite for multi-tenant or any workload where untrusted input reaches the agent. Cheap to add now, painful to retrofit later.

**Plan**:

1. Create `core/guardrails.py`:
   - `Guardrail` ABC with `check_input(messages)` and `check_output(response)`
   - `GuardrailResult(passed: bool, reason: str, action: Literal["allow", "block", "redact"])`
   - `GuardrailManager` with register/run
2. Built-in guardrails:
   - `PIIScanner` — regex-based (email, phone, SSN)
   - `SecretsScanner` — API keys, tokens
   - `PromptInjectionDetector` — heuristic + optional LLM
   - `OutputSchemaGuard` — JSON schema validation for structured output
   - `CostGuard` — kill the run if projected cost exceeds a threshold
3. Integrate in `Agent.execute()`:
   - Run input guardrails before LLM call
   - Run output guardrails before returning
   - On block: emit `guardrail.blocked` event, optionally trigger clarification
4. Events + metrics: `guardrail_checks_total`, `guardrail_blocks_total{type}`
5. Config via YAML:
   ```yaml
   guardrails:
     input:
       - type: pii_scanner
         action: redact
     output:
       - type: output_schema
         schema: ./schemas/response.json
         action: block
   ```

**Reference repos**:
- [guardrails-ai/guardrails](https://github.com/guardrails-ai/guardrails)
- [NVIDIA/NeMo-Guardrails](https://github.com/NVIDIA/NeMo-Guardrails)
- [protectai/llm-guard](https://github.com/protectai/llm-guard)

---

### P4 — Personalized Memory

- **Effort**: S
- **Impact**: 🔥🔥
- **Risk**: Very low (additive namespace in existing store)

**Plan**:

1. Extend `store_postgres.py` write paths to accept `user_id` and use namespace `("user", user_id)`
2. In `Agent._build_system_prompt()`, after the existing `<memory>` block, append a `<user_profile>` block with top-N user memories
3. Small skill: `profile_extractor` — scans last N messages, extracts preferences, persists them (runs async after each agent run, not blocking)
4. API: `GET /api/memory/users/{user_id}`, `DELETE /api/memory/users/{user_id}/{key}`
5. Respect existing `memory_filter.py` rules (don't persist session-scoped paths)

**Reference repos**:
- [mem0ai/mem0](https://github.com/mem0ai/mem0)
- [letta-ai/letta](https://github.com/letta-ai/letta)

---

### P5 — Agent-Agent protocol formalization

- **Effort**: L (if A2A) / S (if documenting existing)
- **Impact**: 🔥
- **Risk**: Low

**Two paths**:

**5a. Tactical — document what we have**
Write a spec doc for `core/cooperation.py`: message types (`delegate`, `result`, `conflict`, `capability_query`), state transitions, error handling. Add typed message classes. 2-3 days.

**5b. Strategic — A2A adapter**
Watch Google's [A2A protocol](https://github.com/google/A2A) spec. When stable (as of Apr 2026 still moving), build an adapter that exposes our agents as A2A endpoints. 2-3 weeks. **Not recommended yet** — protocol still evolving.

**Recommendation**: do 5a now, revisit 5b in Q3 2026.

---

### P6 — Observability polish

- **Effort**: S
- **Impact**: 🔥
- **Risk**: None

- Add Langfuse exporter alongside OTel → nicer LLM-native trace viewer
- Add Phoenix exporter → free hosted alternative
- Document the trace schema (span attributes, event naming)

---

## Sequencing

```
Q1 focus:  P1 (RAG) → P2 (Evaluator) → P3 (Guardrails)
Q2 focus:  P4 (Personalized Memory) → P5a (doc protocol) → P6 (obs polish)
Q3 revisit: P5b (A2A) if standard stabilizes
```

## Definition of done (per priority)

Every roadmap item is only "done" when it has:

1. Implementation + unit tests
2. Integration test that exercises it end-to-end through `Orchestrator` or `Agent`
3. Docs updated: `CLAUDE.md`, relevant `docs/*.md`, this folder
4. Metrics exported to Prometheus
5. Example in `examples/` or the embedded client
