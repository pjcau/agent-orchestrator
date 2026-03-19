# Security

This document describes the security architecture of the Agent Orchestrator, covering authentication, authorization, secret management, network protection, and deployment hardening.

## Security Model

The orchestrator follows a **fail-closed** security model: all requests are denied by default unless explicitly authenticated. There is no implicit "dev mode" — unauthenticated access must be opted into with `ALLOW_DEV_MODE=true`, and this flag is automatically blocked when `ENVIRONMENT=production`.

```
Internet → ALB/CloudFront → Frontend (static) → Dashboard API → LLM Providers
                                                       ↑
                                               Authentication required
```

## Authentication

Two authentication methods are supported, used independently or together.

### 1. API Key Authentication (programmatic access)

API keys are passed via the `X-API-Key` HTTP header. Query parameter authentication (`?api_key=...`) is explicitly disabled to prevent key leakage through server logs, browser history, referer headers, and CDN logs.

```bash
# Correct
curl -H "X-API-Key: your-key" https://agents.example.com/api/agents

# Rejected (query params not accepted)
curl https://agents.example.com/api/agents?api_key=your-key
```

**Configuration:**

```bash
# Comma-separated list of valid API keys
DASHBOARD_API_KEYS="key1,key2,key3"
```

Keys should be generated with sufficient entropy (at least 32 bytes / 64 hex chars):

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### 2. OAuth2 + JWT Sessions (browser access)

Browser-based users authenticate via GitHub OAuth2. On successful login, a JWT session cookie is set.

**Session cookie properties:**

| Property | Value | Rationale |
|----------|-------|-----------|
| `httponly` | `true` | Prevents JavaScript access (XSS protection) |
| `secure` | `true` | Only sent over HTTPS |
| `samesite` | `strict` | Prevents CSRF attacks |
| `max_age` | `14400` (4 hours) | Limits window of exposure for stolen cookies |

**JWT token structure:**

```json
{
  "sub": "user@example.com",
  "name": "User Name",
  "provider": "github",
  "github_login": "username",
  "role": "developer",
  "iat": 1710000000,
  "exp": 1710014400
}
```

**Configuration:**

```bash
OAUTH_CLIENT_ID="github-oauth-app-id"
OAUTH_CLIENT_SECRET="github-oauth-app-secret"
JWT_SECRET_KEY="64-char-random-hex-string"
BASE_URL="https://agents.example.com"
```

The `JWT_SECRET_KEY` has no default fallback. If not set, the application generates a random key at startup (sessions will not persist across restarts). For production, always set a stable 256-bit key.

### WebSocket Authentication

WebSocket endpoints (`/ws` and `/ws/stream`) check authentication **before** accepting the connection. Unauthenticated WebSocket upgrade requests are rejected with close code `1008` (Policy Violation).

Authentication methods for WebSocket connections:
1. `X-API-Key` header in the upgrade request
2. `auth_session` JWT cookie (automatic for browser clients)

```javascript
// Browser: cookie is sent automatically
const ws = new WebSocket("wss://agents.example.com/ws");

// Programmatic: pass API key via protocols or custom header
const ws = new WebSocket("wss://agents.example.com/ws", {
  headers: { "X-API-Key": "your-key" }
});
```

### Unauthenticated Endpoints

The following paths bypass authentication (required for the auth flow and health checks):

| Path | Purpose |
|------|---------|
| `/health` | Load balancer health checks |
| `/static/*` | CSS, JS, images |
| `/auth/*` | OAuth2 login/callback flow |
| `/login` | Login page |
| `/api/models` | Model listing (needed before auth for UI) |

## Authorization (RBAC)

Three roles control access to resources:

| Role | Permissions |
|------|-------------|
| **admin** | Full access: config, agents, projects, users, dashboard, audit |
| **developer** | Read config, read/write/execute agents, read projects/users/dashboard/audit |
| **viewer** | Read-only: config, agents, projects, dashboard |

The admin user is identified by the `GITHUB_USERNAME` environment variable and is auto-created on first OAuth login. All other users must be explicitly approved by the admin via the `/api/admin/pending/{login}/approve` endpoint.

### Permission Matrix

```
config.read      ✓ admin  ✓ developer  ✓ viewer
config.write     ✓ admin
agents.read      ✓ admin  ✓ developer  ✓ viewer
agents.write     ✓ admin  ✓ developer
agents.execute   ✓ admin  ✓ developer
projects.read    ✓ admin  ✓ developer  ✓ viewer
projects.write   ✓ admin
users.read       ✓ admin  ✓ developer
users.write      ✓ admin
dashboard.read   ✓ admin  ✓ developer  ✓ viewer
audit.read       ✓ admin  ✓ developer
```

## Password Hashing

Passwords are hashed with **bcrypt** (cost factor 12, ~260ms per hash). This provides:

- **Adaptive cost**: resistant to GPU/ASIC brute-force attacks
- **Per-hash salt**: identical passwords produce different hashes
- **Time-tested**: industry standard since 1999

