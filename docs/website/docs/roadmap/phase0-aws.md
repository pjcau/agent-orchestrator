---
sidebar_position: 2
title: "Phase 0: AWS Infrastructure + Auth"
---

# Phase 0 — AWS Infrastructure + Auth (ASAP)

**Goal:** EC2 up, HTTPS working, OAuth2 active, first agent reachable remotely.
**Budget:** ~42 EUR/month
**Duration:** 2 sprints (2 weeks)

> **IaC:** Terraform · **CI/CD:** GitHub Actions · **Cloud:** AWS EC2 + Docker Compose
> **Auth:** OAuth2 (Google/GitHub) + JWT session cookies · **State:** S3 + DynamoDB lock

## Architecture Target

```
Internet
   │
   ▼
Route53 (DNS) ──► ACM (SSL cert)
   │
   ▼
EC2 t3.medium (Elastic IP + Security Group)
   │
   Docker Compose
   ├── Nginx (reverse proxy + SSL termination)
   │     └── /          → Dashboard (FastAPI + static UI)
   │     └── /api/      → FastAPI (orchestrator API)
   │     └── /auth/     → OAuth2 callback handler
   ├── FastAPI + StateGraph (multi-agent orchestrator)
   ├── PostgreSQL (checkpoints, usage data)
   ├── Redis (semantic cache + session store)
   ├── Prometheus (metrics collection)
   ├── Grafana (visualization, alerts)
   └── Node Exporter (system metrics)
   │
   ▼
OpenRouter API (free models + fallback chains)
```

## Sprint 1 — Terraform: Bootstrap AWS Infrastructure

### Step 1.1 — Terraform Backend (S3 + DynamoDB)

One-time manual bootstrap for state management.

### Step 1.2 — VPC + EC2 + Security Group

Terraform modules: `terraform/modules/ec2/`, `terraform/modules/networking/`, `terraform/modules/iam/`

### Step 1.3 — GitHub Actions: Terraform Pipeline

`.github/workflows/terraform.yml` — plan on PR, apply on merge to main.

**Deliverables:**
- [ ] S3 bucket + DynamoDB created
- [ ] `terraform apply` creates EC2, SG, EIP
- [ ] SSH to EC2 working, Docker installed
- [ ] GitHub Actions runs plan/apply on push

## Sprint 2 — Auth OAuth2 + App Deploy + Monitoring

### Step 2.1 — OAuth2 Authentication

OAuth2 flow with JWT session cookies (authlib + PyJWT):

```
Browser → GET /auth/google → redirect to Google
Google  → GET /auth/google/callback?code=xxx
FastAPI → exchange code → get user info → create JWT session cookie
Browser → all /api/* requests use JWT cookie
```

### Step 2.2 — Docker Compose Production

`docker-compose.prod.yml` with nginx, backend, redis, postgres, prometheus, grafana.

### Step 2.3 — GitHub Actions: Deploy Pipeline

`.github/workflows/deploy.yml` — SSH deploy + health check.

### Step 2.4 — Monitoring Board

| Task | Priority | Detail |
|------|----------|--------|
| Prometheus setup | CRITICAL | Scrape orchestrator metrics (`/metrics` endpoint) |
| Grafana dashboards | CRITICAL | Agent activity, latency, token usage, cost per model |
| Node Exporter | HIGH | EC2 system metrics (CPU, RAM, disk, network) |
| Alert rules | HIGH | Cost threshold, error rate spike, agent stall detection |

**Deliverables:**
- [ ] OAuth2 Google + GitHub working
- [ ] Dashboard accessible only after login
- [ ] GitHub Actions auto-deploys on push to `main`
- [ ] HTTPS active on custom domain
- [ ] Grafana accessible via SSH tunnel

## KPIs

| KPI | Target |
|-----|--------|
| Deploy time (push → live) | < 5 min |
| Auth success rate | 100% |
| First token latency | < 5s |
| Uptime | 99% |
| Monthly infra cost | < 60 EUR |

## Security Checklist

- [ ] SSH open only from your fixed IP (Terraform SG)
- [ ] Grafana not publicly exposed (SSH tunnel only)
- [ ] `.env.prod` never in repository (GitHub Secrets only)
- [ ] JWT cookie `httponly=True`, `secure=True`, `samesite=lax`
- [ ] Rate limiting on `/api/*` (max 60 req/min per user)
- [ ] OpenRouter API key rotated every 90 days
