# 13 - Weaknesses & Limitations

## Overview
Gaps and design limitations in llm-use that would need addressing for production use.

## 1. Single-File Monolith
- All 1484 lines in `cli.py` -- no separation of concerns
- Impossible to test or reuse individual components independently
- Code duplication: Orchestrator init copied 3 times in `main()`
- Scraping logic duplicated between `_scrape_urls()`, `_scrape_urls_playwright()`, and `simple_scrape()`

**Impact**: Maintainability degrades as features are added. Already showing signs with copy-paste patterns.

## 2. No Async Support
- All LLM calls are synchronous
- Worker parallelism via `ThreadPoolExecutor` (GIL-bound for CPU work)
- TUI uses `time.sleep(0.02)` polling loop
- MCP server runs on uvicorn but orchestrator calls block threads

**Impact**: Can't efficiently handle many concurrent requests. Thread pool limits scalability.

## 3. Silent Error Swallowing
Multiple `try/except: pass` patterns:
```python
# Provider init
if anthropic_key:
    try:
        self.providers["anthropic"] = AnthropicProvider(anthropic_key)
    except: pass  # What went wrong? Nobody knows.

# Session loading
except: pass  # Corrupt session file? Silently skipped.
```

**Impact**: Debugging is extremely difficult. Errors disappear silently.

## 4. No Cache Eviction
- LLM cache grows unbounded
- Scrape cache has no TTL (stale content served forever)
- Router examples capped at 500 but LLM/scrape tables are not
- No cache size monitoring

**Impact**: `cache.sqlite` grows indefinitely. Stale scrape content may produce incorrect answers.

## 5. Minimal Testing
- 5 tests total, no CI pipeline
- No provider tests, no router tests, no scraping tests
- No type checking (no mypy/pyright)
- No linting configuration

**Impact**: Regressions go undetected. Refactoring is risky.

## 6. Hardcoded Prompts
- `ORCHESTRATOR_PROMPT`, `ROUTER_PROMPT`, `SYNTHESIS_PROMPT` are module-level constants
- No way to customize prompts without editing source code
- No prompt versioning or A/B testing

**Impact**: Users can't tune behavior for their specific use cases.

## 7. No Authentication or Multi-User Support
- Single-user only
- API keys stored in environment variables
- No rate limiting on MCP server
- No access control

**Impact**: Unsuitable for shared or production deployments.

## 8. No Streaming
- All LLM calls use `stream: False`
- User sees nothing until full response is ready
- Long tasks appear to hang

**Impact**: Poor UX for complex tasks that take minutes.

## 9. No Agent Memory / Context
- Chat mode only keeps last 6 turns
- No persistent conversation memory
- No cross-session context
- No summarization for long conversations

**Impact**: Chat mode forgets context quickly. No learning across sessions.

## 10. Security Concerns
- MD5 for cache keys (not security-critical but outdated)
- No input sanitization on task text
- MCP server has no authentication
- `simple_scrape()` follows any URL without restriction
- Bare `except:` catches can mask security-relevant errors

**Impact**: Not suitable for any environment with untrusted input.

## Key Patterns
- Technical debt accumulates fast in single-file projects
- Silent error handling is the most dangerous pattern
- Missing tests make every change risky

## Relevance to Our Project
These weaknesses validate our architectural decisions: modular structure, async execution, comprehensive testing, PostgreSQL persistence, auth/RBAC, and proper error handling. The gaps here are exactly what our framework addresses.
