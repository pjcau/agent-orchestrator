# Roadmap — Agent Orchestrator

**Unified Technical & Business Plan**
*Updated: March 2026*

---

## Current State (near-MVP)

**What's built:**
- StateGraph engine (parallel execution, conditional routing, HITL, checkpointing)
- 5 LLM providers: Anthropic, OpenAI, Google, OpenRouter (free models), Local/Ollama
- LLM node factories (`llm_node`, `multi_provider_node`, `chat_node`)
- Interactive dashboard (FastAPI + WebSocket, streaming, model selector, graph visualization)
- Core abstractions: Provider, Agent, Skill, Orchestrator, Cooperation
- 23 agents across 5 categories (software-engineering, data-science, finance, marketing, tooling)
- SkillKit scout agent for marketplace skill discovery (15,000+ skills)
- Per-agent cost tracking, fallback chains, budget enforcement
- OrbStack/Docker infrastructure (dashboard, postgres, test, lint, format)
- 487+ tests, pre-commit hooks, CI pipeline

**What's missing for production:**
- No cloud deployment (runs only locally)
- No infrastructure monitoring (Prometheus, Grafana)
- No sandbox for testing agent output (preview before merge)
- No autonomous agent workflow validation
- No revenue model or public API

---

## URGENT: Phase 0 — AWS Infrastructure (ASAP)

**Goal:** Get the orchestrator running on AWS immediately. Everything else depends on this.
**Budget:** ~42 EUR/month

### 0A — AWS Setup (Week 1)

| Task | Priority | Detail |
|------|----------|--------|
| AWS EC2 t3.medium | CRITICAL | Deploy orchestrator + FastAPI + dashboard |
| Docker Compose on EC2 | CRITICAL | Same stack as local, with production config |
| Elastic IP + HTTPS | CRITICAL | Let's Encrypt, nginx reverse proxy |
| S3 storage | HIGH | Checkpoints, outputs, prompt templates |
| Security groups | CRITICAL | Restrict ports, SSH key-only, no open DB |
| `.env.production` | CRITICAL | API keys, budget caps, provider config |

### 0B — Monitoring Board (Week 2)

| Task | Priority | Detail |
|------|----------|--------|
| Prometheus setup | CRITICAL | Scrape orchestrator metrics (`/metrics` endpoint) |
| Grafana dashboards | CRITICAL | Agent activity, latency, token usage, cost per model |
| Node Exporter | HIGH | EC2 system metrics (CPU, RAM, disk, network) |
| Alert rules | HIGH | Cost threshold, error rate spike, agent stall detection |
| CloudWatch basics | MEDIUM | EC2 auto-recovery, uptime monitoring |

**Target stack:**

```
[EC2 t3.medium]
  ├── docker-compose.production.yml
  │   ├── dashboard (port 5005, nginx reverse proxy + HTTPS)
  │   ├── postgres (checkpoints, usage data)
  │   ├── prometheus (metrics collection)
  │   ├── grafana (visualization, alerts)
  │   └── node-exporter (system metrics)
  └── S3 (outputs, templates, backups)
```

### KPIs

- System live and reachable on AWS within 1 week
- Grafana dashboard showing real-time agent metrics within 2 weeks
- Monthly infra cost < 60 EUR

---

## Phase 1: Agent Autonomy Lab (Month 1)

**Goal:** Understand how agents actually perform, test their output safely, build confidence in autonomous execution.

### 1A — Agent Output Sandbox (Preview & Test)

| Task | Detail |
|------|--------|
| E2B or Docker sandbox | Isolated environment where agents run code before it touches real files |
| Output preview | Agent generates code/changes → preview diff → human approves or rejects |
| Auto-validation pipeline | Lint + test + security scan on every agent output before merge |
| Artifact staging | Agent output goes to a staging branch/directory, not directly to main |
| Dashboard integration | Show preview diffs in the dashboard UI, approve/reject with one click |

**Flow:**

```
Agent produces output
  → Sandbox executes (lint, test, security scan)
  → Preview in dashboard (diff view, test results)
  → Human approves → merge to main
  → Human rejects → agent retries with feedback
```

### 1B — Agile Team Experiment

| Task | Detail |
|------|--------|
| Sprint simulation | Give agents a backlog of tasks, see what they can deliver in a "sprint" |
| Team-lead as Scrum Master | Team-lead decomposes epics into stories, assigns to agents |
| Velocity tracking | Measure: tasks completed, quality score, rework rate |
| Autonomy levels | L1: human approves everything, L2: auto-merge if tests pass, L3: full autonomy |
| Retrospective data | What tasks agents handle well vs. where they fail |

### 1C — Agent Behavior Observability

