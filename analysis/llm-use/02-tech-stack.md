# 02 - Tech Stack & Dependencies

## Overview
llm-use is remarkably lean in its dependency footprint. Only `requests` is required; everything else is optional.

## Core Dependencies

| Package | Purpose | Required |
|---------|---------|----------|
| `requests>=2.31.0` | HTTP calls to Ollama, scraping | Yes |
| `anthropic>=0.32.0` | Anthropic Claude API | Optional |
| `openai>=1.0.0` | OpenAI GPT API | Optional |
| `beautifulsoup4>=4.12.0` | Web scraping / HTML parsing | Optional |
| `playwright>=1.43.0` | Dynamic web scraping (JS rendering) | Optional |
| `polymcp>=0.1.0` | MCP server support | Optional |
| `uvicorn>=0.30.0` | ASGI server for MCP | Optional |
| `pytest>=8.0.0` | Testing | Dev only |

## Standard Library Usage
The project makes heavy use of the standard library:
- `sqlite3` — caching and router learning storage
- `threading` / `concurrent.futures` — parallel worker execution
- `curses` — TUI chat interface
- `hashlib` — cache keys (MD5)
- `argparse` — CLI interface
- `dataclasses` — data models (Call, Session, ModelConfig)
- `json` — serialization everywhere
- `re` — URL extraction, router pattern matching
- `logging` — structured logging to file + console

## Build System
- **Tool**: setuptools (not hatchling, poetry, or flit)
- **Config**: `pyproject.toml` (modern style)
- **Entry point**: `llm-use = "llm_use:main"` (via import shim)
- **Python**: 3.10+

## Import Shim Pattern
`llm_use.py` is a thin wrapper that dynamically imports `cli.py`:
```python
from importlib.util import spec_from_file_location, module_from_spec
_spec = spec_from_file_location("llm_use_cli", _MODULE_PATH)
_mod = module_from_spec(_spec)
_spec.loader.exec_module(_mod)
Orchestrator = _mod.Orchestrator
```
This allows `cli.py` to be both a runnable script and an importable module.

## Key Patterns
- Optional dependency pattern: `try/except ImportError` with `HAS_*` boolean flags
- Zero external deps for local-only usage (Ollama needs only `requests`)
- Import shim for dual CLI/library usage

## Relevance to Our Project
Our orchestrator has ~30 modules and many more dependencies. The minimal dependency approach here is worth noting — it makes installation trivial and reduces supply chain risk. The optional dependency pattern with `HAS_*` flags is similar to our approach in `tracing.py`.
