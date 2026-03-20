# 22 - Skill System

## Overview

Paperclip manages skills at the company level and syncs them to agent runtimes. Skills are Markdown-based instructions (SKILL.md) that can be imported from GitHub, local files, or company portability exports.

## Company Skills

Skills stored in DB (`companySkills` table):

```typescript
interface CompanySkill {
  id: string;
  companyId: string;
  key: string;              // unique identifier
  slug: string;             // URL-friendly name
  name: string;
  description: string;
  markdown: string;         // SKILL.md content
  sourceType: "github" | "local" | "import" | "manual";
  sourceLocator: string;    // repo URL or file path
  sourceRef: string;        // git ref for updates
  trustLevel: "verified" | "community" | "unknown";
  compatibility: CompanySkillCompatibility;
  fileInventory: CompanySkillFileInventoryEntry[];
}
```

## Skill Sources

| Source | Method |
|--------|--------|
| GitHub | Import from `owner/repo/skills/name` |
| Local | Scan local directory |
| Import | Via company portability |
| Manual | Created in dashboard |

## Skill Sync

Skills are synced to each agent's runtime format:

```
Company Skills DB
       │
       ▼
  syncSkills(adapter)
       │
       ├─ Claude Code → .claude/skills/name/SKILL.md
       ├─ Codex → codex skill format
       ├─ Cursor → cursor skill format
       ├─ Gemini → gemini skill format
       └─ OpenCode → opencode skill format
```

Each adapter has `listSkills()` and `syncSkills()` methods.

## Built-in Skills

`.agents/skills/` in the repo:
- `release-changelog` — Generate release notes
- `doc-maintenance` — Documentation audit and updates
- `create-agent-adapter` — Guide for creating new adapters
- `release` — Release process management
- `pr-report` — PR analysis and reporting
- `company-creator` — Create company from repo

Each skill follows the `SKILL.md` + `references/` pattern.

## Skill Trust Levels

| Level | Meaning |
|-------|---------|
| `verified` | Official or reviewed skills |
| `community` | Community-contributed |
| `unknown` | Unreviewed imports |

## Key Patterns
- Central skill registry synced to per-adapter formats
- Multiple import sources (GitHub, local, manual)
- Trust levels for skill governance
- File inventory tracking per skill
- Adapter-agnostic skill definition (Markdown)

## Relevance to Our Project
Our skills are Claude-Code-specific (SKILL.md format). Paperclip's approach of central skill storage + per-adapter sync is more flexible — skills can be shared across different agent runtimes. The trust level concept is important for skill governance. The GitHub import pattern allows skill marketplaces (their planned "ClipMart").
