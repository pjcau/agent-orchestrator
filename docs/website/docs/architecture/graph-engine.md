---
sidebar_position: 5
title: Graph Engine
---

# StateGraph Engine

The graph engine is the core of the orchestration system. Inspired by LangGraph but fully provider-agnostic.

```python
from agent_orchestrator.core.graph import END, START, StateGraph
from agent_orchestrator.core.llm_nodes import llm_node
from agent_orchestrator.providers.local import LocalProvider

provider = LocalProvider(model="qwen2.5-coder:7b-instruct")

analyze = llm_node(provider=provider, system="Analyze the code.",
                   prompt_key="code", output_key="analysis")
fix = llm_node(provider=provider, system="Fix the code.",
               prompt_template=lambda s: f"Analysis:\n{s['analysis']}\n\nCode:\n{s['code']}",
               output_key="fixed")

graph = StateGraph()
graph.add_node("analyze", analyze)
graph.add_node("fix", fix)
graph.add_edge(START, "analyze")
graph.add_edge("analyze", "fix")
graph.add_edge("fix", END)

result = await graph.compile().invoke({"code": "def avg(lst): return sum(lst) / len(lst)"})
```

## Features

- **Parallel execution** — independent nodes run via `asyncio.gather`
- **Conditional routing** — route to different nodes based on LLM output
- **Human-in-the-loop** — pause graph execution for user input, resume later
- **Checkpointing** — save/restore graph state (InMemory, SQLite, Postgres)
- **LLM node factories** — `llm_node()`, `multi_provider_node()`, `chat_node()`
- **Reducers** — control how state merges (append, replace, merge_dict, etc.)

## Graph Types (Dashboard)

| Type | Description |
|------|-------------|
| Auto | Classify input then route to appropriate sub-graph |
| Chat | Simple conversational graph |
| Code Review | Analyze code quality and suggest improvements |
| Analyze + Fix | Two-step: analyze then fix |
| Parallel Review | Multiple reviewers running in parallel |
