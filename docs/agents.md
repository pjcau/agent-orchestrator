# Agents & Skills

Catalog of the 34 agents, their categories, cross-dependencies, the skills map, and the research scout workflow.

Agents live under `.claude/agents/<category>/`. Root-level agents live directly in `.claude/agents/`.

## Root-Level Agents (6)

```
.claude/agents/
  ├── team-lead (sonnet) ──────── orchestrator, coordinates all categories
  ├── architect (sonnet) ──────── codebase architecture analysis
  ├── code-reviewer (sonnet) ──── code quality and security review
  ├── dependency-checker (sonnet)  dependency updates, vulnerabilities, unused packages
  ├── migration-helper (sonnet) ── database migrations, API versioning, breaking changes
  └── test-runner (sonnet) ──────── run tests after code changes
```

### team-lead routing rules

`team-lead` decomposes a task into sub-agent assignments (the plan prompt lives
in `run_team` in `dashboard/agent_runner.py`). The plan prompt enforces:

- **Route by file ownership, not keyword.** Application / server / API /
  business-logic code → `backend`; UI / components / styling → `frontend`;
  **only** Dockerfile, docker-compose, CI/CD, deploy scripts and infra config →
  `devops`. A failing build / run / test command is usually a code or config
  bug — team-lead identifies the file that must change and assigns its **owner**
  instead of defaulting to `devops` just because the task mentions a command,
  Docker, or the word "fix". (Regression fixed 2026-06-16: an agent-host
  session routed every turn to `devops`, which only read files and never wrote
  any.)
