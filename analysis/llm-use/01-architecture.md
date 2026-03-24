# 01 - Architecture

## Overview
llm-use uses a straightforward 3-phase architecture: Route → Execute → Synthesize. Everything lives in a single file (`cli.py`) with clear class boundaries.

## Component Diagram

```
CLI (argparse)
    │
    ├── exec ──→ Orchestrator.execute()
    │               │
    │               ├── _route() ──→ Router (LLM/heuristic/learned)
    │               │                   │
    │               │                   ├── "simple" → _execute_simple() → worker direct call
    │               │                   └── "full"   → _get_decision()
    │               │
    │               ├── _get_decision() ──→ LLM returns JSON {mode, subtasks/response}
    │               │
    │               ├── "single" → _execute_single() → output from orchestrator LLM
    │               │
    │               └── "parallel" → _execute_parallel()
    │                                   │
    │                                   ├── _spawn_workers() → ThreadPoolExecutor
    │                                   │     └── _run_worker() × N (with optional scraping)
    │                                   │
    │                                   └── _synthesize() → orchestrator LLM combines results
    │
    ├── chat ──→ run_chat_tui() → curses TUI with threaded execution
    ├── mcp  ──→ run_mcp_server() → PolyMCP HTTP server
    └── stats ─→ print_stats() → read session JSON files
```

## Class Structure

| Class | Responsibility | Lines |
|-------|---------------|-------|
| `OllamaProvider` | HTTP calls to Ollama `/api/generate` | ~15 |
| `LlamaCppProvider` | OpenAI-compatible `/v1/chat/completions` | ~20 |
| `AnthropicProvider` | Anthropic SDK wrapper | ~10 |
| `OpenAIProvider` | OpenAI SDK wrapper | ~10 |
| `Cache` | SQLite: LLM cache, scrape cache, router examples | ~100 |
| `API` | Provider registry + cache-aware call dispatch | ~50 |
| `SessionManager` | JSON file persistence for sessions | ~60 |
| `Orchestrator` | Core logic: routing, execution, synthesis | ~300 |

## Execution Flow (Parallel Mode)

1. User submits task via CLI
2. Optional router decides if task is simple or complex
3. If complex: orchestrator LLM generates JSON with subtasks
4. `ThreadPoolExecutor` spawns workers (up to `max_workers`)
5. Each worker calls the worker model; optionally scrapes URLs
6. Results collected with per-worker and global timeouts
7. Synthesis LLM combines worker outputs
8. Session saved to `~/.llm-use/sessions/{id}.json`

## Key Design Decisions
- **Single model per role**: one orchestrator model, one worker model (no per-task model selection)
- **JSON-over-prompt**: LLM output is parsed as JSON to decide execution mode
- **Thread-based parallelism**: `ThreadPoolExecutor` for worker spawning (not async)
- **File-based sessions**: JSON files in `~/.llm-use/sessions/`, no database

## Key Patterns
- Monolithic single-file design keeps deployment simple
- Provider abstraction is minimal but effective (each provider has identical `call()` signature)
- Prompt templates are module-level constants

## Relevance to Our Project
The 3-phase pattern (plan → execute → synthesize) mirrors our StateGraph approach but without explicit graph representation. The simplicity is notable — no middleware, no plugins, no event system.