| Task | Detail |
|------|--------|
| LangFuse integration | Trace every LLM call: prompt, response, latency, tokens, cost |
| Agent decision log | Why did team-lead route to agent X? Why did agent choose approach Y? |
| Failure analysis | Categorize failures: wrong approach, hallucination, tool misuse, timeout |
| Quality scoring | Auto-score agent output: does it compile? pass tests? follow conventions? |

### KPIs

- Sandbox preview working end-to-end
- First "sprint" completed with measurable velocity
- Agent success rate measured per category
- Clear data on which tasks agents handle autonomously vs. need human help

---

## Phase 2: Optimization & First Revenue (Month 2-4)

**Goal:** Reduce costs, add smart routing, acquire first paying users.
**Budget:** 42-100 EUR/month

### 2A — Cost Optimization

| Task | Detail |
|------|--------|
| Prompt caching | Cache repeated contexts (50-80% token savings) |
| Smart model routing | Route by task complexity: expensive models (complex) vs cheap (simple) |
| Context pruning | Send only relevant code snippets, not full files |
| Streaming + early cancel | Stop generation when first tokens indicate wrong approach |

### 2B — Product Features

| Task | Detail |
|------|--------|
| Multi-tenancy | User accounts, isolated workspaces, per-user rate limits |
| Usage analytics | Dashboard showing tokens used, cost, latency per user/graph |
| REST API docs | OpenAPI spec, usage examples, SDK stubs |
| Graph templates | Pre-built workflows users can customize (code review, analysis, Q&A) |
| Webhook integrations | GitHub, Slack, custom webhook for agent results |

### 2C — Business

| Task | Detail |
|------|--------|
| Pricing model | Define tiers: Free (limited), Pro (higher limits), Enterprise |
| Landing page | Simple static site explaining the product |
| Beta program | Onboard 5-10 beta users, collect feedback |
| Payment integration | Stripe for subscriptions |

### KPIs

- Monthly revenue > 100 EUR
- Cost per request optimized (prompt caching active)
- 5+ active beta users
- NPS positive

---

## Phase 3: Platform Maturity (Month 4-6)

**Goal:** Solidify the platform, expand provider support, prepare for scaling.
**Budget:** 100-300 EUR/month

### 3A — Framework Hardening

| Task | Detail |
|------|--------|
| Agent persistence | Save/resume long-running agent sessions across restarts |
| Skill marketplace | Users can publish and share custom skills |
| Graph versioning | Version and rollback graph definitions |
| Advanced observability | Distributed tracing, per-agent performance dashboards |

### 3B — Advanced Features

| Task | Detail |
|------|--------|
| Full agile team mode | Agents run sprints autonomously with human review at end |
| Conflict resolution | When agents modify same resources, auto-resolve or escalate |
| Human-in-the-loop flows | Production-grade approval steps in graphs |
| Fine-tuning pipeline design | Document the approach, prepare data collection |

### 3C — Provider Expansion

| Task | Detail |
|------|--------|
| Mistral provider | EU data residency option |
| DeepSeek provider | Budget coding alternative |
| Multi-provider node | Production routing across all providers based on cost/capability |

### KPIs

- Monthly revenue > 300 EUR
- Zero-downtime deploys
- Autonomous sprint velocity > 60% of human baseline
- 20+ active users

---

## Phase 4: Hybrid Scaling (Month 6+)

**Trigger:** Monthly revenue > 600 EUR for 2 consecutive months.
**Budget:** ~625 EUR/month

### 4A — GPU Infrastructure

| Task | Detail |
|------|--------|
| Vast.ai H200 setup | vLLM inference server for complex/fine-tuned tasks |
| Hybrid routing | EC2 orchestrator routes to Vast.ai (complex) or OpenRouter (burst/simple) |
| Model hosting | Self-host Qwen3 30B or fine-tuned variant on H200 |
| Auto-scaling | Scale between OpenRouter and self-hosted based on load |

**Architecture:**

```
[AWS EC2 -- Orchestrator + Dashboard + Prometheus + Grafana]
      |
      |--- Complex / fine-tuned -------> [Vast.ai H200 -- vLLM]
      |
      |--- Standard / burst traffic ---> [OpenRouter -- Qwen3 30B]
      |
      |--- Simple / economical --------> [OpenRouter -- Qwen3.5-Flash]
```

### 4B — Fine-Tuning

| Task | Detail |
|------|--------|
| Data pipeline | Collect and curate training data from production usage |
| Fine-tune Qwen3 30B | Domain-specific fine-tuning on H200 |
| A/B testing | Compare fine-tuned vs base model on real traffic |
| Model registry | Track model versions, metrics, rollback capability |

### 4C — Enterprise Features