If bcrypt is not installed, the system falls back to SHA-256 with a random 16-byte salt per hash. Legacy hashes (fixed-salt SHA-256 from versions before v1.1) are still verified for migration but should be re-hashed.

```bash
# Install bcrypt (required for production)
pip install bcrypt>=4.0
```

## Secret Management

### Environment Variables

All secrets are loaded from environment variables, never from code or config files.

| Variable | Required | Description |
|----------|----------|-------------|
| `JWT_SECRET_KEY` | Production | 256-bit key for signing JWT tokens |
| `DASHBOARD_API_KEYS` | Production | Comma-separated API keys |
| `OAUTH_CLIENT_ID` | If using OAuth | GitHub OAuth app client ID |
| `OAUTH_CLIENT_SECRET` | If using OAuth | GitHub OAuth app client secret |
| `OPENROUTER_API_KEY` | If using OpenRouter | OpenRouter API key |
| `DATABASE_URL` | If using Postgres | PostgreSQL connection string |

### Rules

1. **Never commit secrets** — `.env`, `.env.local`, `.env.*` are in `.gitignore`
2. **Never pass secrets in URLs** — API keys are header-only
3. **Never log secrets** — error messages are sanitized; `OPENROUTER_API_KEY` is masked in CI logs
4. **Rotate regularly** — especially after any suspected exposure
5. **Use AWS Secrets Manager** (or equivalent) in production — not plain environment variables

### CI/CD Secrets

GitHub Actions uses:
- `OPENROUTER_API_KEY` — stored as a GitHub repository secret
- `GITHUB_TOKEN` — auto-provided by GitHub Actions
- `GH_USERNAME` — stored as a GitHub repository variable (not a secret, public info)

The CI workflow masks `OPENROUTER_API_KEY` in logs with `::add-mask::`.

## Network Security

### CORS (Cross-Origin Resource Sharing)

CORS is configured with an explicit origin allowlist. No wildcard (`*`) origins are permitted.

```bash
# Comma-separated list of allowed origins
CORS_ALLOWED_ORIGINS="https://agents.example.com,https://admin.example.com"

# If not set, defaults to BASE_URL (single origin)
```

Allowed methods: `GET`, `POST`, `PATCH`, `DELETE`
Allowed headers: `X-API-Key`, `Content-Type`, `Authorization`
Credentials: enabled (required for cookie-based auth)

### SSRF Protection

The Ollama base URL is validated against a strict allowlist to prevent Server-Side Request Forgery:

```python
_OLLAMA_ALLOWED_PREFIXES = (
    "http://localhost",
    "http://127.0.0.1",
    "http://host.docker.internal",
    "http://ollama",  # Docker service name
)
```

Any `OLLAMA_BASE_URL` not matching these prefixes is rejected with a `ValueError`.

### Path Traversal Protection

File access endpoints (`/api/files`, `/api/file`) validate that resolved paths stay within `PROJECT_ROOT`:

```python
if not str(target).startswith(str(base)):
    return JSONResponse({"error": "Path outside project"}, status_code=400)
```

File reads are also limited to 100KB to prevent resource exhaustion.

## Dev Mode

Dev mode disables all authentication. It exists for local development only.

```bash
# Enable dev mode (local development only)
ALLOW_DEV_MODE=true

# Blocked in production
ENVIRONMENT=production ALLOW_DEV_MODE=true  # → dev mode is NOT enabled
```

When dev mode is active, a `WARNING` log is emitted at startup:

```
SECURITY: Dev mode enabled (ALLOW_DEV_MODE=true).
All endpoints are unauthenticated. Do NOT use in production.
```

When `ALLOW_DEV_MODE=true` is set with `ENVIRONMENT=production`, an `ERROR` log is emitted and dev mode is blocked:

```
SECURITY: ALLOW_DEV_MODE=true is BLOCKED in production.
Remove ALLOW_DEV_MODE or set ENVIRONMENT to something else.
```

## Audit Logging

Authentication events are logged with Python's `logging` module:

| Event | Log Level | Example |
|-------|-----------|---------|
| Login success | `INFO` | `AUTH login success: pjcau (role=admin)` |
| Login failure | `WARNING` | `AUTH login failed: GitHub authentication failed` |
| Logout | `INFO` | `AUTH logout: pjcau` |
| Auth denied | `WARNING` | `Auth denied: GET /api/agents from 192.168.1.100` |
| Dev mode active | `WARNING` | `SECURITY: Dev mode enabled...` |
| Dev mode blocked | `ERROR` | `SECURITY: ALLOW_DEV_MODE=true is BLOCKED in production` |

In production, pipe logs to a centralized logging system (CloudWatch, Datadog, ELK) for alerting and compliance.

## AWS Deployment Checklist

Before deploying to AWS with the frontend exposed:

### Secrets

- [ ] Generate a 64-char random `JWT_SECRET_KEY` — store in AWS Secrets Manager
- [ ] Generate strong `DASHBOARD_API_KEYS` — store in AWS Secrets Manager
- [ ] Create a new GitHub OAuth App with the production `BASE_URL`
- [ ] Store `OAUTH_CLIENT_ID` and `OAUTH_CLIENT_SECRET` in AWS Secrets Manager
- [ ] Store `OPENROUTER_API_KEY` in AWS Secrets Manager
- [ ] Set `ENVIRONMENT=production` in the container/task definition
- [ ] Verify `ALLOW_DEV_MODE` is NOT set (or is `false`)

