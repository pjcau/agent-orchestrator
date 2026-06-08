# Phase 0 — Cost Report

## Infrastructure Cost (AWS)

**Region**: eu-west-1 (Ireland)
**Instance**: EC2 t3.medium Spot (2 vCPU, 4GB RAM)
**AWS account**: 617845011308
**Last audit**: 2026-06-05 (see `.github/workflows/aws-cost-audit.yml`)

### Monthly Breakdown

Two numbers matter each month: the **actual** of the last closed month and the
**Cost Explorer forecast** for the month in progress. Both come from the AWS
Cost Audit pipeline (see [Monitoring](#monitoring)).

#### May 2026 — actual (full month, measured from Cost Explorer)

| Service | Cost/month | Notes |
|---------|-----------:|-------|
| AWS Cost Explorer API | **$22.94** | Still the #1 line in May — the polling fix only took full effect in June (see below) |
| EC2 Spot compute (t3.medium) | $16.18 | Effective Spot price in eu-west-1 |
| Tax (EU VAT) | $11.98 | Unavoidable |
| EC2 — Other (100 GB gp3 EBS + transfer) | $10.57 | Root volume + data transfer |
| Amazon VPC (Public IPv4 charge since Feb 2024) | $3.72 | $0.005/h per public IPv4 (one EIP) |
| Route 53 (2 hosted zones) | $1.02 | agents-orchestrator.com + monitoring subdomain |
| S3 | $0.02 | Job archives only (~1 MB) |
| **AWS Total (May 2026 actual)** | **~$66.43** | |

> **Correction**: an earlier revision of this report listed May at ~$41 and
> claimed the Cost Explorer polling fix had already dropped that line to
> ~$1.20. The measured May actual is **$66.43**, with Cost Explorer still at
> **$22.94** — the fix's full effect lands in **June**, not May.

#### June 2026 — forecast (month in progress)

| Metric | Value |
|--------|------:|
| Month-to-date (2026-06-01 → 06-05) | $7.54 |
| Cost Explorer forecast (end of month) | **$47.90** |
| AWS Cost Explorer line (MTD, 4 days) | $1.18 → ~$9/mo run-rate |

The forecast firms up as daily data accumulates: it read $39.51 on 2026-06-01
and $47.90 on 2026-06-05.

### Month-over-Month vs the −30% target

| | Value |
|---|------:|
| May 2026 actual | $66.43 |
| Target (−30% MoM) | $46.50 |
| June 2026 forecast | $47.90 |
| **Change vs last month** | **−27.9%** |
| Distance from −30% target | +$1.40 (≈ on track) |

The expected ~30% month-over-month reduction **is materialising**. The single
biggest driver is the AWS Cost Explorer line collapsing from $22.94 (May) to a
~$9/mo run-rate (June) as the polling cadence fix finally takes effect.

### Cost Comparison

Anchored to the current June forecast (~$48) baseline:

| Configuration | Cost/month | vs Current |
|--------------|-----------:|-----------:|
| t3.medium On-Demand | ~$65 | +35 % |
| t3.medium Spot (current) | ~$48 | baseline |
| t3.small Spot | ~$38 | -21 % |
| Only 2h/day (start/stop) | ~$27 | -44 % |

### Cost Explorer polling fix

The original `REFRESH_INTERVAL = 3600` in `docker/aws-cost-exporter/exporter.py`
hit the Cost Explorer API **4 × per cycle** (monthly + today + yesterday +
forecast). At $0.01 per call, that's:

> 4 calls/cycle × 24 cycles/day × 30 days × $0.01 = **$28.80/month**.

The interval was raised to 86 400 s (24 h, current value in the exporter).
The May actual still shows $22.94 on this line because the change only became
effective for the full June cycle; the June MTD run-rate (~$9/mo) confirms the
expected drop. Note that **each manual `aws-cost-audit` workflow run also calls
Cost Explorer** (several `ce` calls per run), so bursts of manual audits inflate
this line — prefer the weekly scheduled run.

**Restart amplification fix (June 2026).** Even at a 24 h interval the exporter
re-queried CE on *every container start* — and worse, it fetched **twice** per
start (an eager `_fetch_costs()` in `main()` plus the refresh loop's immediate
first iteration = 8 paid calls/restart). On a heavy deploy day (many `compose
up`s, OOM restarts, failed-deploy retries) that turned a $1.20/mo line into
~$14/mo. Two changes removed it:

1. Dropped the redundant eager fetch in `main()` (8 → 4 calls per real fetch).
2. Added an **on-disk cache** (`COST_CACHE_PATH`, a `costcache:` volume in
   `docker-compose.prod.yml`) that survives restarts. A restart inside the
   24 h window reuses the cached numbers and issues **zero** CE calls; only the
   daily loop, once the cache ages out, does a real fetch and rewrites it.

Net effect: redeploys are now free on the Cost Explorer line. Covered by
`tests/test_aws_cost_exporter.py`.

### What's included in the running cost

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

All of the above run as containers on a single t3.medium Spot instance.

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
|----------|-------------:|
| AWS Infrastructure (June 2026 forecast) | ~$48 |
| AWS Infrastructure (May 2026 actual) | ~$66 |
| OpenRouter API (max, capped) | $60 |
| Domain (yearly, amortized) | ~$1 |
| **Total (max, June forecast)** | **~$109/month** |
| **Total (infra only, June forecast)** | **~$48/month** |

---

## Open Cost Items to Investigate

- **AWS Cost Explorer line** — $22.94 in May, the largest single line. The
  24 h polling cadence in the exporter should bring it to ~$9/mo (June run-rate
  confirms). Keep manual `aws-cost-audit` dispatches to a minimum, since each
  run also bills Cost Explorer calls.
- **Public IPv4 charge** — since Feb 2024 every public IPv4 (including attached
  EIPs) is billed $0.005/h (~$3.65/mo). The architecture uses one EIP
  (`52.212.89.114`). Dropping it (DNS pointing at the auto-assigned public IP,
  recreated on each spot rotation) or going IPv6-only would save ~$3.65/mo.
- **EBS sizing** — the root volume is 100 GB gp3 (~$9.3/mo) while S3 usage is
  ~1 MB and the containers are modest. Right-sizing the volume is a candidate
  saving.

> **Resolved**: the 30 GB orphan EBS volume flagged in the May 2026 audit is
> gone — the 2026-06-05 audit shows a single 100 GB gp3 volume `in-use` and no
> volumes in `available` state.

---

## Key Decisions That Reduced Costs

1. **Spot Instance** — saves 35-50 % vs On-Demand
2. **Single t3.medium** — runs all containers on one instance
3. **Self-hosted monitoring** — Grafana + Prometheus instead of paid services (Datadog ~$23/mo, New Relic ~$25/mo)
4. **Let's Encrypt** — free SSL instead of paid certificates
5. **OrbStack** — zero cost for local development (vs Docker Desktop paid plans)
6. **GitHub Actions** — free CI/CD for public repos
7. **Cost Explorer polling 1 h → 24 h** — biggest single lever; full effect visible in June 2026 (~$14/mo saved vs May)

## Monitoring

Real-time AWS cost tracking and the monthly forecast pipeline:

- **Weekly audit**: `.github/workflows/aws-cost-audit.yml` (Mondays 09:00 UTC + manual `workflow_dispatch`). Emits cost-by-service, EC2/EBS/EIP/S3 inventory, and the end-of-month forecast. Output goes to stdout, the GitHub Step Summary, and the `aws-cost-audit-report` artifact (30-day retention).
- **Real-time exporter**: `docker/aws-cost-exporter/exporter.py` exposes `aws_cost_monthly_forecast_usd` (and per-service/daily metrics) to Prometheus.
- **Grafana**: monitoring.agents-orchestrator.com → AWS Costs dashboard (Monthly MTD, Forecast, Daily per-service, Trend).
- **Data source**: AWS Cost Explorer API → custom exporter → Prometheus → Grafana.
</content>
