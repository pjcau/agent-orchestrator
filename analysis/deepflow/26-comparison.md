# 26 - Comparison: DeerFlow vs Our Agent Orchestrator

## Architecture Philosophy

| Aspect | DeerFlow | Our Orchestrator |
|--------|----------|-----------------|
| Approach | Single super agent + sub-agents | Multi-agent with 24 specialized agents |
| Framework | Built on LangGraph | Custom-built from scratch |
| Routing | Lead agent decides | TaskRouter (6 strategies) |
| Agent Identity | 1 lead + 2 sub-agent types | 24 agents with distinct personas |
| Categories | None (general-purpose) | 5 (software, finance, data, marketing, tooling) |

## Feature Comparison

| Feature | DeerFlow | Ours | Winner |
|---------|----------|------|--------|
| Sandbox execution | Local/Docker/K8s | None | DeerFlow |
| Skills (instructions) | Progressive loading | Code-based | DeerFlow |
| Memory system | LLM-powered facts | File-based manual | Tie |
| MCP integration | Consumer + OAuth | Expose as MCP server | DeerFlow |
| IM channels | Telegram/Slack/Feishu | None | DeerFlow |
| Embedded client | DeerFlowClient | None | DeerFlow |
| Loop detection | Yes (middleware) | Anti-stall (basic) | DeerFlow |
| Observability | LangSmith (optional) | Prometheus/Grafana/Tempo | Ours |
| Security | Sandbox isolation | RBAC + OAuth + audit | Ours |
| Cost tracking | None | UsageTracker + budgets | Ours |
| Provider support | LangChain adapters | Custom Provider interface | Tie |
| Graph engine | LangGraph | Custom StateGraph | Tie |
| Testing | ~40 unit tests | Extensive pytest suite | Tie |
| Frontend | Full SaaS-quality | Utilitarian dashboard | DeerFlow |
| CI/CD | Basic (tests only) | Full pipeline (test/lint/deploy) | Ours |
| Alert pipeline | None | Grafana → GitHub Issues → LLM analysis | Ours |
| Job archiving | None | S3 + Glacier lifecycle | Ours |
| Multi-user | None | RBAC (admin/dev/viewer) | Ours |

## Strengths We Have That DeerFlow Lacks

1. **24 specialized agents** with domain expertise
2. **Production observability** (Prometheus, Grafana, Tempo)
3. **Cost tracking and budget enforcement**
4. **RBAC and multi-user support**
5. **CI/CD pipeline** with deploy to EC2
6. **Audit logging** (11 event types)
7. **Job archiving** to S3
8. **Security scanning** (CodeQL, Trivy, TruffleHog)
9. **Alert pipeline** with automated root-cause analysis

## Strengths DeerFlow Has That We Lack

1. **Sandbox execution** — agents can run code
2. **Progressive skill loading** — context-efficient
3. **IM channel integration** — Telegram/Slack/Feishu
4. **Embedded Python client** — use without HTTP
5. **Loop detection middleware** — prevents infinite loops
6. **Clarification-first workflow** — structured HITL
7. **File upload + conversion** — PDF/PPT/Excel/Word
8. **Config versioning** with auto-upgrade
9. **Full SaaS-quality frontend** — Next.js 16
10. **Harness/App boundary** — publishable library
