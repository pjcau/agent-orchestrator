# 23 - Security

## Path Traversal Prevention

### Virtual Path Enforcement
```python
if not path.startswith(f"{VIRTUAL_PATH_PREFIX}/"):
    raise PermissionError("Only paths under /mnt/user-data/ are allowed")
```

### Path Resolution Validation
```python
resolved = Path(resolved_path).resolve()
for root in allowed_roots:
    resolved.relative_to(root)  # ValueError if traversal
```

### Bash Command Path Validation
```python
def validate_local_bash_command_paths(command, thread_data):
    # Regex finds all absolute paths in command
    # Only /mnt/user-data/ and system paths (/bin/, /usr/bin/) allowed
    # Rejects anything else
```

## Sandbox Isolation

### Local Mode
- Path translation masks real filesystem paths
- Output masking: actual paths → virtual paths in command output
- System path allowlist for executables

### Docker Mode
- Full container isolation
- Mounted volumes only: user-data, skills
- No host filesystem access

### Kubernetes Mode
- Pod-level isolation
- Namespace separation
- k3s for local development

## Thread Isolation

Each thread gets isolated directories:
```
backend/.deer-flow/threads/{thread_id}/user-data/
├── workspace/
├── uploads/
└── outputs/
```

No cross-thread file access possible.

## Memory Safety

### Upload Filtering
Memory updater strips file-upload references to prevent the agent from searching for non-existent files in future sessions.

### Prompt Injection Protection
`test_memory_prompt_injection.py` — tests that memory content can't inject malicious instructions.

## API Security

### File Upload
- Path validation before storage
- No directory traversal in upload paths
- 500KB file size limits for preview

### CORS
- Configurable origins via `CORS_ORIGINS` env var
- Default: localhost only

## Authentication

### Frontend
- `better-auth` for session management
- `BETTER_AUTH_SECRET` for session security

### No API Authentication
The Gateway API has **no authentication** by default. This is a notable gap — anyone with network access can:
- Read/write MCP config
- Install skills
- Access memory data
- Upload files to any thread

## Key Security Gaps

1. **No API authentication** on Gateway endpoints
2. **Local sandbox** runs commands directly on host (dev only, but risky)
3. **MCP environment variables** stored in config files (not encrypted)
4. **No rate limiting** on API endpoints
5. **No audit logging** for API operations

## Comparison with Our Security

Our orchestrator has more security infrastructure:
- OAuth2 + API key auth on dashboard
- RBAC (admin/developer/viewer roles)
- JWT authentication
- Audit logging (11 event types)
- Security scanning CI (pip-audit, CodeQL, Trivy, TruffleHog)
- OWASP-aware security auditor agent

DeerFlow's sandbox isolation is stronger, but API security is weaker.
