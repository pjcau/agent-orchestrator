---
sidebar_position: 2
title: "Phase 0: AWS Infrastructure"
---

# Phase 0 — AWS Infrastructure (ASAP)

**Goal:** Get the orchestrator running on AWS immediately. Everything else depends on this.
**Budget:** ~42 EUR/month

## 0A — AWS Setup (Week 1)

| Task | Priority | Detail |
|------|----------|--------|
| AWS EC2 t3.medium | CRITICAL | Deploy orchestrator + FastAPI + dashboard |
| Docker Compose on EC2 | CRITICAL | Same stack as local, with production config |
| Elastic IP + HTTPS | CRITICAL | Let's Encrypt, nginx reverse proxy |
| S3 storage | HIGH | Checkpoints, outputs, prompt templates |
| Security groups | CRITICAL | Restrict ports, SSH key-only, no open DB |
| `.env.production` | CRITICAL | API keys, budget caps, provider config |

## 0B — Monitoring Board (Week 2)

| Task | Priority | Detail |
|------|----------|--------|
| Prometheus setup | CRITICAL | Scrape orchestrator metrics (`/metrics` endpoint) |
| Grafana dashboards | CRITICAL | Agent activity, latency, token usage, cost per model |
| Node Exporter | HIGH | EC2 system metrics (CPU, RAM, disk, network) |
| Alert rules | HIGH | Cost threshold, error rate spike, agent stall detection |
| CloudWatch basics | MEDIUM | EC2 auto-recovery, uptime monitoring |

## Target Stack

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

## KPIs

- System live and reachable on AWS within 1 week
- Grafana dashboard showing real-time agent metrics within 2 weeks
- Monthly infra cost < 60 EUR
