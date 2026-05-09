# Quickstart — Features shipped in the Q1+Q2 sprint

Hands-on walkthrough for the six priorities that landed in this sprint. Each section is a self-contained recipe you can run in 30–90 seconds.

> **Prerequisites**: dashboard running locally (`docker compose up dashboard` → `https://localhost:5005`) or in dev (`pip install -e ".[dev,dashboard]" && python -m agent_orchestrator.dashboard.server`). The default RAG embedder is dependency-free; the rest of the recipes work without external services unless noted.

## Pick what you need

| Goal | Jump to |
|---|---|
| Make an agent search a corpus | [P1 RAG](#p1-rag-retrieve-from-a-knowledge-store) |
| Score agent answers against expected outputs | [P2 Evaluator](#p2-evaluator-score-runs-against-a-rubric) |
| Block PII / secrets / prompt injection | [P3 Guardrails](#p3-guardrails-blockredact-bad-input-and-output) |
| Personalise the assistant per user | [P4 Personalized memory](#p4-personalized-memory-per-user-prefs) |
| Use typed agent-to-agent messages | [P5a Cooperation messages](#p5a-typed-cooperation-messages) |
| View LLM traces in Langfuse / Phoenix | [P6 Observability sinks](#p6-observability-sinks-langfuse--phoenix) |
| Try the new dashboard UI bits | [Dashboard tour](#dashboard-tour) |

Convention: every shell snippet uses `localhost:5005`. Replace with your deployed host and add `-H "X-API-Key: …"` if `DASHBOARD_API_KEYS` is set.

---

## P1 RAG — retrieve from a knowledge store

<details open>
<summary>1-minute round-trip: ingest → search → chat with auto-injection</summary>

### Ingest

```bash
curl -sX POST http://localhost:5005/api/knowledge/ingest \
  -H 'Content-Type: application/json' \
  -d '{
    "source_id": "auth-doc",
    "namespace": "shared",
    "text": "# Auth\n\nUse JWT tokens. Sessions are stateless. Tokens expire after 24h.\n\n## Refresh\n\nRefresh tokens last 30 days."
  }'
```

Expected:

```json
{
  "success": true,
  "namespace": "shared",
  "source_id": "auth-doc",
  "chunks_added": 2,
  "embedding_model": "hash-md5"
}
```

### Search

```bash
curl -sX POST http://localhost:5005/api/knowledge/search \
  -H 'Content-Type: application/json' \
  -d '{"query": "how long do tokens last?", "namespace": "shared", "k": 3}'
```

You'll get `hits[]` with `score`, `text`, `location`, `source_id` per chunk and a ready-to-paste `context_block` Markdown.

### Chat with auto-injection

Send a normal prompt with `rag_enabled: true`. The dashboard prepends the retrieved context to your prompt and emits a `knowledge.retrieved` event so the chat UI shows a "RAG: shared · N chunk(s)" bubble before the assistant reply.

```bash
curl -sX POST http://localhost:5005/api/prompt \
  -H 'Content-Type: application/json' \
  -d '{
    "prompt": "How long do auth tokens last?",
    "model": "openai/gpt-4o",
    "provider": "openrouter",
    "rag_enabled": true,
    "rag_namespace": "shared"
  }'
```

The response includes a top-level `rag` summary:

```json
"rag": {"namespace": "shared", "hits": 2, "embedding_model": "hash-md5", "scores": [0.91, 0.74]}
```

### Production swap-in

| Embedder | Switch | Install |
|---|---|---|
| Hash (default, dev) | (built-in) | nothing |
| sentence-transformers | `RAG_EMBEDDING_PROVIDER=local RAG_LOCAL_MODEL=all-MiniLM-L6-v2` | `pip install -e ".[rag]"` |
| OpenAI | `RAG_EMBEDDING_PROVIDER=openai RAG_OPENAI_MODEL=text-embedding-3-small` | `pip install -e ".[openai]"` + `OPENAI_API_KEY` |

</details>

---

## P2 Evaluator — score runs against a rubric

<details>
<summary>Run the smoke suite locally (no LLM needed for dry-run)</summary>

### Run the bundled smoke suite

```bash
python -m evals.runners.cli --suite evals/datasets/smoke.json --dry-run
```

Output:

```
case_id     | passed | mean_score | detail
----------- | ------ | ---------- | ----------------------------------
code-001    |   ✓    |    1.00    | rubric: contains "summary" OK
math-001    |   ✗    |    0.00    | rubric: contains "42" failed
json-001    |   ✓    |    1.00    | rubric: json schema valid
safety-001  |   ✓    |    1.00    | rubric: refusal pattern matched
chat-001    |   ✓    |    0.90    | rubric: contains "Hello" + length OK
---
pass_rate=0.80 mean_score=0.78
```

### Run via REST (background job)

```bash
curl -sX POST http://localhost:5005/api/evals/run \
  -H 'Content-Type: application/json' \
  -d '{"suite_path": "evals/datasets/smoke.json", "agent": "team-lead", "model": "openai/gpt-4o", "provider": "openrouter"}'
# → {"run_id": "abc12345"}

curl -s http://localhost:5005/api/evals/runs/abc12345
# → full report with scores, durations, per-case detail
```

### Compare two runs

```bash
curl -s 'http://localhost:5005/api/evals/compare?a=abc12345&b=def67890'
# → {"delta_pass_rate": +0.10, "delta_mean_score": +0.07, "regressions": [], "improvements": [...]}
```

### Drop-in CI gate (GitHub Actions)

```yaml
- name: Eval gate
  run: |
    python -m evals.runners.cli \
      --suite evals/datasets/smoke.json \
      --agent team-lead --provider openrouter --model openai/gpt-4o \
      --json > eval-result.json
    python -c "
    import json, sys
    r = json.load(open('eval-result.json'))
    assert r['summary']['pass_rate'] >= 0.95, f'Regression: {r[\"summary\"]}'
    "
```

</details>

---

## P3 Guardrails — block/redact bad input and output

<details>
<summary>Wire a manager into your agent in 5 lines</summary>

### Programmatic

```python
from agent_orchestrator.core.guardrails import (
    GuardrailManager, PIIScanner, SecretsScanner, PromptInjectionDetector,
)
from agent_orchestrator.core.agent import Agent

mgr = GuardrailManager()
mgr.register(PIIScanner(action="redact"))         # email/phone/SSN/IBAN/cards → masked
mgr.register(SecretsScanner(action="block"))      # AWS key, GitHub token → block
mgr.register(PromptInjectionDetector(action="block"))

agent = Agent(config=..., provider=..., skill_registry=..., guardrails=mgr)
```

Now every `agent.execute()` runs `mgr.run_input(messages)` before the LLM call and `mgr.run_output(response)` after. On block → `GuardrailBlocked` exception. On redact → messages substituted in place.

### YAML config

Drop into `orchestrator.yaml`:

```yaml
guardrails:
  input:
    - type: pii_scanner
      action: redact
    - type: secrets_scanner
      action: block
    - type: prompt_injection
      action: block
  output:
    - type: output_schema
      schema_path: ./schemas/response.json
      action: block
```

Load at startup:

```python
from agent_orchestrator.core.guardrails import guardrail_manager_from_config
import yaml
mgr = guardrail_manager_from_config(yaml.safe_load(open("orchestrator.yaml"))["guardrails"])
```

### See it in the dashboard

Every check emits one of:

- `guardrail.checked{type, side: "input"|"output", action: "allow"|"block"|"redact"}`
- `guardrail.blocked{type, reason}`
- `guardrail.redacted{type, before, after}`

These show up in the event log with the existing event-category styling.

</details>

---

## P4 Personalized memory — per-user prefs

<details>
<summary>Save preferences and inject them into the system prompt</summary>

### Save / read / delete

```bash
# Save
curl -sX PUT http://localhost:5005/api/user-memory/users/u-123/style \
  -H 'Content-Type: application/json' \
  -d '{"value": {"prefers": "concise, code blocks > prose, dark theme"}}'

# Read all entries for user
curl -s http://localhost:5005/api/user-memory/users/u-123
# → {"items": [{"key": "style", "value": {...}}]}

# Delete one
curl -sX DELETE http://localhost:5005/api/user-memory/users/u-123/style
```

### GDPR-style wipe

```bash
curl -sX DELETE http://localhost:5005/api/user-memory/users/u-123
# → {"success": true, "removed": 5}
```

### Auto-injection into system prompt

When you instantiate an `Agent` with `user_id="u-123"` and `personalized_memory=…`, `build_system_prompt()` appends:

```
<user_profile>
- prefers: concise, code blocks > prose, dark theme
- recurring_topics: [auth, postgres, terraform]
</user_profile>
```

Call `await agent.prefetch_user_profile()` once before the first turn to populate the cache (avoids blocking the synchronous prompt-build).

### Auto-extract from history

```python
from agent_orchestrator.skills.profile_extractor_skill import ProfileExtractorSkill

skill = ProfileExtractorSkill(provider=my_provider, memory=my_personalized_memory)
result = await skill.execute({
    "user_id": "u-123",
    "recent_messages": [{"role": "user", "content": "..."}, ...],
})
# Persists {preferences: [...], style_notes: [...], recurring_topics: [...]}
```

</details>

---

## P5a Typed cooperation messages

<details>
<summary>Send delegate / result / capability-query without raw dicts</summary>

```python
from agent_orchestrator.core.cooperation_messages import (
    DelegateMessage, ResultMessage, CapabilityQueryMessage, parse_message,
)

# Sender:
msg = DelegateMessage(
    message_id="m-1",
    from_agent="team-lead",
    to_agent="backend",
    timestamp=1234567890,
    kind="delegate",
    task_id="t-1",
    description="Build the JWT login endpoint",
    priority="high",
    payload={"due": "tomorrow"},
)
await event_bus.publish(msg.to_dict())

# Receiver:
incoming = await event_bus.next()
parsed = parse_message(incoming)         # dispatches on `kind`
if isinstance(parsed, DelegateMessage):
    result = ResultMessage(
        message_id="m-2", from_agent="backend", to_agent="team-lead",
        timestamp=..., kind="result",
        task_id=parsed.task_id, success=True,
        output="Endpoint shipped at /auth/login",
    )
    await event_bus.publish(result.to_dict())
```

The full message catalogue, sequence diagrams, and state-transition spec are in [docs/cooperation-protocol.md](cooperation-protocol.md).

</details>

---

## P6 Observability sinks — Langfuse / Phoenix

<details>
<summary>Add LLM-native trace UI alongside Tempo (no replacement)</summary>

### Langfuse (cloud or self-hosted)

```bash
pip install -e ".[langfuse]"
export LANGFUSE_PUBLIC_KEY=pk-…
export LANGFUSE_SECRET_KEY=sk-…
export LANGFUSE_HOST=https://cloud.langfuse.com   # default
docker compose up dashboard
```

Open `LANGFUSE_HOST` → traces appear with prompt/completion pairs, eval scores, prompt versions. The existing Tempo pipeline keeps receiving the same spans.

### Phoenix (local, free)

```bash
pip install -e ".[phoenix]"
docker run -d -p 6006:6006 arizephoenix/phoenix:latest
export PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006
docker compose up dashboard
```

Open `http://localhost:6006` for the LLM trace UI.

### What gets traced

Span inventory and attribute reference: [docs/trace-schema.md](trace-schema.md). Highlights: `agent.run`, `llm.call` (with `gen_ai.*` attributes), `graph.node`, `skill.execute`, `agent.message`.

### Both off → no-op

Both exporters are import-safe and degrade gracefully when their package isn't installed or env vars aren't set. Default deploy ships with the existing Tempo pipeline only.

</details>

---

## Dashboard tour

<details>
<summary>What's new in the React UI</summary>

- **RAG checkbox** — next to the Stream toggle in `ChatInput`. When on, every send carries `rag_enabled: true` and a namespace input. Persisted in `localStorage` (`ao_rag_enabled` / `ao_rag_namespace`); survives Reset (it's a user preference, not session state).
- **System bubble** — after each RAG-enabled turn: `RAG: <namespace> · N chunk(s) retrieved (<embedding_model>)`. If retrieval errors, you get `RAG skipped: <reason>` instead.
- **Knowledge category in the event log** — `knowledge.*` events get a `K` icon and a distinct accent. New filter option in the event-log dropdown.
- **File chip transparency** — every attachment shows a kind badge (PDF/CSV/IMG/…), size, source colour (upload vs workspace), truncation indicator. System bubble at send time: `Sent with N files: a.pdf (3.2 KB) [upload], b.csv (12 KB) [workspace]`.
- **Conversation persistence (A2)** — first send auto-creates a conversation; the id lives in `localStorage` (`ao_conv_id`) and the chat replays at boot.
- **Full Reset (B)** — wipes server-side conversation memory + graph + chat + attached files + `localStorage`. Best-effort: UI clears even if the network call fails.

</details>

---

## Pointers

- Roadmap status: [docs/unified-roadmap.md](unified-roadmap.md)
- Match matrix: [analysis/harnessed-llm-agent/06-match-matrix.md](../analysis/harnessed-llm-agent/06-match-matrix.md)
- Trace schema: [docs/trace-schema.md](trace-schema.md)
- Cooperation protocol: [docs/cooperation-protocol.md](cooperation-protocol.md)
- Dashboard internals: [docs/dashboard.md](dashboard.md)
