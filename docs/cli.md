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

## Shipped surface

| Command | Description |
|---|---|
| `ago config set/get/show/path` | Inspect/modify `~/.config/ago/config.toml` (mode 0600). |
| `ago login [--server URL] [--key-env VAR] [--with-stdin]` | Persist an API key in the OS keychain after validating it against `/api/cli/v1/whoami`. |
| `ago logout [--server URL]` | Remove the stored token for the active or given server. |
| `ago whoami` | Print the authenticated identity from the server. |
| `ago run "<task>" --agent NAME --model ID [--provider TYPE] [--max-steps N] [--json] [--stream]` | Execute a single-agent task. Reads task from stdin if omitted. Default is blocking against `/api/agent/run`; `--stream` switches to SSE via the dedicated `/api/cli/v1/run` endpoint and renders progress events to stderr. |

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

- Login uses an **API key paste** instead of the full device-flow promised in
  the design discussion. Device-flow lands in Phase 2c. The token validation
  step still proves the key works before storing it, so the security
  guarantee — "no token persisted unless the server accepts it" — already
  holds.
- No `--local` fallback (subprocess Python `client.py`) yet.
- No update channel; rely on `brew upgrade` / `cargo install --force`.
