# 09 - CLI & Python API

## Overview
llm-use offers both a CLI interface (argparse) and a Python API for programmatic use. The CLI is the primary interface.

## CLI Structure (lines 1317-1483)

### Commands
| Command | Description | Key Args |
|---------|-------------|----------|
| `exec` | Execute a task | `--task`, `--orchestrator`, `--worker`, `--router` |
| `chat` | Interactive TUI | `--orchestrator`, `--worker` |
| `mcp` | Start MCP server | `--host`, `--port` |
| `stats` | Show session stats | — |
| `install` | Install optional deps | `--all`, `--scrape`, `--mcp`, `--playwright` |
| `router-reset` | Clear learned routing | — |
| `router-export` | Export routing data | `--out` |
| `router-import` | Import routing data | `--in` |

### Common Arguments (shared across exec/chat/mcp)
- `--orchestrator` (default: `claude-3-7-sonnet-20250219`)
- `--worker` (default: `claude-3-5-haiku-20241022`)
- `--router` — optional routing model
- `--router-path` — local llama.cpp model for routing
- `--max-workers` (default: 10)
- `--ollama-url` (default: `http://localhost:11434`)
- `--llama-cpp-url` (default: `http://localhost:8080`)
- `--enable-scrape` — enable web scraping
- `--no-cache` — disable response caching

### Model Parsing
`parse_model()` (lines 1301-1315) handles both forms:
- `provider:model` (e.g., `ollama:llama3.1:8b`)
- `model` alone (looked up in `DEFAULT_MODELS`)

## Python API

### Via Import Shim (`llm_use.py`)
```python
from llm_use import Orchestrator, ModelConfig

orch = Orchestrator(
    orchestrator=ModelConfig(name="llama3.1:70b", provider="ollama"),
    worker=ModelConfig(name="llama3.1:8b", provider="ollama")
)
result = orch.execute("Your task")
print(result["output"])
```

### Return Format
```python
{
    "output": "Final answer text",
    "mode": "parallel",
    "orchestrator_model": "anthropic:claude-3-7-sonnet-20250219",
    "worker_model": "ollama:llama3.1:8b",
    "workers_spawned": 5,
    "workers_succeeded": 5,
    "cost": 0.007,
    "breakdown": {"orchestrator": 0.003, "workers": 0.001, "synthesis": 0.003},
    "duration": 8.2,
    "session_id": "abc123"
}
```

## Code Duplication
There's significant duplication in `main()`: the Orchestrator initialization block is copy-pasted 3 times (exec, chat, mcp). This could be extracted into a helper.

## Key Patterns
- argparse with subcommands for clear CLI structure
- `add_common_args()` helper reduces argument duplication
- Import shim pattern for dual CLI/library usage
- Dict return values (no typed response objects)

## Relevance to Our Project
Our `OrchestratorClient` (`client.py`) provides a similar Python API but wraps more components (agents, skills, graphs). Their approach of returning plain dicts is simpler but less type-safe than our approach. The CLI design with subcommands is clean and could inspire a CLI frontend for our orchestrator.
