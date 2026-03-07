---
sidebar_position: 3
title: Agents
---

# Agents

An agent is a stateless unit that receives a task, uses tools, and returns a result.

```python
@dataclass
class AgentConfig:
    name: str
    role: str                          # system prompt / persona
    provider: str                      # provider key
    tools: list[str]                   # allowed tool names
    max_steps: int = 10                # anti-stall: hard step limit
    max_retries_per_approach: int = 3  # anti-stall: retry cap
```

Agents are **provider-parameterized** — the same agent definition can run on Claude, GPT, or a local model by swapping the provider.

## Agent Categories

Agents are organised by **category** under `.claude/agents/<category>/`.
The `team-lead` lives at root level and coordinates all categories.

```
team-lead (sonnet) ──── orchestrator, coordinates all categories
```

### Software Engineering (6 agents)

```
.claude/agents/software-engineering/
  ├── backend (sonnet) ──────── API, database, server logic
  ├── frontend (sonnet) ─────── UI, state management, styling
  ├── devops (sonnet) ───────── Docker/OrbStack, CI/CD, infra
  ├── platform-engineer (sonnet) system design, scalability
  ├── ai-engineer (opus) ────── LLM integration, prompts
  └── scout (opus) ──────────── GitHub pattern discovery
```

### Data Science (5 agents)

```
.claude/agents/data-science/
  ├── data-analyst (sonnet) ──── EDA, statistical testing, visualization
  ├── ml-engineer (opus) ─────── model training, evaluation, MLOps
  ├── data-engineer (sonnet) ─── ETL pipelines, data warehousing, quality
  ├── nlp-specialist (opus) ──── text processing, embeddings, NER, RAG
  └── bi-analyst (sonnet) ────── dashboards, KPI metrics, data storytelling
```

### Finance (5 agents)

```
.claude/agents/finance/
  ├── financial-analyst (sonnet) ── financial modeling, valuation, forecasting
  ├── risk-analyst (opus) ─────── VaR, stress testing, regulatory compliance
  ├── quant-developer (opus) ──── algorithmic trading, backtesting, signals
  ├── compliance-officer (sonnet)  audit trails, KYC/AML, policy enforcement
  └── accountant (sonnet) ──────── bookkeeping, reconciliation, tax prep
```

### Marketing (5 agents)

```
.claude/agents/marketing/
  ├── content-strategist (sonnet) ── content planning, brand voice, SEO copy
  ├── seo-specialist (sonnet) ────── keyword research, technical SEO, links
  ├── growth-hacker (opus) ─────── acquisition funnels, A/B tests, CRO
  ├── social-media-manager (sonnet)  social strategy, community, paid social
  └── email-marketer (sonnet) ────── campaigns, automation, segmentation
```

## Cross-Agent Dependencies

### Software Engineering
```
Backend  ↔ Frontend:  API contracts, data models
Backend  ↔ Platform:  database, caching, queues
DevOps   ↔ All:       Docker, CI/CD, deployment
AI-Eng   ↔ Backend:   provider implementations
Scout    →  All:       discovers patterns, creates PRs
```

### Data Science
```
Data-Analyst ↔ ML-Engineer:  feature discovery, model validation
Data-Engineer ↔ All:         pipeline outputs feed all analysis
NLP-Specialist ↔ ML-Engineer: text features, embedding models
BI-Analyst ↔ Data-Analyst:   metrics definitions, data sources
```

### Finance
```
Financial-Analyst ↔ Risk-Analyst:  valuation inputs, risk metrics
Quant-Developer ↔ Risk-Analyst:   portfolio risk, position limits
Compliance-Officer ↔ All:         regulatory checks on all outputs
Accountant ↔ Financial-Analyst:   financial statements, budgets
```

### Marketing
```
Content-Strategist ↔ SEO-Specialist: keyword-driven content
Growth-Hacker ↔ All:                 experiment design across channels
Social-Media-Manager ↔ Content:      content distribution
Email-Marketer ↔ Growth-Hacker:      funnel automation, nurture flows
```
