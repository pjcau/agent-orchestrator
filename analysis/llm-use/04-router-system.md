# 04 - Router System

## Overview
The router is one of llm-use's most interesting features. It implements a 3-tier routing strategy to decide whether a task needs full orchestration or can be handled with a simple direct call.

## Routing Tiers

### Tier 1: LLM Router (lines 550-564)
When `--router <model>` is specified, a cheap/small model classifies the task:
```python
ROUTER_PROMPT = """Classify the task complexity...
Return JSON:
{
  "route": "simple" | "full",
  "reason": "short reason",
  "confidence": 0.0-1.0
}"""
```
- Uses a dedicated (cheap) model for routing decisions
- Falls back to heuristic if the LLM call fails
- Cost-efficient: uses small model (e.g., llama3.1:8b) to avoid expensive orchestrator calls

### Tier 2: Learned Router (lines 614-640)
Uses cosine similarity on token frequency vectors to match against past tasks:
```python
def _route_learned(self, task):
    examples = cache.get_router_examples(limit=200)
    vec = self._tf_vector(task)
    for ex_task, ex_mode, _created, _conf in examples:
        sim = self._cosine_sim(vec, self._tf_vector(ex_task))
        if sim > best[0]:
            best = (sim, ex_mode)
    if best[0] >= 0.35:  # similarity threshold
        route = "simple" if best[1] == "single" else "full"
```

Key details:
- TF vectors: token frequency (word count / total words)
- Cosine similarity threshold: 0.35 (quite low — may over-match)
- Stored in SQLite `router_examples` table (max 500 rows)
- Confidence: `min(0.85, 0.5 + similarity_score)`
- Every execution records its result for future learning

### Tier 3: Heuristic Router (lines 584-612)
Pattern-based fallback using regex rules from `router_rules.json`:
```python
# Signals for "full" (complex) routing:
- URL present in task
- Word count > 140
- Matches full_patterns (compare, research, sources, benchmark, etc.)
- Word count > 60 with no simple pattern matches

# Signals for "simple" routing:
- Short task with simple_patterns (explain, define, summarize, etc.)
```

## Router Data Management
- `router-reset` — Clears all learned examples
- `router-export --out file.json` — Export learned data (with timestamps + confidence)
- `router-import --in file.json` — Import learned data

## Routing Flow
```
Task → LLM Router (if configured)
         │
         ├── success → return route
         └── fail ──→ Heuristic Router
                        │
                        ├── Learned Router (check SQLite)
                        │     ├── match (sim ≥ 0.35) → return route
                        │     └── no match → continue
                        │
                        └── Pattern/length rules → return route
```

## Key Patterns
- 3-tier routing with graceful degradation
- Self-improving: every execution trains the learned router
- Configurable patterns via JSON file (no code changes needed)
- Router can use a separate, cheaper model than the orchestrator

## Relevance to Our Project
Our `TaskRouter` has 6 strategies but doesn't have a learned/ML component. The learned router concept (storing past routing decisions and using similarity matching) is novel and could be adopted. Our category-aware routing is more sophisticated for multi-domain work, but their self-improving approach is interesting for single-domain use.
