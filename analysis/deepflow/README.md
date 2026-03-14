# DeerFlow 2.0 Deep Analysis

**Repository**: [bytedance/deer-flow](https://github.com/bytedance/deer-flow)
**Analysis Date**: 2026-03-14
**Version Analyzed**: 2.0 (ground-up rewrite, shares no code with v1)

## What is DeerFlow?

DeerFlow (**D**eep **E**xploration and **E**fficient **R**esearch **Flow**) is an open-source "super agent harness" by ByteDance. It orchestrates sub-agents, memory, and sandboxes — powered by extensible skills. Built on LangGraph and LangChain.

## Key Stats

- **Stars**: #1 GitHub Trending (Feb 28, 2026)
- **License**: MIT
- **Language**: Python 3.12+ (backend), TypeScript/Next.js 16 (frontend)
- **Build**: uv (Python), pnpm (frontend)
- **LLM Framework**: LangGraph + LangChain

## Analysis Structure (30 files)

| # | File | Topic |
|---|------|-------|
| 01 | [01-project-overview.md](01-project-overview.md) | Project goals, positioning, evolution |
| 02 | [02-architecture.md](02-architecture.md) | High-level architecture, component diagram |
| 03 | [03-tech-stack.md](03-tech-stack.md) | Languages, frameworks, dependencies |
| 04 | [04-agent-system.md](04-agent-system.md) | Lead agent, creation, configuration |
| 05 | [05-middleware-chain.md](05-middleware-chain.md) | 11 middleware components in detail |
| 06 | [06-subagent-system.md](06-subagent-system.md) | Sub-agent decomposition and execution |
| 07 | [07-tool-system.md](07-tool-system.md) | Tool registration, groups, resolution |
| 08 | [08-sandbox-system.md](08-sandbox-system.md) | Local/Docker/K8s sandboxes |
| 09 | [09-skills-system.md](09-skills-system.md) | Progressive skill loading, SKILL.md format |
| 10 | [10-llm-integration.md](10-llm-integration.md) | Model factory, provider abstraction |
| 11 | [11-mcp-integration.md](11-mcp-integration.md) | MCP server, tools, OAuth |
| 12 | [12-memory-system.md](12-memory-system.md) | Long-term memory with facts |
| 13 | [13-state-management.md](13-state-management.md) | ThreadState, reducers, checkpointing |
| 14 | [14-configuration.md](14-configuration.md) | YAML config, env vars, versioning |
| 15 | [15-frontend.md](15-frontend.md) | Next.js 16, React 19, UI architecture |
| 16 | [16-api-layer.md](16-api-layer.md) | Gateway API, LangGraph Server, nginx |
| 17 | [17-streaming.md](17-streaming.md) | SSE streaming, real-time events |
| 18 | [18-im-channels.md](18-im-channels.md) | Telegram, Slack, Feishu integration |
| 19 | [19-prompt-engineering.md](19-prompt-engineering.md) | System prompts, clarification, skills injection |
| 20 | [20-human-in-the-loop.md](20-human-in-the-loop.md) | Clarification tool, plan mode |
| 21 | [21-testing.md](21-testing.md) | Test strategy, harness boundary |
| 22 | [22-error-handling.md](22-error-handling.md) | Loop detection, tool errors, resilience |
| 23 | [23-security.md](23-security.md) | Path traversal, sandbox isolation |
| 24 | [24-docker-deployment.md](24-docker-deployment.md) | Docker Compose, nginx, production |
| 25 | [25-embedded-client.md](25-embedded-client.md) | DeerFlowClient, programmatic access |
| 26 | [26-comparison.md](26-comparison.md) | vs our agent-orchestrator |
| 27 | [27-strengths.md](27-strengths.md) | What DeerFlow does well |
| 28 | [28-weaknesses.md](28-weaknesses.md) | Gaps and limitations |
| 29 | [29-learnings.md](29-learnings.md) | Key takeaways |
| 30 | [30-roadmap.md](30-roadmap.md) | Adoption roadmap for our project |
