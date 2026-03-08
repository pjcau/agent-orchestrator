---
sidebar_position: 7
title: Monitoring Stack
---

# Monitoring Stack

## Phase 0 (Immediate)

- **Prometheus**: scrape `/metrics` endpoint, agent execution metrics
- **Grafana**: real-time dashboards (agent activity, cost, latency, error rates)
- **Node Exporter**: EC2 system metrics (CPU, RAM, disk)
- **Alert Manager**: cost threshold, error spike, stall detection → Telegram/email

## Phase 1 (Month 1)

- **LangFuse**: LLM tracing, prompt versioning, evaluation scores
- **Agent decision log**: structured JSON log of routing decisions
- **Quality metrics**: compile rate, test pass rate, convention adherence per agent

## Phase 3-4 (Complete)

- Vast.ai dashboard: GPU utilization, instance uptime
- Custom analytics: per-user cost, graph execution stats, sprint velocity
