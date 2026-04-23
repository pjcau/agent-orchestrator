# 04 — Protocols

How the agent *talks* to other parties. Two flavors in the diagram: Agent↔User and Agent↔Agent.

## 4a. Agent ↔ User

### Motivation
The agent often cannot finish without the human. It needs to:

- Ask for missing info
- Disambiguate vague goals
- Get approval on risky steps
- Offer multiple options
- Escalate when stuck

A good protocol separates **structured** clarification from chit-chat.

### Reference implementations

| Repo | Pattern |
|------|---------|
| [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) | `interrupt_before`/`interrupt_after` in graph, then `update_state` to resume |
| [humanlayer/humanlayer](https://github.com/humanlayer/humanlayer) | Approval APIs, Slack/Email escalation |
| [microsoft/autogen](https://github.com/microsoft/autogen) | `UserProxyAgent`, human input mode |
| [openai/openai-agents-python](https://github.com/openai/openai-agents-python) | Handoff to humans |

### Match — ✅ HAVE

- `core/clarification.py` — **5 typed request categories**: `missing_info`, `ambiguous`, `approach`, `risk`, `suggestion`
- Blocking mode (pause until response, 5-min timeout) + non-blocking mode
- Events: `clarification.request`, `clarification.response`, `clarification.timeout`
- `skills/clarification_skill.py` — agent-facing skill
- **SSE HITL**: `RunManager` in `dashboard/sse.py` — `POST /api/runs/{run_id}/resume` with `{"human_input": {...}}`
- Dashboard UI: option pill buttons, Approve/Reject buttons, SSE live stream

---

## 4b. Agent ↔ Agent

### Motivation
Multi-agent systems need a *protocol*: who can delegate to whom, how results return, how conflicts resolve, how trust is established. Ad-hoc calls become a tangled mess at scale.

### Reference implementations

| Repo | Pattern |
|------|---------|
| [microsoft/autogen](https://github.com/microsoft/autogen) | GroupChat, speaker selection, conversational protocol |
| [crewAIInc/crewAI](https://github.com/crewAIInc/crewAI) | Hierarchical + sequential crews, delegation |
| [OpenBMB/ChatDev](https://github.com/OpenBMB/ChatDev) | Role-based software company |
| [google/A2A](https://github.com/google/A2A) | **Agent2Agent Protocol** (emerging standard) |
| [modelcontextprotocol/modelcontextprotocol](https://github.com/modelcontextprotocol/modelcontextprotocol) | MCP — tool/resource exposure; can be used agent-to-agent |

### Match — ⚠️ PARTIAL

What we have:

- `core/cooperation.py` — inter-agent messaging: delegation, results, conflict resolution primitives
- `team-lead` agent orchestrates sub-agents (plan → sub-agents → review)
- Category-aware routing — team-lead picks agents by task domain
- **MCP bidirectional**: both server (expose agents as MCP tools) and client (connect to external MCP servers)

What is missing:

- **No adherence to A2A protocol** (Google's emerging standard for agent interop)
- **No formal negotiation primitives** (bid/ask, capability exchange)
- **No trust/capability advertisement** between agents
- **No cross-orchestrator federation**

### Gap to close

Two options depending on ambition:

**Tactical (small):** formalize the existing `cooperation.py` messages as a typed protocol with clear message types (`delegate`, `result`, `conflict`, `capability_query`, `capability_advertise`). Document it.

**Strategic (larger):** implement Google A2A adapter once the spec stabilizes — lets our agents interop with any A2A-compliant external agent. Wait-and-see recommended.

**Via MCP:** since we already have MCP client/server, we could expose our agents as MCP tools to *other* orchestrators — de facto agent-agent via MCP. Document this pattern.
