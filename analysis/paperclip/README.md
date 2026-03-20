# Paperclip Analysis

**Repository**: [paperclipai/paperclip](https://github.com/paperclipai/paperclip)
**Analysis Date**: 2026-03-20
**Version Analyzed**: v0.3.1 (commit a290d1d)

## What is Paperclip?

Paperclip is an open-source Node.js + React platform for orchestrating "zero-human companies." It manages teams of AI agents with org charts, budgets, goals, governance, and accountability. The tagline: "If OpenClaw is an employee, Paperclip is the company."

## Key Stats

- **Language**: TypeScript (Node.js 20+, React 19)
- **License**: MIT
- **Build**: pnpm monorepo (~15 packages)
- **Database**: PostgreSQL (embedded for local, external for production)
- **Source files**: ~1,100+
- **DB tables**: 55+
- **Agent adapters**: 10 (Claude Code, Codex, Cursor, Gemini, OpenCode, Pi, OpenClaw, Hermes, process, HTTP)

## Analysis Structure (30 files)

### Core (00-09)
| # | File | Topic |
|---|------|-------|
| 00 | [00-overview.md](00-overview.md) | Project goals, positioning, stats |
| 01 | [01-architecture.md](01-architecture.md) | High-level architecture, component diagram |
| 02 | [02-tech-stack.md](02-tech-stack.md) | Languages, frameworks, dependencies |
| 03 | [03-company-model.md](03-company-model.md) | Company as organizational unit |
| 04 | [04-agent-system.md](04-agent-system.md) | Agent lifecycle, config versioning, auth |
| 05 | [05-heartbeat-system.md](05-heartbeat-system.md) | Heartbeat execution engine |
| 06 | [06-adapter-system.md](06-adapter-system.md) | Agent runtime adapters |
| 07 | [07-issue-tracking.md](07-issue-tracking.md) | Full issue tracking system |
| 08 | [08-goal-hierarchy.md](08-goal-hierarchy.md) | Hierarchical goal system |
| 09 | [09-budget-system.md](09-budget-system.md) | Budget and cost control |

### Infrastructure (10-19)
| # | File | Topic |
|---|------|-------|
| 10 | [10-configuration.md](10-configuration.md) | Layered config, deployment modes |
| 11 | [11-persistence.md](11-persistence.md) | Drizzle ORM, 55+ tables, embedded Postgres |
| 12 | [12-api-layer.md](12-api-layer.md) | Express 5 REST API |
| 13 | [13-realtime.md](13-realtime.md) | WebSocket live events |
| 14 | [14-frontend.md](14-frontend.md) | React 19 dashboard |
| 15 | [15-plugin-system.md](15-plugin-system.md) | Plugin SDK and architecture |
| 16 | [16-security.md](16-security.md) | Auth, secrets, guards, redaction |
| 17 | [17-testing.md](17-testing.md) | Testing strategy and patterns |
| 18 | [18-error-handling.md](18-error-handling.md) | Error factories, recovery |
| 19 | [19-deployment.md](19-deployment.md) | Local, Docker, production |

### Deep Dives (20-25)
| # | File | Topic |
|---|------|-------|
| 20 | [20-company-portability.md](20-company-portability.md) | Export/import companies |
| 21 | [21-onboarding-system.md](21-onboarding-system.md) | CEO template, SOUL.md |
| 22 | [22-skill-system.md](22-skill-system.md) | Company skills and adapter sync |
| 23 | [23-approval-governance.md](23-approval-governance.md) | Approval workflow |
| 24 | [24-workspace-execution.md](24-workspace-execution.md) | Git worktree, runtime services |
| 25 | [25-finance-accounting.md](25-finance-accounting.md) | Double-entry finance ledger |

### Insights (26-29)
| # | File | Topic |
|---|------|-------|
| 26 | [26-comparison.md](26-comparison.md) | Paperclip vs agent-orchestrator |
| 27 | [27-strengths.md](27-strengths.md) | What Paperclip does well |
| 28 | [28-weaknesses.md](28-weaknesses.md) | Gaps and limitations |
| 29 | [29-learnings.md](29-learnings.md) | Actionable takeaways and roadmap |

## Quick Start

Start with **[00-overview](00-overview.md)** for the big picture, then **[26-comparison](26-comparison.md)** for how it relates to our project, and **[29-learnings](29-learnings.md)** for actionable next steps.

---

*Analysis date: 2026-03-20*
*Source: github.com/paperclipai/paperclip (shallow clone, commit a290d1d)*
