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

```mermaid
graph TD
    TL["team-lead (sonnet)<br/>orchestrator, coordinates all categories"]

    style TL fill:#4a90d9,color:#fff
```

### Software Engineering (6 agents)

```mermaid
graph TD
    SE["Software Engineering"]
    SE --> BE["backend (sonnet)<br/>API, database, server logic"]
    SE --> FE["frontend (sonnet)<br/>UI, state management, styling"]
    SE --> DO["devops (sonnet)<br/>Docker/OrbStack, CI/CD, infra"]
    SE --> PE["platform-engineer (sonnet)<br/>system design, scalability"]
    SE --> AI["ai-engineer (opus)<br/>LLM integration, prompts"]
    SE --> SC["scout (opus)<br/>GitHub pattern discovery"]

    style SE fill:#4a90d9,color:#fff
    style AI fill:#e6a23c,color:#fff
    style SC fill:#e6a23c,color:#fff
```

### Data Science (5 agents)

```mermaid
graph TD
    DS["Data Science"]
    DS --> DA["data-analyst (sonnet)<br/>EDA, statistical testing, visualization"]
    DS --> ML["ml-engineer (opus)<br/>model training, evaluation, MLOps"]
    DS --> DE["data-engineer (sonnet)<br/>ETL pipelines, data warehousing, quality"]
    DS --> NLP["nlp-specialist (opus)<br/>text processing, embeddings, NER, RAG"]
    DS --> BI["bi-analyst (sonnet)<br/>dashboards, KPI metrics, data storytelling"]

    style DS fill:#7bc67e,color:#fff
    style ML fill:#e6a23c,color:#fff
    style NLP fill:#e6a23c,color:#fff
```

### Finance (5 agents)

```mermaid
graph TD
    FIN["Finance"]
    FIN --> FA["financial-analyst (sonnet)<br/>financial modeling, valuation, forecasting"]
    FIN --> RA["risk-analyst (opus)<br/>VaR, stress testing, regulatory compliance"]
    FIN --> QD["quant-developer (opus)<br/>algorithmic trading, backtesting, signals"]
    FIN --> CO["compliance-officer (sonnet)<br/>audit trails, KYC/AML, policy enforcement"]
    FIN --> AC["accountant (sonnet)<br/>bookkeeping, reconciliation, tax prep"]

    style FIN fill:#d94a4a,color:#fff
    style RA fill:#e6a23c,color:#fff
    style QD fill:#e6a23c,color:#fff
```

### Marketing (5 agents)

```mermaid
graph TD
    MKT["Marketing"]
    MKT --> CS["content-strategist (sonnet)<br/>content planning, brand voice, SEO copy"]
    MKT --> SEO["seo-specialist (sonnet)<br/>keyword research, technical SEO, links"]
    MKT --> GH["growth-hacker (opus)<br/>acquisition funnels, A/B tests, CRO"]
    MKT --> SM["social-media-manager (sonnet)<br/>social strategy, community, paid social"]
    MKT --> EM["email-marketer (sonnet)<br/>campaigns, automation, segmentation"]

    style MKT fill:#9b59b6,color:#fff
    style GH fill:#e6a23c,color:#fff
```

## Cross-Agent Dependencies

### Software Engineering

```mermaid
graph LR
    BE["Backend"] <-->|"API contracts, data models"| FE["Frontend"]
    BE <-->|"database, caching, queues"| PE["Platform"]
    DO["DevOps"] <-->|"Docker, CI/CD, deployment"| BE & FE & PE & AI
    AI["AI-Eng"] <-->|"provider implementations"| BE
    SC["Scout"] -->|"discovers patterns, creates PRs"| BE & FE & DO & PE & AI
```

### Data Science

```mermaid
graph LR
    DA["Data-Analyst"] <-->|"feature discovery, model validation"| ML["ML-Engineer"]
    DE["Data-Engineer"] <-->|"pipeline outputs feed all"| DA & ML & NLP & BI
    NLP["NLP-Specialist"] <-->|"text features, embeddings"| ML
    BI["BI-Analyst"] <-->|"metrics definitions, data sources"| DA
```

### Finance

```mermaid
graph LR
    FA["Financial-Analyst"] <-->|"valuation inputs, risk metrics"| RA["Risk-Analyst"]
    QD["Quant-Developer"] <-->|"portfolio risk, position limits"| RA
    CO["Compliance-Officer"] <-->|"regulatory checks"| FA & RA & QD & AC
    AC["Accountant"] <-->|"financial statements, budgets"| FA
```

### Marketing

```mermaid
graph LR
    CS["Content-Strategist"] <-->|"keyword-driven content"| SEO["SEO-Specialist"]
    GH["Growth-Hacker"] <-->|"experiment design"| CS & SEO & SM & EM
    SM["Social-Media-Manager"] <-->|"content distribution"| CS
    EM["Email-Marketer"] <-->|"funnel automation, nurture flows"| GH
```
