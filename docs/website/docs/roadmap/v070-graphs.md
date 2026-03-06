---
sidebar_position: 5
title: "v0.7.0: Advanced Graphs"
---

# v0.7.0 — Advanced Graph Patterns ✅

More powerful orchestration flows built on top of the StateGraph engine.

## Status: Complete

| Feature | Status | Module |
|---------|--------|--------|
| Sub-graphs (nested graphs as nodes) | ✅ | `core/graph_patterns.py` |
| Loop/retry nodes with provider upgrade | ✅ | `core/graph_patterns.py` |
| Map-reduce with bounded concurrency | ✅ | `core/graph_patterns.py` |
| Provider annotations (local/cloud/any) | ✅ | `core/graph_patterns.py` |
| Long-context node routing | ✅ | `core/graph_patterns.py` |
| Graph template store (versioned) | ✅ | `core/graph_templates.py` |
| JSON serialisation (export/import) | ✅ | `core/graph_templates.py` |
| Build graph from template | ✅ | `core/graph_templates.py` |
| 40 tests | ✅ | `tests/test_graph_patterns.py` |

## Key APIs

### SubGraphNode

Wrap a compiled graph as a callable node with input/output mapping:

```python
from agent_orchestrator.core.graph import StateGraph, START, END
from agent_orchestrator.core.graph_patterns import SubGraphNode

sub = StateGraph()
sub.add_node("double", lambda s: {"value": s["value"] * 2})
sub.add_edge(START, "double")
sub.add_edge("double", END)

node = SubGraphNode(
    sub.compile(),
    input_mapping={"parent_val": "value"},
    output_mapping={"value": "result"},
)
result = await node({"parent_val": 5})
# result == {"result": 10}
```

### retry_node

Wrap any node with retry logic and optional provider upgrade chain:

```python
from agent_orchestrator.core.graph_patterns import retry_node

wrapped = retry_node(flaky_node, max_retries=3, upgrade_providers=[local, cloud])
result = await wrapped(state)
# Retries up to 3 times, injecting next provider on each retry
```

### map_reduce_node

Parallel map with semaphore-bounded concurrency:

```python
from agent_orchestrator.core.graph_patterns import map_reduce_node

wrapped = map_reduce_node(
    map_func=square_node,
    reduce_func=lambda results: {"total": sum(r["val"] for r in results)},
    items_key="numbers",
    max_concurrency=5,
)
result = await wrapped({"numbers": [1, 2, 3, 4, 5]})
```

### GraphTemplateStore

Versioned template store with JSON serialisation:

```python
from agent_orchestrator.core.graph_templates import (
    GraphTemplateStore, GraphTemplate, NodeTemplate, EdgeTemplate,
)

store = GraphTemplateStore()
store.save(GraphTemplate(
    name="review",
    description="Code review pipeline",
    version=1,
    nodes=[
        NodeTemplate("analyze", "llm", {"system": "Analyze code.", "provider": "claude"}),
        NodeTemplate("suggest", "custom", {"function_name": "suggest_fixes"}),
    ],
    edges=[
        EdgeTemplate("__start__", "analyze"),
        EdgeTemplate("analyze", "suggest"),
        EdgeTemplate("suggest", "__end__"),
    ],
    created_at=time.time(),
))

# Build a runnable graph
graph = store.build_graph("review", providers={"claude": my_provider}, node_registry={"suggest_fixes": fn})
result = await graph.compile().invoke({"input": "def foo(): ..."})
```

## Not Yet Implemented

- Local-only graph templates (Ollama-optimised patterns)
- Dynamic graph construction (LLM decides nodes at runtime)