| Task | Detail |
|------|--------|
| SSO / SAML | Enterprise authentication |
| Audit logging | Full audit trail of all agent actions |
| Data residency | Choose where data is processed (EU, US, self-hosted) |
| SLA guarantees | Defined uptime and latency commitments |
| On-prem option | Package for customer self-hosting |

### Cost Breakdown (Phase 4)

| Item | EUR/month |
|------|-----------|
| AWS EC2 + S3 + networking | 80 |
| Vast.ai H200 interruptible (252h/month inference) | 305 |
| Vast.ai H200 on-demand (108h/month fine-tuning) | 241 |
| OpenRouter (overflow/fallback) | 30 est. |
| **Total** | **~656** |

### KPIs

- Monthly revenue > 1,000 EUR
- Fine-tuned model outperforms base on domain tasks
- Self-hosted inference latency < cloud API
- Enterprise pipeline started

---

## v1.1 — LangGraph-Inspired Improvements

**Goal:** Adopt key patterns from LangGraph analysis to harden the orchestrator before scaling.
**Reference:** [`analysis/langgraph/`](../analysis/langgraph/) — 30-file deep analysis of LangGraph internals.
**Source files:** [28-comparison](../analysis/langgraph/28-comparison.md), [29-lessons-learned](../analysis/langgraph/29-lessons-learned.md)

### Sprint 1: State & Caching

| Task | Inspired By | Priority | Detail |
|------|------------|----------|--------|
| Channel-based state with reducers | [03-channels](../analysis/langgraph/03-channels.md) | High | Typed channels per state field. `LastValue` (single writer), `BinaryOperatorAggregate` (reducer fold), `Topic` (append). Solves concurrent agent writes to shared state. |
| Task-level result caching | [15-cache](../analysis/langgraph/15-cache.md) | High | Cache skill/node results by input hash. `CachePolicy` per skill. InMemory first, Redis later. Skip re-execution on cache hit. |
| Conformance test suite | [16-conformance-tests](../analysis/langgraph/16-conformance-tests.md) | High | Capability-based test harness for Provider and Checkpoint interfaces. Any new implementation runs against it automatically. |

### Sprint 2: HITL & Memory

| Task | Inspired By | Priority | Detail |
|------|------------|----------|--------|
| Interrupt/resume (HITL) | [19-human-in-the-loop](../analysis/langgraph/19-human-in-the-loop.md) | High | `interrupt()` pauses graph, persists state. `Command(resume=value)` continues. Interrupt is control flow, not an error. Required for production approval workflows. |
| Store abstraction (cross-agent memory) | [14-store](../analysis/langgraph/14-store.md) | High | Separate from checkpoints. `BaseStore` with `get/put/search/delete`. Namespace-based hierarchy. Cross-thread persistent memory (user profiles, knowledge base). |
| Skill middleware pattern | [18-tool-node](../analysis/langgraph/18-tool-node.md) | Medium | `SkillWrapper(request, next_fn) -> result`. Enables retry, caching, logging, authorization as composable middleware on skill execution. |

### Sprint 3: Persistence & Streaming

| Task | Inspired By | Priority | Detail |
|------|------------|----------|--------|
| Content-addressed checkpoint blobs | [13-checkpoint-postgres](../analysis/langgraph/13-checkpoint-postgres.md) | Medium | Split complex values into `checkpoint_blobs` table keyed by `(thread, ns, channel, version)`. `ON CONFLICT DO NOTHING` — same blob never re-written. Massive storage savings. |
| Anti-stall via managed values | [09-managed-values](../analysis/langgraph/09-managed-values.md) | Medium | Inject `RemainingSteps` / `IsLastStep` into agents. Graceful degradation instead of hard recursion limit errors. |
| Encrypted serialization | [11-checkpoint-serialization](../analysis/langgraph/11-checkpoint-serialization.md) | Low | Optional AES encryption for checkpoint blobs. Required for sensitive data at rest. |
| SSE streaming improvements | [27-streaming](../analysis/langgraph/27-streaming.md) | Low | Add `stream_mode` support (values/updates/messages/debug). SSE reconnection with `Last-Event-ID`. |

### v1.1 KPIs

- Channel-based state operational with reducer tests
- HITL interrupt/resume working end-to-end
- Conformance suite passing for all providers and checkpointers
- Task caching reducing redundant LLM calls by 30%+
- Store abstraction with namespace-based cross-agent memory

---

## Growth Opportunities (Suggestions)

These are high-potential features that could accelerate product growth, based on market trends in the AI agent orchestration space ($8.5B market in 2026).

### 1. Agent-as-a-Service API

Expose agents via a public REST API. Users send a task, get back structured results. No need to self-host. This is the fastest path to recurring revenue.

