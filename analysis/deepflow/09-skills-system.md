# 09 - Skills System

## Concept

Skills are structured capability modules — Markdown files that define workflows, best practices, and references. They are NOT code; they are instructions the agent follows.

## Directory Structure

```
skills/
├── public/                    # Shipped with DeerFlow (committed)
│   ├── deep-research/SKILL.md
│   ├── report-generation/SKILL.md
│   ├── slide-creation/SKILL.md
│   ├── web-design-guidelines/SKILL.md
│   ├── frontend-design/SKILL.md
│   ├── image-generation/SKILL.md
│   ├── video-generation/SKILL.md
│   ├── data-analysis/SKILL.md
│   ├── chart-visualization/SKILL.md
│   ├── consulting-analysis/SKILL.md
│   ├── podcast-generation/SKILL.md
│   ├── ppt-generation/SKILL.md
│   ├── claude-to-deerflow/SKILL.md
│   ├── github-deep-research/SKILL.md
│   ├── skill-creator/SKILL.md
│   ├── find-skills/SKILL.md
│   ├── surprise-me/SKILL.md
│   ├── bootstrap/SKILL.md
│   └── vercel-deploy-claimable/SKILL.md
└── custom/                    # User-installed (gitignored)
    └── your-skill/SKILL.md
```

## SKILL.md Format

```yaml
---
name: Deep Research
description: Comprehensive research workflow
license: MIT
allowed-tools:
  - web_search
  - web_fetch
  - read_file
  - write_file
  - bash
---

# Deep Research Skill

## Workflow
1. Analyze the research question
2. Search for relevant sources
3. Fetch and read key articles
...
```

## Progressive Loading

This is a key innovation — skills are NOT loaded into the system prompt all at once.

1. Agent system prompt lists available skills with names/descriptions
2. When a task matches a skill, agent calls `read_file` on the SKILL.md
3. Agent reads the workflow instructions
4. Additional resources referenced in the skill are loaded on-demand
5. Only the relevant skill content enters the context window

```xml
<skill_system>
  <available_skills>
    <skill>
      <name>deep-research</name>
      <description>Comprehensive research workflow</description>
      <location>/mnt/skills/public/deep-research/SKILL.md</location>
    </skill>
    ...
  </available_skills>
</skill_system>
```

## Enabled/Disabled State

Stored in `extensions_config.json`:
```json
{
  "skills": {
    "deep-research": {"enabled": true},
    "slide-creation": {"enabled": false}
  }
}
```

Managed via Gateway API: `PUT /api/skills/{name}`

## Skill Installation

- `POST /api/skills/install` — accepts `.skill` ZIP archives
- Extracts to `custom/` directory
- Accepts standard frontmatter: `version`, `author`, `compatibility`

## Key Differences from Our Skills

| Aspect | DeerFlow Skills | Our Skills |
|--------|----------------|------------|
| Nature | Markdown instructions | Executable code |
| Loading | Progressive (on-demand) | All at once |
| Execution | Agent reads & follows | Direct invocation |
| Middleware | None | retry, logging, timeout, cache |
| Registry | File-based scanning | Code-based registry |
| Format | SKILL.md (YAML frontmatter) | Python functions |

DeerFlow's approach is more flexible (any skill is just instructions) but less programmatic (can't compose skills in code).
