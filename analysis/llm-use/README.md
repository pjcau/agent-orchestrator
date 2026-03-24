# llm-use Analysis

**Repository**: [llm-use/llm-use](https://github.com/llm-use/llm-use)
**Analysis Date**: 2026-03-20
**Version Analyzed**: v0.2.0 (commit `464e0fb`)

## Key Stats
- **Language**: Python 3.10+
- **License**: MIT
- **Size**: ~1484 lines in a single file (`cli.py`)
- **Dependencies**: `requests` (only required), optional: `anthropic`, `openai`, `beautifulsoup4`, `playwright`, `polymcp`
- **Author**: Vincenzo

## Analysis Structure (15 files)

| # | File | Topic |
|---|------|-------|
| 00 | [00-overview.md](00-overview.md) | Project overview, goals, positioning, key stats |
| 01 | [01-architecture.md](01-architecture.md) | Architecture, component diagram, execution flow |
| 02 | [02-tech-stack.md](02-tech-stack.md) | Tech stack, dependencies, import shim pattern |
| 03 | [03-provider-system.md](03-provider-system.md) | Provider implementations, API class, cost model |
| 04 | [04-router-system.md](04-router-system.md) | 3-tier routing: LLM, learned (ML), heuristic |
| 05 | [05-cache-and-sessions.md](05-cache-and-sessions.md) | SQLite cache, JSON session persistence |
| 06 | [06-scraping-system.md](06-scraping-system.md) | Web scraping with BS4/Playwright, worker-level RAG |
| 07 | [07-tui-chat.md](07-tui-chat.md) | Curses-based TUI with live status panel |
| 08 | [08-mcp-server.md](08-mcp-server.md) | MCP server via PolyMCP (3 tools) |
| 09 | [09-cli-and-api.md](09-cli-and-api.md) | CLI structure, Python API, model parsing |
| 10 | [10-testing.md](10-testing.md) | Testing strategy (5 tests, coverage gaps) |
| 11 | [11-comparison.md](11-comparison.md) | Comparison vs our agent-orchestrator |
| 12 | [12-strengths.md](12-strengths.md) | What llm-use does well (7 strengths) |
| 13 | [13-weaknesses.md](13-weaknesses.md) | Gaps and limitations (10 weaknesses) |
| 14 | [14-learnings.md](14-learnings.md) | Actionable takeaways and adoption roadmap |

## Quick Start

Start with **[00-overview](00-overview.md)** for the big picture,
then **[11-comparison](11-comparison.md)** for how it relates to our project,
and **[14-learnings](14-learnings.md)** for actionable next steps.

## Top Actionable Items

| # | Action | Effort | Impact | Priority |
|---|--------|--------|--------|----------|
| 1 | Robust JSON extraction utility | Low | High | P1 |
| 2 | Learned router for TaskRouter | Medium | Medium | P2 |
| 3 | Worker-level web grounding | Low | Medium | P2 |
| 4 | CLI frontend for OrchestratorClient | Medium | Low | P3 |
