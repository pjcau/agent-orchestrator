# Phase 3 — Modality detection + hybrid graph execution

The last two of the eight research-scout proposals. Both are pure
utilities — no invasive wiring — so existing callers keep working.

## Modality Detection (PR #88)

`core/modality.py` — a deterministic, dependency-free classifier for
task inputs. No ML, no LLM call; just pattern matching on the input
shape and content.

```python
from agent_orchestrator.core.modality import detect_modality, Modality

detect_modality("What's the capital of France?")      # Modality.TEXT
detect_modality("def f(x):\n    return x*2")          # Modality.CODE (2+ code patterns)
detect_modality("Solve $x^2 + 2x = 0$")               # Modality.EQUATION
detect_modality({"image": "…", "text": "describe"})   # Modality.MIXED
detect_modality({"image": "…"})                       # Modality.IMAGE
detect_modality(b"\x89PNG\r\n\x1a\n…")                # Modality.IMAGE (magic bytes)
detect_modality([{"id": 1}, {"id": 2}])               # Modality.STRUCTURED
```

Priority: **IMAGE > MIXED > STRUCTURED > EQUATION > CODE > TEXT** — the
ranking reflects how sharply each label narrows the set of capable
providers (IMAGE needs a VLM, CODE benefits from coding-tuned models,
STRUCTURED maps to data agents, etc.).

### Integration

The classifier is provided as a utility; wiring into the TaskRouter or
team-lead decomposition is opt-in to avoid breaking callers that do not
need modality-aware routing. Call `record_detection(modality, metrics)`
to increment the Prometheus counter:

```
modality_detected_total{modality="text"|"code"|"image"|"structured"|"equation"|"mixed"}
```

Use this counter to answer the question *"what kind of inputs are we
actually handling?"*. If >30% of traffic is CODE but your default model
is generic, that's a concrete signal to switch.

## Hybrid Graph Execution (PR #84)

`CompiledGraph.invoke` gains two optional keyword-only parameters:

| Param | Type | Effect |
|---|---|---|
| `preload` | `list[(namespace, key, state_key)]` | fetch each tuple from `store`, copy into `initial_state[state_key]` before traversal |
| `store` | `BaseStore` | required when `preload` is supplied |

```python
from agent_orchestrator.core.graph import StateGraph, START, END
from agent_orchestrator.core.store import InMemoryStore

store = InMemoryStore()
await store.aput(("glossary",), "db", {"def": "Database"})

result = await compiled.invoke(
    {"query": "what is db?"},
    preload=[(("glossary",), "db", "glossary")],
    store=store,
)
# Nodes see state["glossary"] == {"def": "Database"}
```

### Why this shape

The PR #84 proposal suggested a `mode="hybrid"` flag. A pre-load list is
cleaner: it's **typed**, **composable** (callers can preload N
namespaces at once), and **doesn't require a second API** next to
`invoke`. Missing keys are silently skipped so fallback to the graph's
own retrieval is natural.

Regression-safe: callers that never pass `preload` see identical
behaviour.

## Tests

21 new tests in `tests/test_phase3.py`. All green. Full suite: 1739
tests passing.