- **Outcome first.** Each assignment is phrased as a concrete edit ("add X to
  file Y"), never as "investigate" / "analyze" / "diagnose" / "review" — unless
  the user explicitly asked only for analysis.
- **Current-turn anchor.** team-lead plans for the user's *latest* message;
  earlier conversation is background only. A new or terse instruction in a
  context saturated by a prior task (e.g. "write a rules file" after a long
  test-fixing session) defines THIS turn's goal — team-lead switches to it
  instead of continuing the previous task by inertia. The sub-agent steer
  carries the matching "stay on this task" rule so the executor doesn't drift
  back either. (Added after a 16-byte instruction was swallowed by a 900k-token
  context, 2026-06-16.)
- **Fan out across layers** when a change spans them (API + UI → `backend` AND
  `frontend`); otherwise keep it to a single agent.
- **Bug-fix / debug / "make it work" tasks → one owning agent, never a team.**
  These need a tight edit→run→read-error→fix→re-run loop on a single shared
  context. A parallel fan-out makes 3-4 agents re-read the same files, burns the
  step budget on redundant exploration, and hits the step cap before any fix
  converges (observed 2026-06-16: a `--client-tools` debug task fanned out to
  backend+frontend+devops+code-reviewer and was halted at the 60-step cap with 0
  fixes applied). Fan-out is reserved for genuinely multi-component feature work.

Every sub-agent additionally carries an **outcome requirement** plus a
**convergence loop** (the `_MINIMAL_CHANGES_STEER` suffix appended to its role):
a fix / implement / build task that ends with analysis but no `file_write` is a
failure (analysis-/review-/audit-only tasks are exempt); and a "make it work /
pass / build / run" task must be driven to green in a loop — apply the smallest
fix, **run the verification command and read its real error output**, fix the
specific cause, and re-run until it exits cleanly, installing prerequisites
first and never re-issuing the identical failing command or re-reading
already-read files. Covered by `tests/test_prompt_rules.py`.

> **Note on `--client-tools` convergence.** The server-side `RepairLoop`
> (`core/repair_loop.py`, wired into `/api/team/run`) verifies the workspace
> *on the server*; under `ago chat --client-tools` the files live on the
> operator's machine, so convergence is driven by the agent's own
> shell-delegated verify→fix loop (the steer above), not the server gate.

## Software Engineering (8 agents)

```
.claude/agents/software-engineering/
  ├── backend (sonnet) ──────── API, database, server logic
  ├── frontend (sonnet) ─────── UI, state management, styling
  ├── devops (sonnet) ───────── Docker/OrbStack, CI/CD, infra
  ├── platform-engineer (sonnet) system design, scalability, observability
  ├── ai-engineer (opus) ────── LLM integration, prompt engineering
  ├── scout (opus) ──────────── GitHub pattern discovery
  ├── research-scout (opus) ─── Analyzes starred repos, proposes code improvements
  ├── security-auditor (opus) ─ Vulnerability scanning, OWASP, secrets detection
  └── test-engineer (sonnet) ─ Test specialist — fixes failing unit/sociable/integration/e2e tests, drives the suite to green
```

> **`test-engineer`** is the dedicated owner of the test suite. team-lead routes
> "make the tests pass / fix failing tests / add tests / improve coverage / flaky
> tests" to it instead of splitting that work across backend+frontend+devops. Its
> role (built by name in `_build_role_for_agent`, not from the generic SE
> category) carries the test taxonomy (solitary vs sociable unit, integration,
> e2e), the scoped run→read-failure→fix→re-run convergence loop, the deliberate
> **fix-the-test vs fix-the-code** judgement, and the hard rule never to weaken a
> test to make it pass. Defined in `.claude/agents/test-engineer.md`; covered by
> `tests/test_prompt_rules.py`.

### Cross-Agent Dependencies

```
Backend ↔ Frontend:  API contracts, data models
Backend ↔ Platform:  database, caching, queues
DevOps  ↔ All:       Docker, CI/CD, deployment
AI-Eng  ↔ Backend:   provider implementations, LLM integration
Scout   →  All:       discovers patterns, creates PRs for integration
Security → All:       audits code, deps, config for vulnerabilities
```

## Data Science (5 agents)

```
.claude/agents/data-science/
  ├── data-analyst (sonnet) ──── EDA, statistical testing, visualization
  ├── ml-engineer (opus) ─────── model training, evaluation, MLOps
  ├── data-engineer (sonnet) ─── ETL pipelines, data warehousing, quality
  ├── nlp-specialist (opus) ──── text processing, embeddings, NER, RAG
  └── bi-analyst (sonnet) ────── dashboards, KPI metrics, data storytelling
```

### Cross-Agent Dependencies

```
Data-Analyst ↔ ML-Engineer:  feature discovery, model validation
Data-Engineer ↔ All:         pipeline outputs feed all analysis
NLP-Specialist ↔ ML-Engineer: text features, embedding models
BI-Analyst ↔ Data-Analyst:   metrics definitions, data sources
```

## Finance (5 agents)

```
.claude/agents/finance/
  ├── financial-analyst (sonnet) ── financial modeling, valuation, forecasting
  ├── risk-analyst (opus) ─────── VaR, stress testing, regulatory compliance
  ├── quant-developer (opus) ──── algorithmic trading, backtesting, signals
  ├── compliance-officer (sonnet)  audit trails, KYC/AML, policy enforcement
  └── accountant (sonnet) ──────── bookkeeping, reconciliation, tax prep
```

### Cross-Agent Dependencies

```
Financial-Analyst ↔ Risk-Analyst:  valuation inputs, risk metrics
Quant-Developer ↔ Risk-Analyst:   portfolio risk, position limits
Compliance-Officer ↔ All:         regulatory checks on all outputs
Accountant ↔ Financial-Analyst:   financial statements, budgets
```

## Marketing (5 agents)

```
.claude/agents/marketing/
  ├── content-strategist (sonnet) ── content planning, brand voice, SEO copy
  ├── seo-specialist (sonnet) ────── keyword research, technical SEO, links
  ├── growth-hacker (opus) ─────── acquisition funnels, A/B tests, CRO
  ├── social-media-manager (sonnet)  social strategy, community, paid social
  └── email-marketer (sonnet) ────── campaigns, automation, segmentation
```

### Cross-Agent Dependencies

```
Content-Strategist ↔ SEO-Specialist: keyword-driven content
Growth-Hacker ↔ All:                 experiment design across channels
Social-Media-Manager ↔ Content:      content distribution
Email-Marketer ↔ Growth-Hacker:      funnel automation, nurture flows
```

## Healthcare (4 agents)

```
.claude/agents/healthcare/
  ├── _safety.md ─────────────────── canonical Hard Safety Rules (not an agent)
  ├── medical-advisor ─────────────── triage / orchestrator, general clinical Q&A
  ├── disease-specialist ──────────── structured disease dossiers (etiology → prognosis)
  ├── diagnostician ───────────────── Bayesian differential diagnosis engine
  └── clinical-pharmacist ─────────── drug class, dose ranges, interactions, monitoring
```

All four healthcare agents default to `deepseek/deepseek-v4-flash` via OpenRouter
(see `AGENT_DEFAULT_MODEL` in `src/agent_orchestrator/dashboard/agents_registry.py`).
The file `_safety.md` is the **single source of truth** for the shared safety
contract; every agent links to it and reproduces the 10 rules verbatim so the
contract survives even when the body is fed directly to the LLM. The CI test
`test_each_healthcare_agent_references_safety_doc` catches any drift.

The medical-advisor is the first agent with a **per-agent default model override**
(see `AGENT_DEFAULT_MODEL` in `src/agent_orchestrator/dashboard/agents_registry.py`).
By default it routes to `deepseek/deepseek-v4-flash` via OpenRouter — cheap, fast,
strong on reasoning — while every other agent retains the user-selected model.

Two DeepSeek V4 models are registered in `OpenRouterProvider.MODELS` and selectable
from the UI for any agent:

| Model | Input $/M | Output $/M | Context | Notes |
|-------|-----------|------------|---------|-------|
| `deepseek/deepseek-v4-flash` | 0.112 | 0.224 | 1.05M | Efficiency MoE — default for medical-advisor |
| `deepseek/deepseek-v4-pro`   | 0.435 | 0.870 | 1.05M | Large MoE (1.6T total / 49B active) — escalation tier |

### Hard Safety Rules

The medical-advisor enforces non-negotiable safety constraints:

1. Every reply opens with the disclaimer "Informational only — not a substitute
   for evaluation by a licensed clinician."
2. Emergency scenarios trigger an explicit escalation to local emergency services.
3. No individualized prescriptions — only drug-class / guideline-level discussion.
4. Clinical claims are cited or labeled "no citation available".
5. PII is refused; users are asked to redact and resend de-identified data.
6. Hallucinated dosages, lab values, or guideline numbers are forbidden.

## Tooling (1 agent)

```
.claude/agents/tooling/
  └── skillkit-scout (opus) ── searches SkillKit marketplace, installs skills
```

### Escalation Flow

```
Team-lead cannot route task → skillkit-scout searches 15,000+ skills
  → Found: install & assign to appropriate agent
  → Not found: report to user, suggest custom agent/skill
```

## Skills Map (19 total)

| Skill | Agent | Description |
|-------|-------|-------------|
| `/docker-build` | devops | Build and manage containers via OrbStack |
| `/test-runner` | all | Run pytest suite via Docker |
| `/lint-check` | all | Ruff linting and formatting checks |
| `/code-review` | all | Automated quality/security review |
| `/deploy` | devops | Container deployment via docker-compose |
| `/scout` | scout | GitHub pattern discovery |
| `/website-dev` | frontend | Documentation site development |
| `/verify` | all | Pre-PR quality gate (tests, lint, format, security, diff review) |
| `/cost-optimization` | ai-engineer | Review LLM API costs, routing, budget, retry efficiency |
| `/ship` | all | Full pipeline: test, lint, docs sync, commit, push |
| `/feature` | all | End-to-end feature dev: implement, user review loop, tests, SOLID review, docs, commit, push |
| `/fix` | all | Bug fix with mandatory regression tests, lint, deploy |
| `/doc` | all | Full docs review: audit all docs/ against codebase, fix stale/missing/inaccurate content |
| `/fetch-star-repos` | scout | Fetch GitHub starred repos for research scout analysis |
| `/research-scout` | research-scout | Analyze starred repos and propose code improvements |
| `/web-research` | all | Search the internet for solutions, docs, and best practices |
| `/analysis` | all | Deep-dive repo analysis: clone, explore, produce up to 30 MD files in analysis/<name>/ |
| `/epic` | all | Multi-phase epic: break large features into phased stories, execute each via /feature |

Skill guidelines: `.claude/skills/skill-guidelines.md`.

## Research Scout & Nightly Workflow

The `research-scout` analyzes **GitHub starred repos** (one per run) via LLM and proposes concrete code improvements as PRs. Token-efficient: one repo, one LLM call.

- **Source**: GitHub starred repos (fetched via `scripts/fetch_github_stars.py`)
- **Lookback**: 30 days (stars older than 30 days are ignored)
- **LLM backend**: `claude` CLI locally, OpenRouter (`tencent/hy3-preview` — same model the dashboard ChatInput auto-selects for multi-agent team runs, see `PREFERRED_CLOUD_MODEL` in `frontend/src/components/chat/ChatInput.tsx`) on CI. The free Qwen model was dropped after 4 consecutive nights of HTTP 429 in late May 2026; keeping the scout on the same model the UI runs on means analysis quality tracks day-to-day user experience. Override via the `SCOUT_MODEL` env var.
- **Analysis**: LLM compares repo's patterns against our codebase, proposes up to **30** improvements with code, each scored on `impact` / `effort` / `risk` and a composite `value_score` (0–10). Parser sorts by `value_score` desc and caps at `MAX_IMPROVEMENTS` (30), so the highest-value items always surface first. See `scripts/run_research_scout.py::_parse_improvements`.
- **Reprocessing existing PRs**: `python scripts/run_research_scout.py --url <github-repo-url>` re-runs the analysis for a specific repo (bypasses bookmarks). Add `--skip-state` to leave the state file untouched (useful for regenerating findings for an open research-scout PR)
- **State tracking**: `.claude/research-scout-state.json`. Each processed URL records `outcome` (`fetch-error` / `low-relevance` / `llm-error` / `no-improvements` / `improvements-found`) and a short `reason`, so any operator can answer "why didn't this turn into a PR?" without reading workflow logs. Legacy entries without `outcome` are classified at render time by parsing the `summary` prefix.
- **Transient errors are retried**: an HTTP 429 / 5xx / network failure from the LLM does NOT mark the URL as processed — the nightly cron picks it up again the next day. This avoids silently dropping repos when OpenRouter has a bad night.
- **Findings file**: `.claude/research-scout-findings.md` (ephemeral, gitignored — used only as PR body, never committed)
- **GitHub Actions**: `.github/workflows/nightly-research.yml` (runs at 02:00 UTC), `.github/workflows/alert-analysis.yml` (automated root-cause analysis on alert issues)
- **Scripts**:
  - `scripts/fetch_github_stars.py` — populates `.claude/bookmarks.json`
  - `scripts/run_research_scout.py` — analyzes one repo, updates state, optionally opens a PR
  - `scripts/explain_research_scout_history.py` — prints a markdown report of recent outcomes (used in the workflow step summary; also runnable locally: `python scripts/explain_research_scout_history.py --days 14`)
- **PR creation**: Handled by the CI workflow (`nightly-research.yml`). When findings exist, the workflow creates a branch `research-scout/YYYY-MM-DD-HHMM`, commits state files, pushes, and opens a PR with findings as body. State is always pushed to main.

GitHub vars/secrets needed: `GITHUB_USERNAME` (repo variable), `OPENROUTER_API_KEY` (secret, for LLM analysis), `GITHUB_TOKEN` (auto-provided).
