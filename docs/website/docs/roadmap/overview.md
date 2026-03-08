---
sidebar_position: 1
title: Overview
---

# Roadmap

## Philosophy

AWS infrastructure and monitoring come **first** — the product must be live and observable before iterating on features. Then validate agent autonomy with real sprints and sandboxed output preview. Only invest in scaling when revenue justifies it.

## Current State (near-MVP)

**Done:**
- Core: Provider, Agent, Skill, Orchestrator, Cooperation, StateGraph engine
- 5 providers: Anthropic, OpenAI, Google, Ollama, OpenRouter (free models + fallback chains)
- 23 agents across 5 categories (software-engineering, data-science, finance, marketing, tooling)
- SkillKit scout agent for marketplace skill discovery (15,000+ skills)
- Dashboard: streaming, multi-turn chat, presets, file context, agent execution, cost tracking
- Checkpointing: InMemory, SQLite, PostgreSQL
- Docker/OrbStack: dashboard, postgres, test, lint, format
- 487+ tests

**What's missing for production:**
- Cloud deployment (AWS)
- Infrastructure monitoring (Prometheus + Grafana)
- Agent output sandbox (preview before merge)
- Autonomous agent workflow validation

## Phases

| Phase | Focus | Timeline | Page |
|-------|-------|----------|------|
| **Phase 0** | AWS Infrastructure + Prometheus/Grafana | **NOW** | [Details](./overview) |
| **Phase 1** | Agent Autonomy Lab (sandbox, sprints, observability) | Month 1 | [Details](./overview) |
| **Phase 2** | Optimization & First Revenue | Month 2-4 | [Details](./v050-routing) |
| **Phase 3** | Platform Maturity | Month 4-6 | [Details](./v060-hardening) |
| **Phase 4** | Hybrid GPU Scaling | Month 6+ | [Details](./post-mvp-scaling) |

## MVP Versions (in progress)

| Version | Focus | Page |
|---------|-------|------|
| **v0.4.0** | Multi-Agent Cooperation | [Details](./v040-cooperation) |
| **v0.5.0** | Smart Routing & Cost Optimization | [Details](./v050-routing) |
| **v0.6.0** | Production Hardening | [Details](./v060-hardening) |
| **v0.7.0** | Advanced Graph Patterns | [Details](./v070-graphs) |
| **v0.8.0** | External Integrations | [Details](./v080-integrations) |
| **v1.0.0** | General Availability | [Details](./v100-ga) |

## Post-MVP: Improvements & Scaling

| Version | Trigger | Focus | Page |
|---------|---------|-------|------|
| **v1.1** | LangGraph analysis | Channels, HITL, Store, caching, conformance | [Details](./v110-langgraph-improvements) |
| **Scaling** | Revenue > 600 EUR/mo x 2 months | GPU infra, fine-tuning, enterprise | [Details](./post-mvp-scaling) |

```
NOW            Month 1          Month 2-4        Month 4-6        Month 6+
 |               |                 |                |                |
 |  Phase 0      |    Phase 1      |   Phase 2      |   Phase 3      |  Phase 4
 |──┬─────────────┬────────────────┬────────────────┬────────────────┬──────
    |             |                |                |                |
  AWS+Grafana  Sandbox+Sprint   Revenue+Optimize  Platform        GPU Scaling
  Prometheus   Agent Autonomy   Beta Users        Full Agile      Fine-tuning
  Monitoring   LangFuse         Pricing           Marketplace     Enterprise
```
