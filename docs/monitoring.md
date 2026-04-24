# Monitoring & Operations

Alert pipeline (Grafana → GitHub), external uptime probes, emergency restart, and job log archiving.

See also: `observability-upgrade.md` (Prometheus/Grafana/Tempo config), `deployment.md` (deploy pipeline, SSL, health checks).

## Alert Pipeline (automated root-cause analysis)

When Grafana alerts fire (severity warning or critical):

1. **Webhook**: Grafana → `POST /api/alerts/webhook` on dashboard
2. **Diagnostics**: `AlertHandler` collects recent errors, error summary, and usage snapshot from PostgreSQL
3. **GitHub Issue**: Creates issue with structured diagnostic report using `gh` CLI (labels: `alert`, `automated`)
4. **Analysis**: `.github/workflows/alert-analysis.yml` triggers on new alert issues, runs LLM analysis via OpenRouter (qwen3-235b), posts root-cause analysis as comment
5. **Triage**: Adds `needs-triage` label for human review

Prometheus alerts tied to this pipeline: `GraphNodeHung`, `LLMCallSlow`, `FrontendErrorSpike`, `ProviderDegraded`.

Implementation: `dashboard/alert_webhook.py` (`AlertHandler`).

## Uptime Monitoring

External HTTPS probes for `agents-orchestrator.com` and `monitoring.agents-orchestrator.com` run from GitHub-hosted runners — independent from the EC2 host so an EC2 outage cannot also disable the alert path.

- **Schedule**: `.github/workflows/uptime-check.yml` runs every 10 min (`*/10 * * * *`) plus `workflow_dispatch`.
- **Probe**: 3 curl attempts with 15 s backoff; accepts HTTP 200/301/302/401/403 as "up" (the landing page redirects to OAuth login).
- **Incident issue**: on failure, opens a `uptime-incident` GitHub issue per domain, or appends a timeline comment to the existing open one (dedup by title).
- **Deploy-time probe**: the `Deploy` workflow ends with a public HTTPS probe to catch nginx/cert/DNS issues that container-level health checks miss, and opens a `deploy-failure` issue when anything in the deploy job fails. When the site is reachable but the served cert is untrusted (strict TLS fails, insecure TLS returns 2xx/3xx/401/403), the probe extracts the issuer via `openssl s_client` and fails the deploy — self-signed fallbacks no longer ship silently.
- **Emergency restart**: `.github/workflows/ec2-restart.yml` (manual dispatch) reboots or starts the EC2 instance by resolving its EIP → instance-id → `reboot-instances`/`start-instances` depending on state. Use when the host becomes unreachable.
- **Tests**: `tests/test_ci_workflows.py` asserts schedule, permissions, matrix, and issue-creation wiring stay intact.

## Job Log Archiving

Session logs (`jobs/job_<session_id>/`) are created lazily (only on first file write) and empty dirs are auto-cleaned after 30s. Archived to S3 with metadata in PostgreSQL.

- **Archiver script**: `scripts/archive_jobs.py` — scans for sessions older than N days, tarballs them, uploads to S3, records metadata in `job_archives` table, deletes local files
- **Docker service**: `archiver` in `docker-compose.prod.yml` — runs every 7 days automatically
- **S3 bucket**: `agent-orchestrator-jobs-archive` (Terraform: `terraform/modules/s3/`)
- **Lifecycle**: S3 Standard → Glacier at 90 days → deleted at 365 days
- **IAM**: EC2 role has `s3:PutObject/GetObject/DeleteObject/ListBucket` (Terraform: `terraform/modules/iam/`)
- **Dry run**: `python scripts/archive_jobs.py --dry-run` to preview without uploading
