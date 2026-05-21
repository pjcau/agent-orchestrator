# Phase 0 — Cost Report

## Infrastructure Cost (AWS)

**Region**: eu-west-1 (Ireland)
**Instance**: EC2 t3.medium Spot (2 vCPU, 4GB RAM)
**Last audit**: 2026-05-21 (see `.github/workflows/aws-cost-audit.yml`)

### Monthly Breakdown (measured from May 2026 Cost Explorer data)

| Service | Cost/month | Notes |
|---------|-----------|-------|
| AWS Cost Explorer API | ~$1.20 (was $29) | Polling cadence reduced from 1 h to 24 h — see "Cost Explorer polling fix" below |
| EC2 Spot compute (t3.medium) | ~$15.00 | Effective Spot price in eu-west-1 |
| EC2 — Other (130 GB EBS + transfer) | ~$11.00 | Root volume + 30 GB orphan volume to investigate |
| Tax (EU VAT 22 %) | ~$9.00 | Unavoidable |
| Amazon VPC (Public IPv4 charge since Feb 2024) | ~$3.65 | $0.005/h per public IPv4 |
| Route 53 (2 hosted zones) | $1.00 | agents-orchestrator.com + monitoring subdomain |
| S3 | < $0.10 | Job archives only |
| **AWS Total** | **~$41/month** (steady state, after polling fix) | Down from ~$73/mo forecast |

### Cost Comparison

| Configuration | Cost/month | vs Current |
|--------------|-----------|------------|
| t3.medium On-Demand | ~$48 | +17 % |
| t3.medium Spot (current) | ~$41 | baseline |
| t3.small Spot | ~$31 | -24 % |
| Only 2h/day (start/stop) | ~$20 | -51 % |

### Cost Explorer polling fix

The original `REFRESH_INTERVAL = 3600` in `docker/aws-cost-exporter/exporter.py`
hit the Cost Explorer API **4 × per cycle** (monthly + today + yesterday +
forecast). At $0.01 per call, that's:

> 4 calls/cycle × 24 cycles/day × 30 days × $0.01 = **$28.80/month** (~40 % of the bill).

Since Cost Explorer data only refreshes ~once per day, the interval was raised
to 86 400 s (24 h), expected savings ~$28/mo.

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
| AWS Infrastructure (after polling fix) | ~$41 |
| OpenRouter API (max, capped) | $60 |
| Domain (yearly, amortized) | ~$1 |
| **Total (max)** | **~$102/month** |
| **Total (infra only)** | **~$41/month** |

---

## Open Cost Items to Investigate

- **Orphan EC2 instance** — the May 2026 audit found a 30 GB EBS volume
  (`vol-089227b8b9b1c6cfc`) created on 2025-05-20 still `in-use` by an
  instance ID (`i-01f4af542f7c8c38d`) that does not appear in the running
  inventory. Confirm whether the instance is stopped/forgotten and either
  start it or terminate + delete its volume. Estimated waste if orphan:
  ~$3-5/mo (30 GB × $0.0952 + spot capacity reservation).
- **Public IPv4 charge** — since Feb 2024 every public IPv4 (including
  attached EIPs) is billed $0.005/h. The current architecture uses one EIP;
  switching to IPv6-only or removing the EIP entirely (with DNS pointing at
  the auto-assigned public IPv4 of the spot instance, recreated on each
  rotation) would save ~$3.65/mo.

---

## Key Decisions That Reduced Costs

1. **Spot Instance** — saves 50-60 % vs On-Demand
2. **Single t3.medium** — runs all 9 containers on one instance
3. **Self-hosted monitoring** — Grafana + Prometheus instead of paid services (Datadog ~$23/mo, New Relic ~$25/mo)
4. **Let's Encrypt** — free SSL instead of paid certificates
5. **OrbStack** — zero cost for local development (vs Docker Desktop paid plans)
6. **GitHub Actions** — free CI/CD for public repos
7. **Cost Explorer polling 1 h → 24 h** — ~$28/mo saved (single biggest line on the bill in May 2026)

## Monitoring

Real-time AWS cost tracking available at:
- **Grafana**: monitoring.agents-orchestrator.com → AWS Costs dashboard
- **Metrics**: Monthly MTD, Forecast, Daily per-service, Trend
- **Data source**: AWS Cost Explorer API → custom exporter → Prometheus → Grafana
