# 02 — Skills

In the diagram, **Skills** decomposes into three sub-concepts.

## 2a. Operational Procedure

### Motivation
Reusable, composable procedures — don't re-prompt the LLM for a workflow it has already learned. Encapsulate "how to do X" as a callable unit.

### Reference implementations

| Repo | Pattern |
|------|---------|
| [anthropics/anthropic-cookbook](https://github.com/anthropics/anthropic-cookbook) | Recipe-style skills |
| [crewAIInc/crewAI](https://github.com/crewAIInc/crewAI) | Task + Agent + Tool composition |
| [langchain-ai/langchain](https://github.com/langchain-ai/langchain) | `Runnable` / LCEL |
| [stanfordnlp/dspy](https://github.com/stanfordnlp/dspy) | `Module`, `Signature` — compilable procedures |

### Match — ✅ HAVE

- `src/agent_orchestrator/core/skill.py` — `SkillRegistry`, middleware chain (retry, logging, timeout, cache)
- `src/agent_orchestrator/core/graph_templates.py` — versioned graph templates with JSON serialization
- `src/agent_orchestrator/core/graph_patterns.py` — sub-graphs, retry, loop, map-reduce
- 19 skills under `src/agent_orchestrator/skills/` + CLI skills under `.claude/skills/`
- **Progressive skill loading**: compact `SkillSummary` in system prompts + on-demand `load_skill` tool

---

## 2b. Normative Constraints

### Motivation
What the agent *must not* do — policy, safety, PII filtering, content moderation, regulatory constraints. Guardrails are the negative space of skills.

### Reference implementations

| Repo | Pattern |
|------|---------|
| [guardrails-ai/guardrails](https://github.com/guardrails-ai/guardrails) | Declarative validators, structured output guards |
| [NVIDIA/NeMo-Guardrails](https://github.com/NVIDIA/NeMo-Guardrails) | Dialog flows, Colang policy DSL |
| [openai/openai-agents-python](https://github.com/openai/openai-agents-python) | Built-in input/output guardrails |
| [protectai/llm-guard](https://github.com/protectai/llm-guard) | Scanner library (prompt injection, PII) |
| [deepset-ai/haystack](https://github.com/deepset-ai/haystack) | Content safety components |

### Match — ⚠️ PARTIAL

What we have today:

- `src/agent_orchestrator/core/audit.py` — structured audit log (11 event types) — *post-hoc*, not preventive
- `src/agent_orchestrator/core/loop_detection.py` — warns at 3 repeats, hard stops at 5
- `src/agent_orchestrator/core/memory_filter.py` — sanitizes session file paths before persistence
- `src/agent_orchestrator/skills/sandboxed_shell.py` — execution isolation

What is missing:

- **No pre/post validation layer** on LLM input/output
- **No policy DSL** (e.g. "agent X cannot call skill Y on production")
- **No PII / secret scanners** at the boundary
- **No prompt-injection detectors**

### Gap to close

Add `core/guardrails.py`:

```python
class Guardrail(ABC):
    async def check_input(self, messages: list[Message]) -> GuardrailResult: ...
    async def check_output(self, response: Message) -> GuardrailResult: ...

class GuardrailManager:
    def register(self, guard: Guardrail, scope: Literal["input", "output", "both"]): ...
    async def run_input(self, messages) -> list[GuardrailResult]: ...
    async def run_output(self, response) -> list[GuardrailResult]: ...
```

Integrate in `Agent.execute()` before LLM call and before returning the response.

---

## 2c. Decision Heuristics

### Motivation
Routing intelligence — *which* model for *which* task. Cost-vs-quality, latency-vs-cost, local-first privacy, complexity-aware.

### Reference implementations

| Repo | Pattern |
|------|---------|
| [BerriAI/litellm](https://github.com/BerriAI/litellm) | Router with fallbacks, load balancing |
| [Portkey-AI/gateway](https://github.com/Portkey-AI/gateway) | Multi-provider gateway, conditional routing |
| [lm-sys/RouteLLM](https://github.com/lm-sys/RouteLLM) | Learned router (cheap vs strong model) |
| [withmartian/martian-routing](https://github.com/withmartian) | Commercial model router |

### Match — ✅ HAVE

- `src/agent_orchestrator/core/router.py` — 6 strategies: local-first, cost-optimized, complexity-based, latency-optimized, quality-first, round-robin
- `src/agent_orchestrator/core/provider_presets.py` — one-click presets (local_only, cloud_only, hybrid, high_quality)
- `src/agent_orchestrator/core/health.py` — auto-failover on provider degradation
- `src/agent_orchestrator/core/rate_limiter.py` — per-provider rate limiting
- Category-aware routing in `dashboard/agent_runner.py` + `dashboard/graphs.py` (finance / data-science / marketing / software)

### Gaps

- **No learned routing** (RouteLLM-style) — heuristics only
- **No A/B testing** of routing decisions against eval scores (links to Evaluator gap)
