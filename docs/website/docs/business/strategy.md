---
sidebar_position: 1
title: Strategy
---

# AI Infrastructure Strategy

**Budget:** from ~42 EUR/month to ~625 EUR/month (scaling with revenue)

## Executive Summary

Build and scale a multi-agent orchestration system based on open-source LLMs, starting with a conservative low-cost setup on AWS + OpenRouter, evolving toward a hybrid self-hosted infrastructure as revenue grows.

| Metric | Value |
|--------|-------|
| Starting budget | ~42 EUR/month |
| Scaling threshold | 600 EUR/month revenue |
| Final target | Hybrid AWS + Vast.ai H200 at ~625 EUR/month |

## Options Evaluated

| Solution | Cost/month | Pros | Cons |
|---|---|---|---|
| Claude Max $200 | ~185 EUR | Zero setup, Opus 4.6 | Rate limits, no fine-tuning |
| AWS + Vast.ai H200 (12h/day) | ~625 EUR | Private, fine-tuning, unlimited | High fixed cost |
| **AWS + OpenRouter Qwen3 30B** | **~42 EUR** | Minimum cost, zero GPU infra | Pay-per-token, no fine-tuning |

## Key Decisions

| Decision | Choice | Reason |
|---|---|---|
| LLM Model | Qwen3 30B A3B | Best quality/cost for agents, open-weight |
| Orchestrator | StateGraph (custom) | Stateful workflows, provider-agnostic |
| Initial provider | OpenRouter | Single endpoint, multi-model, zero infra |
| Cloud infra | AWS | Reliability, ecosystem |
| GPU (Phase 4) | Vast.ai H200 | Lowest market price, interruptible OK |

## Scaling Trigger

Self-hosted GPU becomes justified **not** for token cost savings, but for:

1. **Fine-tuning** on proprietary data (impossible with OpenRouter)
2. **Total privacy** (sensitive data stays in-house)
3. **Guaranteed latency** without third-party dependency
4. **Custom domain-specific model**
