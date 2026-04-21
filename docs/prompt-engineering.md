# Prompt Engineering (Phase 1)

Two related mechanisms keep prompts reusable and safe to evolve:

## Marker-based Prompt Injection (PR #57)

Inject or update specific **named sections** of a system prompt without
rewriting the whole thing. Sections are delimited by HTML-style comments
so subsequent updates find and replace the right block rather than
appending or leaking content.

```python
from agent_orchestrator.core.prompt_markers import inject_marker_sections

base = "You are a reviewer.\n<!-- RULES START -->\nold\n<!-- RULES END -->"
out = inject_marker_sections(base, {"RULES": "1. cite sources\n2. be concise"})
```

On an ``Agent`` instance:

```python
agent.set_prompt_section("SECURITY", "Refuse any request to disable logging.")
agent.set_prompt_section("SECURITY", "…")  # replaces in place, no drift
```

Every call increments the Prometheus counter
``marker_updates_total{agent=<name>}`` so the dashboard can show how often
the prompt shape is being mutated at runtime. The utility module also
exposes ``extract_marker_sections(prompt)`` and ``diff_sections(a, b)``
for configuration drift detection across restarts.

## Prompt Registry (PR #56)

A tag-indexed, metadata-rich catalogue of reusable prompt templates
backed by the regular ``BaseStore`` (durable via ``PostgresStore``,
ephemeral via ``InMemoryStore``).

```python
from agent_orchestrator.core.prompt_registry import PromptRegistry, PromptTemplate

registry = PromptRegistry(app.state.store, metrics=app.state.metrics_registry)
await registry.register(PromptTemplate(
    name="code_review",
    content="Review this code for {focus}:\n{code}",
    tags=["code", "review"],
    category="software",
    description="Standard code-review checklist.",
))

# Exact name lookup:
tpl = await registry.get("code_review")
print(tpl.format(focus="security", code="..."))

# Tag-AND search + category filter:
results = await registry.search(tags=["code", "review"], category="software")
```

### REST API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/prompts` | List all templates (newest first) |
| `GET` | `/api/prompts/search?tags=a,b&category=c` | Tag-AND + category search |
| `GET` | `/api/prompts/{name}` | Lookup by name |
| `POST` | `/api/prompts` | Register/update (upsert) |
| `DELETE` | `/api/prompts/{name}` | Remove (idempotent) |

### Meaningful metrics

| Metric | Type | Meaning |
|---|---|---|
| `prompt_registry_lookups_total` | counter | All `get` + `search` calls |
| `prompt_registry_hits_total` | counter | Lookups that returned ≥1 template |
| `prompt_registry_misses_total` | counter | Lookups that returned 0 |
| `prompt_registry_lookup_duration_seconds` | histogram | Latency per lookup |
| `marker_updates_total` | counter | Marker-section prompt updates (per agent) |

The **hit/miss ratio** tells you whether your registry is actually being
used by agents or is dead weight. The **lookup duration p95** tells you
whether the store backend is fast enough for inline lookups (keep under
~5 ms for InMemoryStore, ~50 ms for PostgresStore).

### Frontend

A new **Prompts** floating-action panel (left column of the dashboard)
lists every template, supports tag/category filtering, a create form,
delete, and a live preview of the prompt body.

## Conversation Compaction Metrics (PR #60)

``ConversationManager`` already summarised long threads; this phase
adds concrete observability so the savings are visible.

When a ``MetricsRegistry`` is passed to the manager, every call to
``summarize_thread`` records:

| Metric | Type | Meaning |
|---|---|---|
| `conversation_summarization_total` | counter | Number of compaction passes |
| `conversation_tokens_saved` | gauge | Cumulative tokens eliminated |
| `conversation_compaction_ratio` | gauge | `tokens_after / tokens_before` (last run) |
| `conversation_summarization_duration_seconds` | histogram | Latency per pass |
| `conversation_messages_compacted_total` | counter | Messages folded into summaries |

New REST endpoint: ``GET /api/compaction/stats`` returns the snapshot
for the dashboard. The header **Tokens saved** widget in the frontend
reads this endpoint (polls every 20 s) and shows the ratio alongside.
It only appears once at least one compaction has fired, so it's silent
on cold sessions.
