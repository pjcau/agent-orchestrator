---
name: website-dev
description: Develop, build, and preview the project documentation website. Use this for docs updates, site builds, and local preview.
disable-model-invocation: true
allowed-tools: Bash, Read, Write, Edit, Glob
---

# Website Dev — Documentation Site

Manage the project documentation and website.

## Local preview (if docs framework is set up)
```bash
# Docusaurus
cd website && npm run start

# MkDocs
mkdocs serve

# Simple HTTP server for static docs
python3 -m http.server 8000 --directory docs/
```

## Build
```bash
# Via Docker
docker compose run --rm app mkdocs build

# Local
mkdocs build
```

## Key directories

- `docs/` — documentation source (architecture, cost-analysis, infrastructure, migration)
- `website/` — website source (if using a framework like Docusaurus)

## Conventions

- All documentation in English
- Use Markdown with GitHub-flavored extensions
- Include code examples for all concepts
- Keep docs in sync with code changes
