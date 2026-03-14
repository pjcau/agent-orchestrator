# 01 - Project Overview

## Identity

DeerFlow 2.0 is a **super agent harness** — not a framework you wire together, but a batteries-included runtime that gives agents infrastructure to actually get work done. Built by ByteDance, open-sourced under MIT.

## Evolution: v1 -> v2

- **v1**: Deep Research framework — primarily web research and report generation
- **Community pushed it further**: data pipelines, slide decks, dashboards, content workflows
- **v2**: Ground-up rewrite. No shared code with v1. Now a general-purpose agent harness.

The v1 branch is maintained at `main-1.x` but active development is entirely on v2.

## Core Philosophy

> "DeerFlow 2.0 is no longer a framework you wire together. It's a super agent harness — batteries included, fully extensible."

Key design principles:
1. **Agent has a computer** — full filesystem, sandboxed execution, not just tool access
2. **Skills are progressively loaded** — only when needed, keeping context lean
3. **Sub-agents for parallel work** — complex tasks decomposed, delegated, synthesized
4. **Memory persists across sessions** — user preferences, knowledge, workflows
5. **Model-agnostic** — any OpenAI-compatible LLM works

## Positioning

DeerFlow sits between:
- **Low-level frameworks** (LangChain, LangGraph) — too much assembly required
- **Closed-platform agents** (ChatGPT, Claude) — not extensible enough
- **Other harnesses** (CrewAI, AutoGen) — DeerFlow adds sandbox, skills, memory as first-class

## What It Can Do

Out of the box:
- Deep research with web search and report generation
- Slide deck creation (PPT)
- Web page/dashboard generation
- Image and video generation
- Data analysis with file uploads
- Code execution in sandboxed environments
- Custom workflows via skills

## Repo Stats

- Python 3.12+ required
- ~200+ source files (backend + frontend)
- 17 built-in public skills
- 40+ backend tests
- LangGraph as the runtime engine
- Next.js 16 + React 19 frontend
