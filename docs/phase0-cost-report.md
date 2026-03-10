# Phase 0 — Cost Report

## Infrastructure Cost (AWS)

**Region**: eu-west-1 (Ireland)
**Instance**: EC2 t3.medium Spot (2 vCPU, 4GB RAM)

### Monthly Breakdown (measured from production data)

| Service | Cost/month | Notes |
|---------|-----------|-------|
| EC2 Spot compute | $1.20 | t3.medium ~$0.04/h effective |
| EBS volume (8GB gp3) | $0.64 | Persistent storage |
| Elastic IP / Network | $0.30 | Static IP allocation |
| Route 53 hosted zone | $0.50 | agents-orchestrator.com + monitoring subdomain |
| Route 53 DNS queries | $0.01 | Minimal traffic |
| Data transfer | $0.05 | Low bandwidth usage |
| **AWS Total** | **$2.70/month** | |

### Cost Comparison

| Configuration | Cost/month | vs Current |
|--------------|-----------|------------|
| t3.medium On-Demand | $39.00 | +1,344% |
| t3.medium Spot (current) | $2.70 | baseline |
| t3.small Spot | ~$1.80 | -33% |
| Only 2h/day (start/stop) | ~$1.60 | -41% |

### What's Included in $2.70/month

- Dashboard (FastAPI + HTTPS) at agents-orchestrator.com
- PostgreSQL 16 (persistent data)
- Redis 7 (session cache)
- Nginx reverse proxy (SSL termination, rate limiting, security headers)
- Prometheus (metrics, 30-day retention)
- Grafana at monitoring.agents-orchestrator.com (7 dashboards)
- Node Exporter + cAdvisor (host/container metrics)
- AWS Cost Exporter (real-time billing dashboard)
- Certbot (auto-renewing Let's Encrypt SSL)
- GitHub OAuth2 authentication
- CI/CD auto-deploy pipeline

---

## LLM API Cost (OpenRouter)

**Separate from infrastructure** — capped at $2/day by the user.

| Metric | Value |
|--------|-------|
| Daily cap | $2.00 |
| Monthly max | $60.00 |
| Model used | qwen/qwen3.5-flash-02-23 (CI), claude CLI (local) |
| Usage | Research scout nightly runs + dashboard prompt routing |

---

## Total Cost Summary

| Category | Monthly Cost |
|----------|-------------|
| AWS Infrastructure | $2.70 |
| OpenRouter API (max) | $60.00 |
| Domain (yearly, amortized) | ~$1.00 |
| **Total (max)** | **~$63.70/month** |
| **Total (infra only)** | **~$2.70/month** |

---

## Key Decisions That Reduced Costs

1. **Spot Instance** — saved 93% vs On-Demand ($39 → $2.70)
2. **Single t3.medium** — runs all 9 containers on one instance
3. **Self-hosted monitoring** — Grafana + Prometheus instead of paid services (Datadog ~$23/mo, New Relic ~$25/mo)
4. **Let's Encrypt** — free SSL instead of paid certificates
5. **OrbStack** — zero cost for local development (vs Docker Desktop paid plans)
6. **GitHub Actions** — free CI/CD for public repos

## Monitoring

Real-time AWS cost tracking available at:
- **Grafana**: monitoring.agents-orchestrator.com → AWS Costs dashboard
- **Metrics**: Monthly MTD, Forecast, Daily per-service, Trend
- **Data source**: AWS Cost Explorer API → custom exporter → Prometheus → Grafana
