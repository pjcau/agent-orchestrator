# `ago` — Rust CLI client

Status: **experimental** (Phase 1 of a 4-phase roadmap — see
[unified-roadmap.md](unified-roadmap.md)).

The `ago` CLI lets developers talk to a remote Agent Orchestrator from any
local project, the same way `gh` or `vercel` work. It lives in the
[`cli/`](../cli/) directory at the repo root and ships as a single static
binary independent from the Python harness.

## Why Rust?

| Concern | Why it matters | How Rust addresses it |
|---|---|---|
| Distribution | One binary per OS, no Python runtime required on the user's machine | Static binary via `rustc`, `cargo install` or Homebrew |
| Cold start | A CLI gets invoked dozens of times per day; ~300 ms of Python startup is noticeable | Rust starts under ~50 ms |
| Token handling | Secrets must not leak through dumps or logs | `secrecy::SecretString` + `zeroize` + OS keychain |
| TLS | The server URL is user-supplied, so no surprises with system OpenSSL builds | `rustls` + vendored `webpki-roots` |
| Cross-platform builds | macOS / Linux / Windows | `cargo build --target <triple>` |

## Architecture

```
cli/
├── Cargo.toml              # standalone crate (not part of the rust/ PyO3 workspace)
├── README.md
├── src/
│   ├── main.rs             # thin tokio::main → lib::run()
│   ├── lib.rs              # Cli parsing, Runtime, init_logging, report_error
│   ├── error.rs            # AgoError + Result + exit code mapping
│   ├── cli.rs              # clap command tree
│   ├── config.rs           # ~/.config/ago/config.toml + URL validation
│   ├── auth.rs             # TokenStorage trait + KeyringStorage + EnvOverride + MemoryStorage
│   ├── client.rs           # reqwest + rustls API client (whoami)
│   └── commands/           # one file per subcommand
│       ├── login.rs
│       ├── logout.rs
│       ├── whoami.rs
│       └── config.rs
└── tests/cli_integration.rs # assert_cmd end-to-end tests against wiremock
```

The CLI talks to the dashboard over `/api/cli/v1/*` endpoints implemented in
[`src/agent_orchestrator/dashboard/cli_routes.py`](../src/agent_orchestrator/dashboard/cli_routes.py).
Authentication is delegated to the existing `APIKeyMiddleware`, so the CLI
re-uses the same API keys configured for browser/programmatic dashboard
access — no separate identity store.

## Install

### Prebuilt binaries

