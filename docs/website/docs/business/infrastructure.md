---
sidebar_position: 3
title: Infrastructure
---

# Infrastructure Tiers

## Tier 1: Pure Cloud (Phase 1-2)

```
[Client / UI]
      |
      v
[AWS EC2 t3.medium -- Orchestrator]
  - StateGraph engine
  - API Gateway (FastAPI)
  - Dashboard (WebSocket)
      |
      v
[OpenRouter API]
  - Qwen3 30B A3B (complex tasks)
  - Qwen3.5-Flash (simple tasks)
      |
      v
[AWS S3] -- storage, checkpoints
```

**Cost:** ~42 EUR/month | **Setup:** hours | **Maintenance:** minimal

## Tier 2: Hybrid (Phase 4)

```
[AWS EC2 -- Orchestrator]
      |
      |--- Complex / fine-tuned -------> [Vast.ai H200 -- vLLM]
      |
      |--- Standard / burst -----------> [OpenRouter Qwen3]
      |
      |--- Simple / economical --------> [OpenRouter Flash]
```

**Cost:** ~625 EUR/month | **Setup:** days | **Maintenance:** moderate

## Tier 3: Full On-Prem (Future)

```
[Kubernetes Orchestrator]
      |
      |--- vLLM Cluster (GPU nodes)
      |--- Cloud API (frontier fallback)
      |--- Model Registry
```

**Cost:** high upfront, low marginal | **Best for:** enterprise, >20 devs

## Monitoring

### Phase 1-2
- AWS CloudWatch: EC2 metrics
- OpenRouter dashboard: token usage
- Custom alerts: Telegram/email on budget threshold

### Phase 3-4
- Grafana + Prometheus: full infra metrics
- LangFuse: LLM tracing, prompt versioning
- Vast.ai dashboard: GPU utilization
