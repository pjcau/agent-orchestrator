# 00 - Project Overview

## Identity

Paperclip is an **open-source orchestration platform for zero-human companies**. It's not an agent framework or a chatbot — it's the infrastructure for running a company made entirely of AI agents. The tagline captures it perfectly: "If OpenClaw is an employee, Paperclip is the company."

## Core Philosophy

Paperclip models the organizational concepts that companies need: org charts, reporting hierarchies, goals, budgets, governance, and accountability. Agents are employees with jobs, not chat participants. The platform doesn't tell you how to build agents — it tells you how to organize and run them.

Key principles:
1. **Bring Your Own Agent** — Any agent runtime (Claude Code, Codex, Cursor, Gemini, OpenClaw, custom) can be plugged in via adapters
2. **Goal alignment** — Every task traces back to the company mission through a goal hierarchy
3. **Heartbeat-driven execution** — Agents wake on schedule, check work, act, then sleep. No continuous running.
4. **Cost control** — Monthly budgets per agent with hard stops. No runaway costs.
5. **Governance** — The human is "the board". Approve hires, override strategy, pause any agent.
6. **Multi-company isolation** — One deployment can run unlimited companies with complete data isolation

## Repo Stats

- **Version**: v0.3.1 (server), active development
- **License**: MIT
- **Language**: TypeScript (Node.js 20+, React 19)
- **Build**: pnpm monorepo
- **Database**: PostgreSQL (embedded for local, external for production)
- **ORM**: Drizzle ORM with 40+ migrations
- **Package count**: ~15 workspace packages
- **Test framework**: Vitest + Playwright (E2E)
- **Source files**: ~1,100+ (excluding lock files, assets, node_modules)

## What It Can Do

Out of the box:
- Define companies with org charts and reporting hierarchies
- Hire agents (any runtime) and assign them roles, budgets, and goals
- Create projects and break goals into trackable issues
- Agents execute work via heartbeats (scheduled wake-ups)
- Track costs per agent, per company, per project
- Export/import entire company templates (org charts, agents, skills)
- Extend functionality via a full plugin system
- Real-time dashboard with WebSocket live events
- Mobile-ready responsive UI

## Key Patterns
- Adapter pattern for agent runtimes — same API, different backends
- Heartbeat-driven execution vs continuous agent loops
- Company-scoped data isolation (every entity belongs to a company)
- Config revisioning with rollback for agent settings
- Budget enforcement as middleware on cost events

## Relevance to Our Project
Paperclip operates at a higher abstraction level than our orchestrator. While we focus on LLM provider abstraction and agent coordination at the technical level, Paperclip adds the "business layer" — org charts, budgets, governance, goal alignment. This is a complementary perspective worth studying.
