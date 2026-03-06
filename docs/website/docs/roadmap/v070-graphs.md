---
sidebar_position: 5
title: "v0.7.0: Advanced Graphs"
---

# v0.7.0 — Advanced Graph Patterns

More powerful orchestration flows.

## Local (Ollama)

| ID | Feature | Files | Detail |
|----|---------|-------|--------|
| GRAPH-01 | Local-only templates | `templates/local/*.yaml` (new) | Graph patterns optimized for Ollama (smaller context, fewer nodes) |
| GRAPH-02 | Loop/retry nodes | `core/graph.py` | Graph-level retry: on node failure, re-run with auto model upgrade |
| GRAPH-03 | Dynamic graph construction | `core/graph.py`, `core/llm_nodes.py` | Local LLM decides which nodes to add at runtime based on task analysis |

## Cloud (OpenRouter)

| ID | Feature | Files | Detail |
|----|---------|-------|--------|
| GRAPH-04 | Cloud-augmented nodes | `core/graph.py` | Tag specific nodes to always run on cloud (e.g., final review step) |
| GRAPH-05 | Map-reduce with cloud fan-out | `core/graph.py` | Parallel cloud calls for high-throughput batch processing |
| GRAPH-06 | Long-context nodes | `core/router.py`, `core/graph.py` | Nodes requiring >128K context auto-routed to cloud models |

## Both

| ID | Feature | Files | Detail |
|----|---------|-------|--------|
| GRAPH-07 | Sub-graphs | `core/graph.py` | Nested graphs as nodes — compose complex workflows from reusable sub-graphs |
| GRAPH-08 | Graph templates (YAML) | `core/graph_template.py` (new), `templates/*.yaml` | Save/load reusable graph patterns from dashboard, declarative YAML format |
| GRAPH-09 | Graph versioning | `core/graph_template.py` | Track changes to graphs over time, rollback to previous versions |
| GRAPH-10 | Provider annotations | `core/graph.py` | Tag nodes with preferred provider: `local`, `cloud`, `any` |

## Implementation Notes

**GRAPH-07 (Sub-graphs)** — key extension to `StateGraph`:

```python
# A sub-graph is just a compiled graph used as a node
sub = StateGraph()
sub.add_node("step_a", node_a)
sub.add_node("step_b", node_b)
sub.add_edge(START, "step_a")
sub.add_edge("step_a", "step_b")
sub.add_edge("step_b", END)

# Use it as a node in the parent graph
parent = StateGraph()
parent.add_node("analysis", sub.compile())  # sub-graph as node
parent.add_node("report", report_node)
parent.add_edge(START, "analysis")
parent.add_edge("analysis", "report")
parent.add_edge("report", END)
```

**GRAPH-08 (YAML templates)**:

```yaml
# templates/code-review.yaml
name: code-review
description: Review code for bugs and quality
nodes:
  - name: analyze
    type: llm
    system: "Analyze this code for bugs and issues."
    provider: any
  - name: suggest
    type: llm
    system: "Suggest fixes for the issues found."
    provider: any
edges:
  - [START, analyze]
  - [analyze, suggest]
  - [suggest, END]
```