`v0.1.0` is the first tagged release. Pick the archive for your platform
from [the GitHub Release](https://github.com/pjcau/agent-orchestrator/releases/tag/v0.1.0),
extract it, and put `ago` on `$PATH`:

| Target | Archive |
|---|---|
| macOS arm64 (M-series) | `ago-v0.1.0-aarch64-apple-darwin.tar.gz` |
| macOS x86_64 (Intel) | `ago-v0.1.0-x86_64-apple-darwin.tar.gz` |
| Linux x86_64 (static musl) | `ago-v0.1.0-x86_64-unknown-linux-musl.tar.gz` |
| Linux arm64 (static musl) | `ago-v0.1.0-aarch64-unknown-linux-musl.tar.gz` |
| Windows x86_64 | `ago-v0.1.0-x86_64-pc-windows-msvc.zip` |

Verify against `SHA256SUMS` (or the per-archive `ago.sha256` inside
each tarball) before installing.

### From source

```bash
cd cli && cargo install --path . --locked
```

## Shipped surface

| Command | Description |
|---|---|
| `ago config set/get/show/path` | Inspect/modify `~/.config/ago/config.toml` (mode 0600). |
| `ago login [--server URL] [--key-env VAR] [--with-stdin]` | Persist an API key in the OS keychain after validating it against `/api/cli/v1/whoami`. |
| `ago login --device [--server URL] [--no-browser]` | RFC 8628 device-flow: the CLI prints a URL + pairing code, opens it in your browser, polls until you approve, then stores the ephemeral token in the keychain. |
| `ago logout [--server URL]` | Remove the stored token for the active or given server. |
| `ago whoami` | Print the authenticated identity from the server. |
| `ago run "<task>" --agent NAME --model ID [--provider TYPE] [--max-steps N] [--json] [--stream]` | Execute a single-agent task. Reads task from stdin if omitted. Default is blocking against `/api/agent/run`; `--stream` switches to SSE via the dedicated `/api/cli/v1/run` endpoint and renders a live progress spinner on a tty. |
| `ago jobs list [--limit N] [--json]` | Show recent server sessions with record counts and the first prompt. |
| `ago jobs show <session_id> [--json]` | Print the records of a single session (job log). |
| `ago jobs cancel <job_id>` | Request cancellation of a running team job. |
| `ago completions <shell>` | Emit a shell completion script (`bash`, `zsh`, `fish`, `powershell`, `elvish`). |

## Security model

- **TLS only.** `https://` everywhere except `http://localhost` and
  `http://127.0.0.1` (development). Enforced in `config::validate_server_url`
  and again in `client::ApiClient::new`.
- **Tokens never on disk in plaintext.** The default chain is:
  1. `AGO_TOKEN` env var (read-only, useful in CI), then
  2. OS keychain via the `keyring` crate.
- **Tokens never logged.** `reqwest::HeaderValue::set_sensitive(true)` masks
  the `X-API-Key` header in `tracing` output. `secrecy::SecretString` wraps
  in-memory tokens and zeroizes on drop.
- **Strict config parsing.** TOML deserialization uses
  `#[serde(deny_unknown_fields)]` to reject foreign keys.
- **0600 config file** on Unix.
- **Fail-closed login.** A login attempt that fails server-side validation
  never persists the token.

## Testing

| Layer | What it covers | How to run |
|---|---|---|
| Unit | `Config`, `validate_*`, `MemoryStorage`, `EnvOverrideStorage`, `ApiClient` (against `wiremock`) | `cd cli && cargo test --lib` |
| Integration | Binary exec via `assert_cmd`, end-to-end against `wiremock` mock servers | `cd cli && cargo test --test cli_integration` |
| Server-side | `/api/cli/v1/*` route shape, auth enforcement | `pytest tests/test_cli_routes.py` |

CI: [`.github/workflows/cli-rust.yml`](../.github/workflows/cli-rust.yml) runs
`cargo fmt --check`, `cargo clippy -- -D warnings`, `cargo test` on
ubuntu/macos/windows, and `cargo audit` on every push to `experiment/**`,
PRs touching `cli/**`, and manual dispatch.

## Next phases

| Phase | Adds | ETA |
|---|---|---|
| 1.5 / 2 | Device-flow OAuth (RFC 8628), `ago run` with SSE streaming, `.ago.yaml` project preset | Phase 2 of the [unified roadmap](unified-roadmap.md#rust-cli-ago) |
| 3 | `ago jobs list/get/cancel`, `ago logs --follow`, indicatif progress, shell completions | — |
| 4 | Cross-compile matrix (macOS arm/x64, Linux x64/arm64 musl, Windows), signed releases via cosign + SBOM, Homebrew tap | release v0.1.0 |

## Per-project preset (`.ago.yaml`)

Drop a `.ago.yaml` (or `.ago.yml`) at the root of any project and the CLI will
pick it up automatically — the file is searched walking up from the current
working directory.

```yaml
# .ago.yaml
server: https://orch.example.com   # optional — overrides ~/.config/ago/config.toml
agent: backend
model: claude-sonnet-4-6
provider: anthropic
max_steps: 20
```

Resolution order (highest priority first):

1. The CLI flag (`--agent`, `--model`, `--provider`, `--max-steps`).
2. `.ago.yaml` walked up from `cwd`.
3. Global config (`~/.config/ago/config.toml`: `server`, `default_agent`).
4. Built-in defaults.

The schema rejects unknown keys (`#[serde(deny_unknown_fields)]`) and validates
the `server` URL the same way `ago config set server` does — typos fail at
load time instead of being silently ignored.

## Device-flow OAuth (RFC 8628)

`ago login --device` is the recommended way to authenticate from a new
device — the API key never appears in your terminal, shell history, or
clipboard.

```
$ ago login --device --server https://orch.example.com
To authorize this device, open:
    https://orch.example.com/api/cli/v1/auth/device?user_code=ABCD-EFGH

and confirm the pairing code:  ABCD-EFGH

Waiting for approval (Ctrl-C to cancel)...
....
Authenticated as alice@example.com on https://orch.example.com
```

How the four endpoints split:

| Endpoint | Auth | Used by |
|---|---|---|
| `POST /api/cli/v1/auth/device-start` | **anonymous** (in `EXEMPT_PREFIXES`) | CLI — request a pairing |
| `POST /api/cli/v1/auth/device-poll` | **anonymous** (in `EXEMPT_PREFIXES`) | CLI — poll for the token |
| `GET /api/cli/v1/auth/device?user_code=…` | JWT session required | Browser — render approval page |
| `POST /api/cli/v1/auth/device/approve` | JWT session required | Browser — submit approval |

### Token model

Approving binds the resulting **JWT** (signed with `JWT_SECRET_KEY`) to the
authenticated user's identity (`name`, `email`, `role`, `provider:
"device-flow"`). The middleware accepts the JWT in either `Authorization`
(future) or `X-API-Key` (current). The token is **stateless** — no per-token
row on the server — so it works:

- **Across restarts.** Anyone holding a valid JWT continues to authenticate
  as long as `JWT_SECRET_KEY` does not change.
- **Across workers.** No shared in-memory state is consulted on each
  request; every worker that knows the secret accepts the same JWT.

Default TTL is **30 days** — override with `AGO_CLI_TOKEN_TTL_SECONDS`. To
revoke a leaked token, rotate `JWT_SECRET_KEY` (invalidates *all* tokens —
session cookies included). A future phase will add a small denylist for
per-token revocation without secret rotation.

### Pairing state (multi-worker correctness)

The pairing-state store (the `device_code → user_info` mapping used
between `device-start` and `device-poll`) is pluggable:

| Store | Multi-worker | Restart-safe | Selected when |
|---|---|---|---|
| `InMemoryDeviceFlowStore` (default) | ❌ — per-process state | ❌ | `DATABASE_URL` is unset |
| `PostgresDeviceFlowStore` | ✅ | ✅ | `DATABASE_URL` is set |

The Postgres backend creates a `cli_device_flows` table lazily on first
use (no manual migration) and reaps expired rows via `cleanup`. Schema is
flat — `device_code` PK, `user_code` unique, `status`, JSONB
`user_info`, plus timestamps.

### Production checklist

For a hardened multi-worker deployment:

- Set `JWT_SECRET_KEY` to a 32+ byte random string (rotate at incident).
- Set `DATABASE_URL` so the pairing store is shared and persistent.
- Set `OAUTH_CLIENT_ID` (GitHub) or `GOOGLE_OAUTH_CLIENT_ID` so the
  browser-side approval page can authenticate the user.
- Optional: set `AGO_CLI_TOKEN_TTL_SECONDS` shorter than the 30-day
  default for stricter rotation policies.

## SSE event shape (`/api/cli/v1/run`)

```
event: start
data: {"run_id": "...", "agent": "...", "model": "...", "provider": "..."}

event: agent.spawn
data: {"agent": "backend", "node": null, "data": {...}, "ts": ...}

event: agent.tool_call
data: {...}

# Periodic ": keepalive" comments are sent on idle to keep proxies open.

event: complete
data: {"run_id": "...", "success": true, "output": "...", "elapsed_s": ...,
       "total_input_tokens": ..., "total_output_tokens": ..., "total_cost_usd": ...}
```

The endpoint allocates a private `EventBus` per request — concurrent CLI runs
cannot leak events into each other's streams, and dashboard event feeds are
isolated from CLI runs by design.

## Limits acknowledged in current revision

- Token revocation requires `JWT_SECRET_KEY` rotation (no per-token
  denylist yet). Acceptable as long as token lifetime is short or
  compromised tokens are rare; a denylist may land later if needed.
- `ago logs <id> --follow` is **not implemented yet** — for now use
  `ago jobs show <session_id>` to read a session's records once it has
  finished. Live tailing of an active team job needs a server-side SSE
  endpoint filtered by `job_id` and will land alongside Phase 4 or v0.2.
- No `--local` fallback (subprocess Python `client.py`) yet.
- No update channel; rely on `brew upgrade` / `cargo install --force`.
