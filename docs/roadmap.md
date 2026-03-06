# Roadmap — Agent Orchestrator

**Unified Technical & Business Plan**
*v1.0 — March 2026*

---

## Current State (v0.1.0)

**What's built:**
- StateGraph engine (parallel execution, conditional routing, HITL, checkpointing)
- 4 LLM providers: Anthropic, OpenAI, Google, Local/Ollama
- LLM node factories (`llm_node`, `multi_provider_node`, `chat_node`)
- Interactive dashboard (FastAPI + WebSocket, model selector, graph visualization)
- Core abstractions: Provider, Agent, Skill, Orchestrator, Cooperation
- OrbStack/Docker infrastructure (dashboard, postgres, test, lint, format)
- 46+ tests, pre-commit hooks, CI pipeline

**What's missing for production:**
- No cloud deployment (runs only locally)
- No authentication / multi-tenancy
- No token cost tracking at runtime
- No prompt caching or smart routing in production
- No fine-tuning pipeline
- No revenue model or public API

---

## Phase 1: Production MVP (Month 1-2)

**Goal:** Deploy the orchestrator as a live service, validate with real users.
**Budget:** ~42 EUR/month

### 1A — Infrastructure Setup (Week 1-2)

| Task | Detail |
|------|--------|
| AWS EC2 t3.medium | Deploy orchestrator + FastAPI + dashboard |
| Docker Compose on EC2 | Same stack as local, with production config |
| OpenRouter integration | New provider: `OpenRouterProvider` (Qwen3 30B as default) |
| S3 storage | Prompt templates, outputs, checkpoints |
| Elastic IP + HTTPS | Let's Encrypt, nginx reverse proxy |
| Environment config | `.env.production` with API keys, budget caps |

### 1B — Core Features (Week 2-4)

| Task | Detail |
|------|--------|
| OpenRouter provider | Implement `OpenRouterProvider` in `src/agent_orchestrator/providers/` |
| Token cost tracker | Track input/output tokens and cost per request, per model |
| Budget alerts | Hard cap on monthly OpenRouter spend, alert via webhook |
| API authentication | API key auth for external access to `/api/prompt` |
| Health monitoring | CloudWatch basic metrics + `/health` endpoint |
| First production graph | End-to-end workflow: classify -> route -> execute -> respond |

### KPIs

- System live and stable on AWS
- Monthly cost < 60 EUR
- Average agent response latency < 5s
- At least 2 graph types working in production

---

## Phase 2: Optimization & First Revenue (Month 2-4)

**Goal:** Reduce costs, add smart routing, acquire first paying users.
**Budget:** 42-100 EUR/month

### 2A — Cost Optimization

| Task | Detail |
|------|--------|
| Prompt caching | Cache repeated contexts (50-80% token savings) |
| Smart model routing | Route by task complexity: Qwen3 30B (complex) vs Qwen3.5-Flash (simple) |
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
| Provider failover | Automatic fallback chain: primary -> secondary -> tertiary |
| Agent persistence | Save/resume long-running agent sessions across restarts |
| Skill marketplace | Users can publish and share custom skills |
| Graph versioning | Version and rollback graph definitions |
| Observability | Structured logging, distributed tracing (LangFuse or custom) |

### 3B — Advanced Features

| Task | Detail |
|------|--------|
| Multi-agent cooperation | Full delegation protocol: team-lead -> specialists |
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
- Provider failover tested and working
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
[AWS EC2 -- Orchestrator + Dashboard]
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

## Financial Summary

```
Phase 1 (M1-2)      Phase 2 (M2-4)      Phase 3 (M4-6)      Phase 4 (M6+)
MVP Go-Live          Optimization        Platform Maturity    Hybrid Scaling
 42 EUR/mo            42-100 EUR/mo       100-300 EUR/mo       625+ EUR/mo

AWS + OpenRouter     + Smart routing     + Multi-agent        + Vast.ai H200
Qwen3 30B            + Prompt caching     + Skill marketplace  + Fine-tuning
1 production graph   + Beta users         + Provider failover  + Enterprise
Token tracking       + Pricing model      + Observability      + SLA / SSO
```

### Break-Even Analysis

- **Phase 1-2:** Profitable at ~5 paying users (10 EUR/month each)
- **Phase 3:** Profitable at ~15 paying users or 2-3 Pro users (100 EUR/month)
- **Phase 4:** Self-hosted GPU pays for itself when OpenRouter spend would exceed 545 EUR/month

The real triggers for Phase 4 are **not** token cost savings but:
1. Fine-tuning on proprietary data (impossible with OpenRouter)
2. Total data privacy (sensitive data stays in-house)
3. Guaranteed latency without third-party dependency
4. Custom domain-specific model

---

## Risk Management

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| OpenRouter price increase | Medium | Medium | Multi-provider routing, DashScope fallback |
| OpenRouter rate limits | Low | High | Aggressive caching, tier upgrade |
| Vast.ai interruption (Phase 4) | Medium | Medium | OpenRouter as automatic fallback |
| Token costs out of control | Medium | High | Hard budget cap, alerts, prompt caching |
| EC2 downtime | Low | High | CloudWatch auto-recovery + Lambda |
| Low user adoption | Medium | High | Iterate on use cases, pivot pricing, open-source core |
| Provider API breaking changes | Medium | Medium | Provider abstraction layer isolates impact |

---

## Monitoring Stack

### Phase 1-2 (Minimal)

- AWS CloudWatch: EC2 metrics (CPU, RAM, uptime)
- OpenRouter dashboard: token usage, cost per model
- Custom webhook alerts: daily cost > threshold -> Telegram/email
- Dashboard built-in: agent cards with tokens/cost

### Phase 3-4 (Complete)

- Grafana + Prometheus: full infrastructure metrics
- LangFuse: LLM tracing, prompt versioning, evaluation
- Vast.ai dashboard: GPU utilization, instance uptime
- Custom analytics: per-user cost, graph execution stats

---

## Immediate Next Steps (This Week)

1. **Implement `OpenRouterProvider`** — new provider in `src/agent_orchestrator/providers/openrouter.py`
2. **Add token cost tracking** — instrument providers to log input/output tokens + cost
3. **Production Docker config** — `docker-compose.production.yml` with nginx, HTTPS
4. **AWS setup** — EC2 t3.medium, security groups, Elastic IP
5. **First deploy** — push current codebase to EC2, verify dashboard works remotely

---

*Document created: March 2026 -- update at each phase completion.*
