# 00 - Project Overview

## Overview
llm-use is a universal LLM orchestrator that runs a "planner + workers + synthesis" flow across multiple providers. It's a compact CLI tool focused on simplicity and cost tracking.

## Goals & Positioning
- Provider-agnostic orchestration: mix cloud (Anthropic, OpenAI) and local (Ollama, llama.cpp) models
- Cost-conscious: tracks token usage and cost per run with breakdowns
- Offline-capable: works fully with Ollama, no cloud dependency required
- Minimal dependencies: only `requests` is required; everything else is optional

## Key Stats
- **Version**: 0.2.0
- **Language**: Python 3.10+
- **License**: MIT
- **Total files**: 10 (excluding .git)
- **Core file**: `cli.py` (~1484 lines, ~58KB) — contains ALL logic
- **Build system**: setuptools
- **Author**: Vincenzo

## Architecture Summary
The orchestrator follows a 3-phase pattern:
1. **Route/Plan**: Decide if task is simple (single-shot) or complex (parallel)
2. **Execute**: Either direct answer or spawn parallel workers
3. **Synthesize**: Combine worker results into final output

## Modes of Operation
1. **exec** — One-shot task execution (CLI)
2. **chat** — Interactive TUI chat mode (curses-based)
3. **mcp** — MCP server via PolyMCP + uvicorn
4. **stats** — View session history and cost summary
5. **router-reset/export/import** — Manage learned routing data

## Key Patterns
- Single-file monolith: entire project lives in `cli.py`
- Optional dependency pattern: try/except imports with `HAS_*` flags
- JSON-over-prompt: LLM decides execution mode via structured JSON output
- SQLite-based caching: LLM responses, scrape results, and router learning data

## Relevance to Our Project
This is a much simpler take on the same problem our agent-orchestrator solves. It's worth studying for its minimalist approach to task decomposition and cost tracking.
