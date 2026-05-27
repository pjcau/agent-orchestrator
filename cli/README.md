# ago — Agent Orchestrator CLI

Talk to a remote Agent Orchestrator from any local project.

> Status: **experimental** — Phase 1 ships `login`, `logout`, `whoami`, `config`.
> `run` and SSE streaming arrive in Phase 2.

## Install (from source)

```bash
cd cli
cargo install --path . --locked
```

This puts `ago` on your `$PATH`.

## Authenticate

```bash
# 1. Configure the server (saved to ~/.config/ago/config.toml on Linux,
#    ~/Library/Application Support/io.agent-orchestrator.ago/ on macOS).
ago config set server https://orch.example.com

# 2. Provide an API key — recommended path is via env var for CI:
export AGO_API_KEY=ago_pat_xxxxx
ago login --key-env AGO_API_KEY

# Or pipe via stdin (no shell history):
printf 'ago_pat_xxxxx' | ago login --with-stdin

# Or interactive (visible echo — only when no other option):
ago login
```

Tokens are stored in the OS keychain (macOS Keychain, Linux Secret Service,
Windows Credential Manager). In environments without a keychain, set
`AGO_TOKEN` directly and the CLI will use it without touching the keychain.

## Verify

```bash
ago whoami
# alice@example.com (developer) — https://orch.example.com
```

## Logout

```bash
ago logout
```

## Security notes

- Only `https://` URLs are accepted as servers (exception: `http://localhost`
  and `http://127.0.0.1` for development).
- Tokens in memory live in `secrecy::SecretString` and are zeroized on drop.
- Config files are written `0600` (owner-only) on Unix.
- The CLI does **not** phone home with telemetry.
- All HTTP goes through `rustls` (no OpenSSL); CA roots are vendored from
  `webpki-roots` for reproducibility.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Generic error |
| 2 | No server / not authenticated |
| 3 | Network or 5xx server error |
| 4 | Authentication rejected (401) |
| 64 | Invalid usage / URL |
| 130 | Cancelled (Ctrl-C) |

## Configuration

| Key | Meaning |
|---|---|
| `server` | Active orchestrator URL (`https://...`) |
| `default_agent` | Default agent for future `ago run` (Phase 2) |

| Env var | Meaning |
|---|---|
| `AGO_TOKEN` | Read-only override; bypasses keychain. Useful in CI. |
| `AGO_LOG` | `tracing_subscriber` env-filter (e.g. `ago=debug`). |
