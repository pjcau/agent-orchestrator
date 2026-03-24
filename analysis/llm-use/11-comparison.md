# 11 - Comparison: llm-use vs agent-orchestrator

## Overview
A side-by-side comparison of llm-use and our agent-orchestrator project across key dimensions.

## Architecture Comparison

| Dimension | llm-use | agent-orchestrator |
|-----------|---------|-------------------|
| **Size** | 1 file, ~1484 lines | ~30 modules, ~15K+ lines |
| **Structure** | Monolithic CLI | Modular library + app layers |
| **Build** | setuptools | hatchling |
| **Python** | 3.10+ | 3.11+ |

## Core Abstractions

| Concept | llm-use | agent-orchestrator |
|---------|---------|-------------------|
| **Provider** | 4 provider classes, duck-typed | Abstract base class with health, tracing, rate limiting |
| **Agent** | Single Orchestrator class | Agent base class with roles, tools, skills |
| **Task routing** | 3-tier router (LLM/learned/heuristic) | TaskRouter with 6 strategies + category awareness |
| **Execution** | ThreadPoolExecutor | StateGraph with async, channels, checkpointing |
| **State** | JSON files + SQLite | PostgreSQL + InMemory + SQLite checkpointers |
| **Cost tracking** | Per-session, file-based | UsageTracker with budget enforcement per task/session/day |
| **Caching** | SQLite (LLM + scrape) | InMemoryCache (LLM) + cache middleware (tools) |
| **Scraping** | Built-in (BS4 + Playwright) | web_reader.py skill |

## What llm-use Has That We Don't

| Feature | Details | Priority for Us |
|---------|---------|----------------|
| **Learned router** | Cosine similarity on past tasks | Medium -- interesting for auto-improving routing |
| **TUI chat** | curses-based terminal chat | Low -- we have a web dashboard |
| **PolyMCP auto-exposure** | Function -> MCP tool automatically | Low -- we have explicit MCP registry |
| **Router export/import** | Portable routing knowledge | Low -- niche feature |
| **Scrape-in-worker** | Workers fetch+ground with web content | Medium -- RAG-like pattern worth adopting |

## What We Have That llm-use Doesn't

| Feature | Details |
|---------|---------|
| **Multi-agent** | 25 specialized agents across 5 categories |
| **Skills system** | Reusable capabilities with middleware |
| **StateGraph** | Full graph execution engine with channels |
| **Dashboard** | Web-based real-time monitoring UI |
| **Integrations** | Slack, Telegram bots |
| **Auth/RBAC** | OAuth2, API keys, role-based access |
| **Tracing** | OpenTelemetry with Tempo |
| **Metrics** | Prometheus-compatible |
| **Conversation memory** | Multi-turn with persistence and summarization |
| **Plugins** | Runtime plugin loading |
| **Webhooks** | HMAC-validated inbound webhooks |
| **Sandbox** | Docker-based isolated execution |
| **CI/CD** | Full deploy pipeline, security scanning |
| **Infrastructure** | Terraform, Docker Compose, Nginx |

## Execution Model Comparison

### llm-use: Linear Pipeline
```
Task -> Router -> Orchestrator LLM -> Workers (parallel) -> Synthesis LLM -> Output
```

### agent-orchestrator: Graph-Based
```
Task -> TaskRouter -> Agent(s) -> StateGraph nodes -> Channels -> Checkpoints -> Output
         |              |
    Category routing  Skills + tools
         |              |
    Provider selection  Middleware chain
```

## Cost Tracking Comparison

### llm-use
- Per-session JSON files
- Simple accumulation: `total_cost += call.cost`
- Stats command reads all files
- No budgets, no alerts

### agent-orchestrator
- PostgreSQL persistence
- UsageTracker with budget enforcement
- Per-task, per-session, per-day tracking
- Spend alerts with rules and thresholds
- Prometheus metrics for monitoring
- Grafana dashboards

## Key Takeaways
1. **llm-use proves the core pattern works**: planner + workers + synthesis is sound
2. **Simplicity vs. capability**: llm-use is 100x simpler but handles 1% of our use cases
3. **Learned routing is novel**: we should consider adding learning to our TaskRouter
4. **Worker-level RAG**: their scrape-in-worker pattern is worth adopting
5. **Single-file approach**: great for prototyping, terrible for maintenance at scale

## Relevance to Our Project
This comparison highlights that our orchestrator is orders of magnitude more capable, but llm-use's simplicity in certain areas (router learning, scrape grounding) offers specific patterns worth adopting.
