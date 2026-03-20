# 20 - Company Portability

## Overview

Paperclip's company portability system enables exporting and importing entire companies — org charts, agents, skills, projects, issues, and assets. This is the foundation for the planned "ClipMart" marketplace.

## Export Process

1. **Collect manifest** — Agents, projects, issues, skills, goals
2. **Scrub secrets** — Remove sensitive config fields (API keys, tokens)
3. **Generate README** — Auto-generated documentation of the company
4. **Render org chart** — SVG → PNG of the organizational hierarchy
5. **Package files** — Attached assets, skill files, documents
6. **Bundle** — ZIP archive with manifest.json + files

## Export Manifest

```typescript
interface CompanyPortabilityManifest {
  agents: CompanyPortabilityAgentManifestEntry[];    // name, slug, role, reportsTo, adapterType, config
  projects: CompanyPortabilityProjectManifestEntry[]; // name, status, workspaces
  issues: CompanyPortabilityIssueManifestEntry[];     // title, status, priority, assignee
  skills: CompanyPortabilitySkillManifestEntry[];     // name, markdown, compatibility
}
```

## Import Process

1. **Parse manifest** — Validate structure
2. **Preview** — Show what will be created/updated
3. **Detect collisions** — Agent name conflicts, project name conflicts
4. **Apply strategy** — Per-entity collision handling:
   - `replace` — Overwrite existing
   - `rename` — Add suffix to avoid conflict
   - `skip` — Don't import
5. **Create entities** — Agents, projects, issues, skills
6. **Remap IDs** — Internal ID references → new IDs

## Org Chart in Export

The export includes a visual org chart:

```typescript
function buildOrgTreeFromManifest(agents) {
  const ROLE_LABELS = {
    ceo: "Chief Executive", cto: "Technology",
    cmo: "Marketing", cfo: "Finance", ...
  };
  // Build tree from reportsToSlug relationships
  // Render as SVG → PNG
}
```

## Key Patterns
- Template-based company creation (export → share → import)
- Secret scrubbing in exports (safety by default)
- Collision detection with user-chosen strategies
- Auto-generated documentation (README + org chart) in exports
- Slug-based references instead of UUIDs (portable)

## Relevance to Our Project
Our `ConfigManager` supports export/import of orchestrator configs but not at the "company" granularity. The secret scrubbing pattern is important — our config exports could leak API keys. The collision strategy pattern (replace/rename/skip) is more user-friendly than our current "overwrite everything" approach.
