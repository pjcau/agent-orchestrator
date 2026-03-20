# 10 - Configuration System

## Overview

Paperclip uses a layered configuration approach: config file (JSON) → environment variables → sensible defaults. The system supports multiple deployment modes, database backends, secret providers, and storage backends.

## Config Loading

```typescript
// server/src/config.ts
export function loadConfig(): Config {
  const fileConfig = readConfigFile(); // JSON at PAPERCLIP_CONFIG path
  // For each setting: env var > config file > default
  return {
    deploymentMode: env ?? fileConfig ?? "local_trusted",
    databaseMode: fileConfig ?? "embedded-postgres",
    secretsProvider: env ?? fileConfig ?? "local_encrypted",
    storageProvider: env ?? fileConfig ?? "local_disk",
    // ... 30+ config fields
  };
}
```

## Deployment Modes

| Mode | Auth | Use case |
|------|------|----------|
| `local_trusted` | None (implicit board access) | Local development |
| `authenticated` | OAuth2 + API keys | Production / multi-user |

## Deployment Exposure

| Mode | Network | Hostname guard |
|------|---------|---------------|
| `private` | Local/VPN only | Hostname allowlist enforced |
| `public` | Internet-facing | Open (auth still required) |

## Database Modes

| Mode | Setup | Use case |
|------|-------|----------|
| `embedded-postgres` | Zero-config, data in `~/.paperclip/` | Local dev, single-user |
| `postgres` | External connection string | Production, multi-instance |

Embedded Postgres starts a bundled PostgreSQL binary automatically. Data persists in `PAPERCLIP_HOME/postgres/`.

## Secret Providers

| Provider | Storage | Encryption |
|----------|---------|------------|
| `local_encrypted` | DB + master key file | AES encryption |
| `aws_ssm` | AWS Systems Manager | AWS KMS (stub) |
| `hashicorp_vault` | HashiCorp Vault | Vault encryption (stub) |

## Storage Providers

| Provider | Backend | Use case |
|----------|---------|----------|
| `local_disk` | Filesystem | Local dev |
| `s3` | AWS S3 / compatible | Production |

## Environment Variables

All settings can be overridden via `PAPERCLIP_*` env vars:
- `PAPERCLIP_DEPLOYMENT_MODE`
- `PAPERCLIP_SECRETS_PROVIDER`
- `PAPERCLIP_STORAGE_PROVIDER`
- `DATABASE_URL`
- `HOST`, `PORT`
- etc.

## .env File Loading

```typescript
// Load from PAPERCLIP_HOME/.env first
loadDotenv({ path: resolvePaperclipEnvPath(), override: false });
// Then CWD/.env (if different file)
loadDotenv({ path: resolve(process.cwd(), ".env"), override: false });
```

## Key Patterns
- Layered config: env > file > default (no surprises)
- Zero-config local development (embedded Postgres, local storage, no auth)
- Production-ready with external Postgres, S3, OAuth2
- Config file is pure JSON (not YAML or TOML)
- Path resolution with `~` support (`resolveHomeAwarePath`)

## Relevance to Our Project
Our `ConfigManager` and `YAMLConfigLoader` use YAML. Paperclip's JSON + env approach is simpler. The zero-config embedded Postgres is a major DX win — our dev setup requires Docker for Postgres. The deployment mode concept (local_trusted vs authenticated) is cleaner than our separate auth middleware configuration.
