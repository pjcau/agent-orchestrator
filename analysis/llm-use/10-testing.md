# 10 - Testing Strategy

## Overview
llm-use has a single test file with 5 tests. The testing approach is minimal but covers key functionality.

## Test File: `tests/test_llm_use.py` (85 lines)

### Tests

| Test | What it covers | Approach |
|------|---------------|----------|
| `test_parse_orchestrator_json_codefence` | JSON extraction from markdown code fences | Unit test, pure function |
| `test_parse_orchestrator_json_embedded` | JSON extraction from mixed text | Unit test, pure function |
| `test_spawn_workers_global_timeout` | Worker timeout handling | Integration, uses FakeAPI mock |
| `test_print_stats` | Stats display with session files | Integration, uses tmp_path + monkeypatch |
| `test_import_shim` | Library import works | Smoke test |

### Test Patterns

**Dynamic Import**: Tests import `cli.py` directly via `importlib`:
```python
MODULE_PATH = Path(__file__).resolve().parents[1] / "cli.py"
spec = importlib.util.spec_from_file_location("llm_use", MODULE_PATH)
llm_use = importlib.util.module_from_spec(spec)
spec.loader.exec_module(llm_use)
```

**FakeAPI Mock**: A minimal mock for the API class:
```python
class FakeAPI:
    def call(self, *args, **kwargs):
        time.sleep(0.1)
        return llm_use.Call("c1", "m", "p", "", "", 0, 0, 0.0, 0.0)
```

**Monkeypatching**: Uses pytest's `monkeypatch` for:
- Overriding timeout constants
- Setting `HOME` to `tmp_path` for isolated file operations

### Coverage Gaps
- No tests for provider classes (Anthropic, OpenAI, Ollama)
- No tests for the router system (heuristic, learned, LLM)
- No tests for scraping functionality
- No tests for TUI chat mode
- No tests for MCP server
- No integration tests with real LLMs
- No tests for cache operations (SQLite)
- No test configuration or CI pipeline

## Key Patterns
- Minimal but focused: tests cover the trickiest parts (JSON parsing, timeouts)
- Dynamic module import to handle the cli.py naming
- Good use of pytest fixtures (tmp_path, monkeypatch, capsys)

## Relevance to Our Project
Our test suite is much more comprehensive with import boundary tests, conformance suites, and per-module tests. Their approach of testing the JSON parser and timeout behavior first is pragmatic -- these are the parts most likely to break. The FakeAPI pattern is simple and effective for testing without real providers.
