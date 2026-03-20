# 19 - Deployment

## Overview

Paperclip supports three deployment modes: local development (zero-config), Docker, and production (external Postgres + S3).

## Local Development

```bash
npx paperclipai onboard --yes
# OR
git clone ... && pnpm install && pnpm dev
```

Zero-config setup:
- Embedded PostgreSQL (bundled binary, data in `~/.paperclip/postgres/`)
- Local disk storage (data in `~/.paperclip/storage/`)
- Local encrypted secrets (key in `~/.paperclip/secrets.key`)
- No auth required (`local_trusted` mode)
- Vite dev server with HMR for UI

## Docker Deployment

```dockerfile
FROM node:lts-trixie-slim AS base
# Multi-stage build: deps → build → production

# Production stage installs agent CLIs globally:
RUN npm install --global @anthropic-ai/claude-code@latest \
  @openai/codex@latest opencode-ai

ENV NODE_ENV=production
ENV PAPERCLIP_DEPLOYMENT_MODE=authenticated
VOLUME ["/paperclip"]
EXPOSE 3100
CMD ["node", "server/dist/index.js"]
```

Key Docker decisions:
- Agent CLIs (Claude Code, Codex, OpenCode) installed globally in container
- Data volume at `/paperclip` (Postgres data, storage, secrets)
- Single container runs everything (server, UI, embedded Postgres)
- Port 3100

## Production Configuration

```env
PAPERCLIP_DEPLOYMENT_MODE=authenticated
PAPERCLIP_DEPLOYMENT_EXPOSURE=private  # or public
DATABASE_URL=postgres://...            # external Postgres
PAPERCLIP_STORAGE_PROVIDER=s3
PAPERCLIP_STORAGE_S3_BUCKET=...
PAPERCLIP_SECRETS_PROVIDER=local_encrypted
```

## Database Backup

Built-in backup for embedded Postgres:
```typescript
{
  databaseBackupEnabled: true,
  databaseBackupIntervalMinutes: 60,   // backup every hour
  databaseBackupRetentionDays: 30,     // keep 30 days
  databaseBackupDir: "~/.paperclip/backups/"
}
```

## HMR Configuration

For development mode, Vite HMR port is computed:
```typescript
function resolveViteHmrPort(serverPort: number): number {
  if (serverPort <= 55_535) return serverPort + 10_000;
  return Math.max(1_024, serverPort - 10_000);
}
```
Server on port 3100 → HMR on port 13100.

## Key Patterns
- Zero-config local dev (single command)
- All-in-one Docker container (simple, no orchestration needed)
- Agent CLIs bundled in Docker image
- Built-in database backup
- Graduated deployment: local → Docker → production

## Relevance to Our Project
Our deployment uses docker-compose with separate containers (postgres, dashboard, nginx, etc.). Paperclip's single-container approach is simpler for solo operators. The embedded Postgres eliminates our Docker requirement for local dev. The agent CLI bundling in Docker is practical — we don't bundle agent runtimes. The graduated deployment path (local → Docker → production) is well-designed.
