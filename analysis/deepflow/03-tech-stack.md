# 03 - Tech Stack

## Backend

| Component | Technology |
|-----------|-----------|
| Language | Python 3.12+ |
| Package Manager | uv (with workspace) |
| Build System | hatchling |
| Agent Framework | LangGraph + LangChain |
| API Framework | FastAPI |
| Server | uvicorn |
| Config | YAML (pyyaml) + Pydantic |
| Environment | dotenv |
| Linting | ruff |
| Testing | pytest |

## Core Dependencies

```
langchain >= 1.2.3
langgraph >= 1.0.6
langgraph-api >= 0.7.0
langgraph-cli >= 0.4.14
langgraph-runtime-inmem >= 0.22.1
langchain-openai >= 1.1.7
langchain-anthropic >= 1.3.4
langchain-deepseek >= 1.0.1
langchain-google-genai >= 4.2.1
langchain-mcp-adapters >= 0.1.0
```

## Search & Fetch Tools

| Tool | Library |
|------|---------|
| Web Search | tavily-python, ddgs (DuckDuckGo) |
| Web Fetch | Jina AI reader, Firecrawl |
| Image Search | DuckDuckGo |
| InfoQuest | BytePlus search (community) |

## Frontend

| Component | Technology |
|-----------|-----------|
| Framework | Next.js 16 (App Router) |
| UI Library | React 19 |
| Language | TypeScript |
| Package Manager | pnpm 10 |
| Styling | Tailwind CSS 4 |
| UI Components | Radix UI primitives |
| Animations | GSAP, Motion (framer) |
| State Management | TanStack Query (React Query) |
| Code Editor | CodeMirror |
| Graph Visualization | XYFlow (React Flow) |
| Markdown | remark-gfm, rehype-katex |
| Streaming | streamdown |
| Auth | better-auth |
| i18n | Custom (en-US, zh-CN) |

## Infrastructure

| Component | Technology |
|-----------|-----------|
| Reverse Proxy | nginx |
| Containerization | Docker, Docker Compose |
| Sandbox Orchestration | Docker / Apple Container / K8s |
| Kubernetes | k3s (for sandbox provisioner) |
| CI | GitHub Actions |

## Notable Choices

1. **uv** over pip/poetry — fast, workspace support
2. **LangGraph** as runtime — not just LangChain chains
3. **Next.js 16** — bleeding edge (App Router, Turbo)
4. **Radix UI** — accessible, unstyled primitives
5. **No database** — file-based storage (JSON, SQLite checkpoints)
6. **No Redis** — no caching layer
7. **streamdown** — custom markdown streaming library
