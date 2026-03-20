# 16 - Security

## Overview

Paperclip implements security at multiple layers: authentication, authorization, middleware guards, secret management, log redaction, and input validation.

## Authentication

### Board Users (Human)
- **better-auth** library for OAuth2/password auth
- Session-based with `authSessions`, `authAccounts`, `authVerifications` tables
- Session resolution via cookie or Authorization header

### Agents
- **API keys**: `pcp_` prefix, SHA256 hashed before storage
- **JWT**: Local adapter auth via `agent-auth-jwt.ts`
- Keys are per-agent with revocation tracking (`revokedAt`)

```typescript
function createToken() {
  return `pcp_${randomBytes(24).toString("hex")}`;
}
function hashToken(token: string) {
  return createHash("sha256").update(token).digest("hex");
}
```

## Authorization Middleware

### Actor Middleware
```typescript
actorMiddleware(db, { deploymentMode, resolveSession })
```
Resolves the request actor (board user or agent) and attaches to `req.actor`.

### Board Mutation Guard
```typescript
boardMutationGuard()
```
Restricts agent API keys to safe operations. Agents cannot: delete companies, manage other agents, change budgets, etc.

### Private Hostname Guard
```typescript
privateHostnameGuard({ enabled, allowedHostnames, bindHost })
```
In private deployment mode, rejects requests from non-allowed hostnames. Prevents exposure if the server is accidentally made public.

## Secret Management

### Local Encrypted Provider
Secrets encrypted with AES using a master key file:
```
~/.paperclip/secrets.key → master key
DB: companySecrets → encrypted values
DB: companySecretVersions → version history
```

### Provider Registry
Pluggable: `local_encrypted` (default), `aws_ssm` (stub), `hashicorp_vault` (stub).

## Log Redaction

```typescript
// server/src/log-redaction.ts
redactCurrentUserText(text, { enabled: censorUsernameInLogs })
```

Redacts usernames and sensitive data from logs. Applied to activity logs and approval comments.

```typescript
// server/src/redaction.ts
sanitizeRecord(record) // Redacts sensitive config fields
```

Fields matching patterns (password, secret, token, key, apiKey) are replaced with `[REDACTED]`.

## Forbidden Token Detection

```typescript
// Prevents injection of forbidden tokens in user-facing text
```

## Input Validation

- **Zod** schemas for request body validation
- **AJV** for JSON Schema validation (plugin configs)
- **DOMPurify** for HTML sanitization

## WebSocket Security

Auth required on upgrade:
```typescript
server.on("upgrade", (req, socket, head) => {
  authorizeUpgrade(db, req, companyId, url, opts)
    .then(context => { /* allow */ })
    .catch(() => { rejectUpgrade(socket, "403 Forbidden") });
});
```

## Key Patterns
- Defense in depth (auth → authz middleware → service-level checks)
- Hash-before-store for API keys
- Pluggable secret providers (local → cloud migration path)
- Log redaction to prevent data leaks
- WebSocket auth on upgrade (not post-connection)
- Hostname allowlist for private deployments

## Relevance to Our Project
Our auth is OAuth2 + API key middleware. Paperclip's security is more comprehensive: hostname guards, board mutation guards, log redaction, forbidden token detection. The pluggable secret provider pattern (local → AWS SSM → Vault) is a good migration path. Our WebSocket doesn't authenticate on upgrade — a security gap we should fix.