```
POST /api/v1/tasks
{ "task": "Review this PR for security issues", "context": { "repo": "...", "pr": 42 } }
→ { "result": "...", "agent": "backend", "cost": 0.003 }
```

### 2. Vertical Agent Packs (Niche Monetization)

Package domain-specific agent teams as paid add-ons:
- **SaaS Startup Pack**: backend + frontend + devops agents pre-configured for common stacks
- **Data Analytics Pack**: data-analyst + ml-engineer + bi-analyst for business intelligence
- **Compliance Pack**: compliance-officer + accountant for regulated industries

The market shows that **niche, domain-specific agent solutions** monetize far better than general-purpose frameworks.

### 3. SkillKit Marketplace Integration (Two-Way)

Not just consume skills from SkillKit — **publish** your agents' skills back. This creates a flywheel: more users → more skills → more users.

### 4. GitHub App / CI Integration

An agent that runs on every PR: reviews code, suggests improvements, checks for security issues. This is the most natural entry point for developer teams. Similar to what Codex and Claude Code do, but with your multi-agent approach.

### 5. Local-First + Cloud Burst Model

Sell the "privacy story": agents run locally by default (Ollama), burst to cloud only when needed. This is a strong differentiator vs. pure-cloud solutions like LangGraph Cloud.

---

## Financial Summary

```
Phase 0 (NOW)       Phase 1 (M1)        Phase 2 (M2-4)      Phase 3 (M4-6)      Phase 4 (M6+)
AWS + Monitoring    Agent Autonomy      Optimization        Platform Maturity    Hybrid Scaling
 42 EUR/mo           42 EUR/mo           42-100 EUR/mo       100-300 EUR/mo       625+ EUR/mo

EC2 + Prometheus    + Sandbox preview   + Smart routing     + Full agile mode    + Vast.ai H200
Grafana dashboards  + Sprint simulation + Prompt caching    + Skill marketplace  + Fine-tuning
HTTPS + nginx       + LangFuse traces   + Beta users        + Provider failover  + Enterprise
                    + Quality scoring   + Pricing model     + Observability      + SLA / SSO
```

### Break-Even Analysis

- **Phase 0-1:** Infrastructure investment, no revenue yet
- **Phase 2:** Profitable at ~5 paying users (10 EUR/month each)
- **Phase 3:** Profitable at ~15 paying users or 2-3 Pro users (100 EUR/month)
- **Phase 4:** Self-hosted GPU pays for itself when OpenRouter spend would exceed 545 EUR/month

---

## Risk Management

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| OpenRouter price increase | Medium | Medium | Multi-provider routing, DashScope fallback |
| OpenRouter rate limits | Low | High | Aggressive caching, tier upgrade |
| Vast.ai interruption (Phase 4) | Medium | Medium | OpenRouter as automatic fallback |
| Token costs out of control | Medium | High | Hard budget cap, alerts, prompt caching |
| EC2 downtime | Low | High | CloudWatch auto-recovery + Prometheus alerts |
| Low user adoption | Medium | High | Iterate on use cases, pivot pricing, open-source core |
| Provider API breaking changes | Medium | Medium | Provider abstraction layer isolates impact |
| Agent output quality too low | Medium | High | Sandbox + auto-validation catches bad output before merge |
| 40% agentic projects cancelled industry-wide | High | Medium | Focus on measurable ROI, start with proven use cases |

---

## Monitoring Stack

### Phase 0 (Immediate)

- **Prometheus**: scrape `/metrics` endpoint, agent execution metrics
- **Grafana**: real-time dashboards (agent activity, cost, latency, error rates)
- **Node Exporter**: EC2 system metrics (CPU, RAM, disk)
- **Alert Manager**: cost threshold, error spike, stall detection → Telegram/email

### Phase 1 (Month 1)

- **LangFuse**: LLM tracing, prompt versioning, evaluation scores
- **Agent decision log**: structured JSON log of routing decisions
- **Quality metrics**: compile rate, test pass rate, convention adherence per agent

### Phase 3-4 (Complete)

- Vast.ai dashboard: GPU utilization, instance uptime
- Custom analytics: per-user cost, graph execution stats, sprint velocity

---

## Immediate Next Steps (This Week)

1. **AWS EC2 setup** — t3.medium, security groups, Elastic IP, SSH key
2. **Production Docker config** — `docker-compose.production.yml` with nginx, HTTPS, Prometheus, Grafana
3. **Deploy to EC2** — push current codebase, verify dashboard works remotely
4. **Grafana dashboards** — agent metrics, cost tracking, system health
5. **Alert rules** — cost > threshold, error rate, agent stall → Telegram notification

---

*Document created: March 2026 — last updated: March 2026*
