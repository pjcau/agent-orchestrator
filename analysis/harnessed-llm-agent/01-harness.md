# 01 — Harness (core runtime)

## Motivation

The LLM alone is not an agent. An **agent** is a *loop* that repeatedly:

1. Reads current state + goal
2. Plans / picks next action
3. Calls a tool or the LLM
4. Updates state
5. Decides whether to continue or stop

The **harness** is the code that runs this loop reliably: checkpointing, retries, streaming, tool routing, stop conditions, cost tracking.

## Reference implementations

| Repo | What to learn from it |
|------|----------------------|
| [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) | StateGraph, channels, checkpointing, interrupts |
| [anthropics/claude-code](https://github.com/anthropics/claude-code) | Tool-use loop, agentic harness in production |
| [openai/openai-agents-python](https://github.com/openai/openai-agents-python) | Agents SDK, handoffs, guardrails |
| [crewAIInc/crewAI](https://github.com/crewAIInc/crewAI) | Role-based crew, task delegation |
| [stanford-oval/storm](https://github.com/stanford-oval/storm) | Multi-stage research harness |
| [microsoft/autogen](https://github.com/microsoft/autogen) | Conversable agents, group chat |

## Match — what we have

✅ **HAVE**

- `src/agent_orchestrator/core/orchestrator.py` — top-level coordinator
- `src/agent_orchestrator/core/agent.py` — agent base class, tool-use loop
- `src/agent_orchestrator/core/graph.py` — StateGraph engine (nodes, edges, parallel, HITL)
- `src/agent_orchestrator/core/llm_nodes.py` — LLM node factories
- `src/agent_orchestrator/core/reducers.py` — state reducers (append, merge, replace)
- `src/agent_orchestrator/core/channels.py` — typed channels (LastValue, Topic, Barrier, Ephemeral)
- `src/agent_orchestrator/core/checkpoint.py` + `checkpoint_postgres.py` — durable state
- `src/agent_orchestrator/client.py` — embedded Python client (no HTTP required)

## Gaps

None substantial. The harness is the project's strongest area.

Minor opportunities:

- **Streaming primitives parity** with LangGraph (`astream_events` vs our `astream`) — we already expose `StreamEvent`, consider aligning naming for interop.
- **Graph versioning** — we have `graph_templates.py` with versions; document migration semantics.
