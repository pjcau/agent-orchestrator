# Research Scout Findings: prompts.chat Prompt Library Patterns

**Date:** 2026-03-08
**Source:** https://github.com/f/prompts.chat
**Scout:** automated research pass
**Evaluation Score:** 0.8 / 1.0

---

## 1. What is prompts.chat

[prompts.chat](https://github.com/f/prompts.chat) is the world's largest open-source prompt library.

| Metric | Value |
|--------|-------|
| GitHub stars | 143 000+ |
| Contributors | 360+ |
| License | CC0 1.0 (public domain) |
| Formats | CSV, Markdown, Hugging Face dataset, MCP server |

### 1.1 Prompt Types

The library defines seven prompt types:

| Type | Description |
|------|-------------|
| `TEXT` | Standard text generation prompts |
| `IMAGE` | Image generation prompts (Stable Diffusion, DALL-E, etc.) |
| `VIDEO` | Video generation prompts |
| `AUDIO` | Audio/music generation prompts |
| `STRUCTURED` | Prompts that expect structured (JSON/XML/CSV) output |
| `SKILL` | Prompts that encode a reusable skill or persona |
| `TASTE` | Stylistic preference prompts (tone, voice, format) |

### 1.2 Data Format (prompts.csv)

```csv
act,prompt,for_devs,type,contributor
"Ethereum Developer","Imagine you are an experienced Ethereum developer...",TRUE,TEXT,ameya-2003
"Linux Terminal","I want you to act as a linux terminal...",TRUE,SKILL,fatihkagan
"REST API Designer","You are a senior backend engineer...",TRUE,STRUCTURED,contributor-x
```

Fields:
- `act` — short human-readable name / role label
- `prompt` — the full prompt text
- `for_devs` — boolean flag (developer-oriented vs general audience)
- `type` — one of the seven types listed above
- `contributor` — GitHub handle of the author

### 1.3 MCP Server

The project ships `@fkadev/prompts.chat-mcp`, an MCP server that exposes the entire library as MCP tools and resources. Any MCP-compatible client (VS Code Copilot, Cursor, Claude Code, etc.) can query prompts directly.

---

## 2. Why This Matters for Agent Orchestrator

### 2.1 Current State (the problem)

The Agent Orchestrator has 23 agents defined across the codebase. Their system prompts are:

- Stored as free-form text inside `.claude/agents/*.md` markdown files
- Not versioned (no history of prompt changes)
- Not tagged or categorized (no way to discover "all API-related prompts")
- Not searchable programmatically
- Not exposed via the dashboard or any API endpoint
- Not reusable across agents

This means that improving a prompt requires knowing exactly which file to edit, and there is no mechanism to compare versions, roll back, or share prompts between agents.

### 2.2 What prompts.chat Solves

The prompts.chat architecture demonstrates:

1. **Centralized registry** — one place to find all prompts, regardless of agent or domain
2. **Typed schema** — every prompt has a declared type, enabling filtering and validation
3. **Contributor attribution** — who wrote the prompt, enabling accountability
4. **Tagging and categorization** — fast lookup by use-case, domain, or model
5. **Version tracking** — iterate on prompts while preserving history
6. **Multiple distribution formats** — CSV for spreadsheets, JSON for APIs, MCP for IDE tools
7. **Scale proof** — 360+ contributors and 143k stars prove the pattern works in production

---

## 3. Proposed Implementation for Agent Orchestrator

### 3.1 New Module

```
src/agent_orchestrator/core/prompt_library.py
```

Internal structure:

```
prompt_library.py
├── PromptType          (Enum: TEXT, IMAGE, VIDEO, AUDIO, STRUCTURED, SKILL, TASTE, SYSTEM)
├── PromptVersion       (dataclass: version int, content str, created_at str, author str)
├── Prompt              (dataclass: id, name, category, tags, for_agents, type, versions, contributor)
├── PromptLibrary       (registry: add, find_by_agent, find_by_tags, find_by_type, search, export_csv)
└── PromptStore         (persistence: JSON file / SQLite / PostgreSQL backend)
```

### 3.2 Prompt JSON Schema (proposed)

```json
{
  "id": "backend-api-design-001",
  "name": "REST API Designer",
  "category": "software-engineering",
  "tags": ["api-design", "rest", "backend", "openapi"],
  "for_agents": ["backend", "platform-engineer"],
  "type": "SYSTEM",
  "current_version": 1,
  "versions": [
    {
      "version": 1,
      "content": "You are an experienced backend engineer specializing in REST API design...",
      "created_at": "2026-03-08",
      "author": "ai-engineer"
    }
  ],
  "contributor": "ai-engineer",
  "created_at": "2026-03-08"
}
```

### 3.3 System Architecture

```
┌─────────────────────────────────────────────────────┐
│                   PromptLibrary                     │
│                                                     │
│   find_by_agent("backend")   → List[Prompt]         │
│   find_by_tags(["api"])      → List[Prompt]         │
│   find_by_type(SYSTEM)       → List[Prompt]         │
│   add_version(id, content)   → PromptVersion        │
│   search("REST API design")  → List[Prompt]         │
│   export_csv()               → str (CSV text)       │
│                                                     │
│   ┌─────────────────────────────────────────────┐   │
│   │               PromptStore                  │   │
│   │  JSON file  /  SQLite  /  PostgreSQL        │   │
│   └─────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
              │                    │
              ▼                    ▼
  ┌──────────────────┐   ┌──────────────────────────┐
  │  REST API        │   │  MCPServerRegistry       │
  │  /api/v1/prompts │   │  (expose as MCP tools)   │
  └──────────────────┘   └──────────────────────────┘
              │
              ▼
  ┌──────────────────────────┐
  │  Dashboard               │
  │  Prompt Browser Widget   │
  └──────────────────────────┘
```

### 3.4 REST API Endpoints (to add to api.py)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/prompts` | List all prompts (paginated) |
| `GET` | `/api/v1/prompts/search?q=...` | Full-text search |
| `GET` | `/api/v1/prompts/{id}` | Get prompt by ID |
| `GET` | `/api/v1/prompts/{id}/versions` | Version history |
| `POST` | `/api/v1/prompts` | Add a new prompt |
| `POST` | `/api/v1/prompts/{id}/versions` | Add a new version |
| `GET` | `/api/v1/prompts?agent=backend` | Filter by agent |
| `GET` | `/api/v1/prompts?type=SYSTEM` | Filter by type |
| `GET` | `/api/v1/prompts?tags=api,rest` | Filter by tags |

### 3.5 MCP Server Integration

The `MCPServerRegistry` in `src/agent_orchestrator/core/mcp_server.py` can be extended to:

1. **Expose our prompts as MCP resources** — so IDE tools (VS Code, Cursor) can query agent prompts from the library
2. **Consume the upstream `@fkadev/prompts.chat-mcp` server** — import the full public library as a read-only source

This lets external tools discover and apply our agent system prompts without needing access to the codebase.

---

## 4. Migration Plan: Existing Agent Prompts

The current agent system prompts live in `.claude/agents/*.md`. The migration path:

1. Parse each markdown file to extract the system prompt block
2. Create a `Prompt` entry in the library with:
   - `type = SYSTEM`
   - `for_agents = [<agent-name>]`
   - `category = software-engineering`
   - `contributor = team-lead`
3. Write the JSON prompt store to `src/agent_orchestrator/core/prompts/`
4. Update `agents_registry.py` to load system prompts from `PromptLibrary` instead of hard-coded strings

No existing files need to be deleted immediately. The registry can serve as the canonical source while the markdown files are kept as human-readable documentation.

---

## 5. Implementation Phases

| Phase | Work | Effort |
|-------|------|--------|
| 1 | `Prompt` dataclass + `PromptLibrary` with JSON backend | 2 hours |
| 2 | Extract existing 23 agent prompts into library JSON format | 1 hour |
| 3 | Search and filter API endpoints in `api.py` and `app.py` | 2 hours |
| 4 | Version tracking (`add_version`, `get_versions`) | 1 hour |
| 5 | Dashboard prompt browser widget (HTML/JS) | 3 hours |
| 6 | MCP server: expose prompts as MCP resources | 2 hours |
| 7 | MCP client: consume `@fkadev/prompts.chat-mcp` upstream | 2 hours |

Total estimated effort: ~13 hours across two sprints.

Phases 1-4 are the minimum viable implementation and can be done independently of the dashboard and MCP work.

---

## 6. Evaluation

| Criterion | Score | Notes |
|-----------|-------|-------|
| Applicable | 0.7 | Pattern is directly useful; not a drop-in integration |
| Quality | 0.9 | 143k stars, 360+ contributors, production-proven format |
| Compatible | 0.6 | Requires new module; patterns to adopt, not copy-paste code |
| Safe | 1.0 | CC0 public domain license, no security concerns |
| **Overall** | **0.8** | High value, medium integration effort |

---

## 7. References

- Repository: https://github.com/f/prompts.chat
- Dataset (Hugging Face): https://huggingface.co/datasets/fka/awesome-chatgpt-prompts
- MCP server package: `@fkadev/prompts.chat-mcp`
- License: CC0 1.0 Universal (https://creativecommons.org/publicdomain/zero/1.0/)
- Related Agent Orchestrator modules: `mcp_server.py`, `api.py`, `agents_registry.py`, `graph_templates.py`