### Network

- [ ] Set `BASE_URL` to the production domain (`https://agents.example.com`)
- [ ] Set `CORS_ALLOWED_ORIGINS` to the frontend domain(s)
- [ ] Place the dashboard behind an ALB with HTTPS termination
- [ ] Use security groups to restrict direct access to the container
- [ ] Ensure Postgres is in a private subnet (no public access)

### Authentication

- [ ] Verify `DASHBOARD_API_KEYS` is set (empty = auth required, but no valid keys)
- [ ] Verify OAuth flow works end-to-end with the production callback URL
- [ ] Set `GITHUB_USERNAME` to the admin's GitHub login
- [ ] Test that unauthenticated requests to `/api/agents` return 401

### Monitoring

- [ ] Configure CloudWatch log groups for auth events
- [ ] Set up alerts for repeated `Auth denied` log entries (brute-force detection)
- [ ] Monitor LLM API costs via the `/api/usage` endpoint
- [ ] Set up budget alerts in OpenRouter/Anthropic dashboards

### Infrastructure

- [ ] Use ECS Fargate or EKS (no SSH access to containers)
- [ ] Enable container image scanning (ECR)
- [ ] Use IAM roles for task execution (no static credentials)
- [ ] Enable VPC flow logs for network audit trail
- [ ] Configure WAF rules on the ALB (rate limiting, IP filtering)

## Threat Model

### Attack Surface

| Component | Exposure | Mitigations |
|-----------|----------|-------------|
| Dashboard API | Internet (via ALB) | Auth middleware, CORS, rate limiting (WAF) |
| WebSocket | Internet (via ALB) | Pre-accept auth check, CORS |
| OAuth callback | Internet | CSRF via state param (authlib), strict samesite cookie |
| LLM providers | Outbound only | API keys in env vars, not in code |
| Postgres | Private subnet | Security groups, no public access |
| Ollama | Localhost only | SSRF allowlist validation |
| File system | API-mediated | Path traversal check, 100KB limit |

### Key Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Stolen API key | Unauthorized API access | Rotate keys, header-only (no logs), short-lived sessions |
| Stolen JWT cookie | Session hijack | 4h expiry, httponly, secure, samesite=strict |
| OpenRouter quota abuse | Financial loss | Auth on all endpoints including WebSocket, budget alerts |
| Shell skill execution | Remote code execution | Auth required, skill allowlist, working directory isolation |
| SSRF via Ollama URL | Internal network scanning | URL prefix allowlist |
| XSS in dashboard | Cookie theft, session hijack | httponly cookies, CSP headers (TODO) |

## Automated Security Fixes

A daily GitHub Action (`.github/workflows/security-autofix.yml`) runs at 03:00 UTC:

1. Fetches open code scanning alerts via GitHub API
2. Runs `scripts/fix_security_alerts.py` to apply automated fixes
3. Runs the test suite to verify fixes
4. Creates a PR with the fixes if any changes were made

Supported fix categories:
- **Log injection** (`py/log-injection`): sanitizes user-controlled values with `_sanitize_log()`
- **Path injection** (`py/path-injection`): adds `..` traversal checks before path construction
- **Weak hashing** (`py/weak-sensitive-data-hashing`): upgrades SHA-256 to PBKDF2-SHA256

Password hashing uses bcrypt (cost=12) by default, with PBKDF2-SHA256 fallback when bcrypt is not installed. Legacy SHA-256 hashes are still verified for migration but new hashes always use the stronger algorithm.

## Code Execution Sandbox

Agent-generated code runs inside isolated Docker containers (`core/sandbox.py`).

**Isolation controls**:
- **Memory limit** — configurable (default: 512m)
- **CPU limit** — configurable (default: 1.0 core)
- **Network** — disabled by default (`--network=none`)
- **Filesystem** — writable paths use tmpfs mounts; all others read-only
- **Timeout** — hard kill after configurable seconds (default: 60s)

**Path traversal protection**:
- All file operations validate paths against allowed roots
- `..` components are rejected before any filesystem access
- Virtual path mapping translates host paths to container paths

**Usage**: `SandboxedShellSkill` in `SkillRegistry` wraps the sandbox as a standard agent skill. The `agent_runner.py` accepts an optional `sandbox` parameter to enable sandboxed execution.

## Future Improvements

- **Rate limiting** — Add `slowapi` middleware for per-IP rate limiting on auth endpoints
- **Content Security Policy** — Add CSP headers to prevent XSS
- **OAuth token revocation** — Revoke GitHub access token on logout
- **API key scoping** — Per-key permission restrictions (read-only keys, agent-specific keys)
- **mTLS** — Mutual TLS between services for zero-trust internal communication
- **Secrets rotation** — Automated key rotation via AWS Secrets Manager Lambda
