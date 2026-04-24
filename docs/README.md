# Documentation Index

Navigable entry point for the docs/ directory. Read top-to-bottom for a full onboarding, or jump to the area you need.

## Start Here

| Doc | What it covers |
|-----|----------------|
| [../CLAUDE.md](../CLAUDE.md) | Project rules (language, mandatory tests/docs, import boundary), quick overview, pointers |
| [architecture.md](architecture.md) | Core abstractions (Provider, Agent, Skill, Orchestrator), design rationale, mapping from Claude Code |
| [abstractions.md](abstractions.md) | Exhaustive reference catalog of every abstraction by concern |

## Building & Running

| Doc | What it covers |
|-----|----------------|
| [deployment.md](deployment.md) | Production deployment (EC2, SSL, Nginx), CI/CD pipeline, secrets, troubleshooting |
| [infrastructure.md](infrastructure.md) | Cloud vs on-prem decision framework |
| [cost-analysis.md](cost-analysis.md) | Provider comparison, cost modeling, routing strategies |
| [migration-from-claude.md](migration-from-claude.md) | How to abstract existing Claude Code configs |

## Agents, Skills, Dashboard

| Doc | What it covers |
|-----|----------------|
| [agents.md](agents.md) | 30 agents organised by category, cross-dependencies, skills map, research scout workflow |
| [dashboard.md](dashboard.md) | Dashboard UI: multi-category routing, conversation persistence, MCP server/client, SSE streaming, async team run, session explorer, memory, metrics, modular architecture |

## Security, Observability, Operations

| Doc | What it covers |
|-----|----------------|
| [security.md](security.md) | Auth (OAuth2, JWT, API keys), RBAC, secrets, network protection, sandbox isolation, AWS deployment checklist, CI security scanning |
| [monitoring.md](monitoring.md) | Alert pipeline (Grafana → GitHub issues → LLM analysis), uptime probes, emergency restart, job log archiving to S3 |
| [observability-upgrade.md](observability-upgrade.md) | Prometheus, Grafana, Tempo, OpenTelemetry setup |

## Engineering Practices

| Doc | What it covers |
|-----|----------------|
| [prompt-engineering.md](prompt-engineering.md) | Marker-based prompt injection, PromptRegistry usage |
| [cache-strategy.md](cache-strategy.md) | LLM cache, tool cache, compaction |
| [components.md](components.md) | Frontend React component map |
| [roadmap.md](roadmap.md) | Feature roadmap |
| [phase2.md](phase2.md) | Verification gate, atomic task validator (PR #59) |
| [phase3.md](phase3.md) | Modality detection (PR #88) |
| [phase0-cost-report.md](phase0-cost-report.md) | Phase 0 cost analysis |
| [dead-code-report.md](dead-code-report.md) | Dead code audit |

## Learning Path Tests

[learning-path-tests/](learning-path-tests/) — dated test logs from the `/orchestrator-learning-path-test` skill, each with confidence score and improvement proposals.
