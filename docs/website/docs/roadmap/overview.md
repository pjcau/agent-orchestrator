---
sidebar_position: 1
title: Overview
---

# Roadmap

## Philosophy

The MVP (v1.0) must be a **complete, usable product** before investing in infrastructure scaling. Everything from v0.4.0 through v1.0.0 is the MVP. Only after v1.0 ships — and generates enough revenue to justify it — do we consider spending beyond ~100 EUR/month.

## Current State (v0.3.0)

**Done:**
- Core: Provider, Agent, Skill, Orchestrator, Cooperation, StateGraph engine
- 5 providers: Anthropic, OpenAI, Google, Ollama, OpenRouter (free models + 429 fallback)
- Dashboard: streaming, multi-turn chat, presets, file context, model comparison, agent execution
- Team graph: team-lead delegates to parallel sub-agents
- Checkpointing: InMemory, SQLite, PostgreSQL
- Docker/OrbStack: dashboard, postgres, test, lint, format
- 436+ tests

**Incomplete from v0.2.0/v0.3.0:**
- Conversation memory persistence
- Code execution (sandboxed)
- Prompt templates (save/reuse)
- Token budget per task (cloud safeguard)

## MVP = v0.4.0 → v1.0.0

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
| **Scaling** | Revenue > 600 EUR/mo × 2 months | GPU infra, fine-tuning, enterprise | [Details](./post-mvp-scaling) |

```
v0.3.0                                   v1.0.0 (MVP)    v1.1         Post-MVP
    |                                         |            |              |
    |  v0.4  v0.5  v0.6  v0.7  v0.8  v1.0   |   v1.1     |              |
    |──┬─────┬─────┬─────┬─────┬─────┬───────|────┬───────|              |
       |     |     |     |     |     |       GA   |    LangGraph      GPU scaling
     Coop  Route  Prod  Graph  Integ  GA         HITL   patterns      625+ EUR/mo
                                              Channels
                                              Caching
```
