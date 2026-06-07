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

`ago-v0.1.0` is the first tagged release. CLI release tags are
namespaced (`ago-v...`) so they do not collide with the orchestrator's
own version tags. Pick the archive for your platform from
[the GitHub Release](https://github.com/pjcau/agent-orchestrator/releases/tag/ago-v0.1.0),
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
| `ago chat [--mode agent\|prompt] [--agent N] [--model ID] [--provider T] [--max-steps N] [--no-progress]` | Interactive REPL. `--mode agent` (default) routes through the tool-using agent loop (`/api/cli/v1/run`); `--mode prompt` does a direct LLM completion (`/api/prompt`) — better for chat-style models. Slash commands: `:mode`, `:agent`, `:model`, `:provider`, `:max-steps`, `:reset`/`:clear`, `:info`, `:help`, `:quit`/`:exit`. **Supports `@file` / `@dir/` references in any input — see below.** |
| `ago run "<task>"` (and all variants) | Same `@file` / `@dir/` expansion as `ago chat` happens on the task string before sending. |
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
ubuntu/macos/windows, `cargo audit`, and **`cargo deny check`** (advisories +
licenses + bans + sources, configured in [`cli/deny.toml`](../cli/deny.toml))
on every push to `main` / `experiment/**`, PRs touching `cli/**`, and
manual dispatch.

## Release artifact verification (v0.5.1+)

Every release tagged `ago-v*` produces a 5-target matrix
([`.github/workflows/cli-release.yml`](../.github/workflows/cli-release.yml)).
Each target ships three companion files:

| File | Verifies |
|---|---|
| `SHA256SUMS` | Archive integrity (`sha256sum -c SHA256SUMS`) |
| `<archive>.sig` + `<archive>.cert` | Cosign keyless signature — proves the archive was built by this repo's GitHub Actions run |
| `<archive>.cdx.json` | CycloneDX 1.5 SBOM listing every transitive dependency for supply-chain audit |

**Verify the signature** (cosign ≥ 2.0):

```bash
cosign verify-blob \
  --certificate ago-v0.5.1-x86_64-unknown-linux-musl.tar.gz.cert \
  --signature   ago-v0.5.1-x86_64-unknown-linux-musl.tar.gz.sig \
  --certificate-identity-regexp 'https://github.com/jonnycau/agent-orchestrator/.+' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ago-v0.5.1-x86_64-unknown-linux-musl.tar.gz
```

A successful run prints `Verified OK`. The cert identity is the workflow URL
of the build job, so a third party can audit exactly which run produced the
artifact — no shared keys, no `keys.openpgp.org` round-trip.

**Audit the SBOM** with any CycloneDX consumer:

```bash
jq '.components[] | {name, version, licenses}' ago-v0.5.1-x86_64-unknown-linux-musl.cdx.json
# Or load into dependency-track / OWASP Dependency-Check / Trivy.
```

## Next phases

| Phase | Adds | ETA |
|---|---|---|
| 1.5 / 2 | Device-flow OAuth (RFC 8628), `ago run` with SSE streaming, `.ago.yaml` project preset | ✅ shipped (v0.1.0–v0.3.x) |
| 3 | `ago jobs list/get/cancel`, `ago logs --follow`, indicatif progress, shell completions | ✅ shipped (v0.1.0–v0.4.x) except `ago logs --follow` (deferred to v0.6.0) |
| 4 | Cross-compile matrix (macOS arm/x64, Linux x64/arm64 musl, Windows), signed releases via cosign + SBOM | ✅ shipped (v0.5.1) — Homebrew tap explicitly not planned (use `cargo install --path cli` or the GitHub Release artifact) |

## `@file` and `@dir` references (v0.3+)

Any input to `ago chat` or `ago run` can include `@<path>` tokens. The CLI
reads the file or directory on **your local machine** before sending the
prompt — so the security boundary stays at the CLI: you choose exactly
what leaves your filesystem.

```text
> explain @src/main.rs to me
> compare @./Cargo.toml and @cli/Cargo.toml
> what files are in @src/        (trailing slash → directory listing)
> review @src/**                  (trailing /** → recursive contents, v0.4.2+)
```

| Syntax | Sends | Use it for |
|---|---|---|
| `@path/to/file.rs` | The single file's content (capped to `max_file_bytes`). | "Explain this function." |
| `@path/to/dir/` | Just the directory listing — names + sizes, no file content. | "What's in this folder?" |
| `@path/to/dir/**` | The content of **every file** inside, depth-first, exclude-filtered, deterministically ordered. | "Review this whole module." |

Each resolved reference is appended to the prompt as a labeled code block
the LLM can quote back. Stderr surfaces a one-line report per ref:

```
· included file @src/main.rs (4321 B)
· skipped @.env — excluded by safety pattern
· included dir @src/ (812 B)
· included dir/** @src/** (14 files, 38112 B — 2 excluded, 1 file(s) truncated)
```

**Defaults (override via `.ago.yaml` `context:` block in v0.3.1):**

| Setting | Default | Rationale |
|---|---|---|
| max bytes per file | 8 KB | Caps a single file at ~2K tokens |
| max bytes total per turn | 50 KB | ~12K tokens total |
| max refs per turn | 16 | Backstop against pathological inputs |
| max files per `@dir/**` | 64 | One recursive ref can fan out to many files; cap stops the walk early |
| exclude patterns | `.env*`, `.git/`, `secrets/**`, `*secret*`, `node_modules/**`, `target/**`, `dist/**`, `.venv/**`, `__pycache__/`, `Cargo.lock`, `package-lock.json`, `yarn.lock`, … | Never leak credentials or heavy artifacts |

**Recursive `@dir/**` safety guarantees (v0.4.2+):**

- **Symlinks are never followed** — protects against loops and against a
  malicious link inside the dir pointing at `~/.ssh/`.
- **Exclude patterns still apply** — `.env` buried inside a subdirectory is
  still skipped, the same way `@/.env` would be.
- **Caps stop the walk early** — when `max_dir_files` or `max_total_bytes`
  hit, the partial result is sent with a `(stopped at …)` trailer so the
  LLM knows it did not see the whole tree.
- **Deterministic order** — entries are sorted by file name within each
  directory before descent, so the same tree produces the same prefix
  bytes across turns. That is what lets prompt caching hit on repeated
  `@src/**` references.

To raise the file cap for a specific project, in `.ago.yaml`:

```yaml
context:
  max_dir_files: 128
  max_total_bytes: 100000   # 100 KB — ~25K tokens
  max_file_bytes: 16384     # 16 KB per file
```

Bare mentions like `@alice` or `alice@example.com` are **not** treated as
references — the CLI requires `/` or `.` in the token AND a real file/dir
to resolve before expansion.

**Token cost example** with `tencent/hy3-preview` ($0.066/M input):
including 3 files totaling ~15K tokens across 10 chat turns ≈ **$0.011**
(~1.1¢) without caching. Provider prompt-caching support lands in v0.3.1.

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

**Return-to plumbing.** When a logged-out user opens the approval URL, the
auth middleware redirects to `/login` and stores the original path in a
short-lived (10-minute, `HttpOnly`, `Secure`, `SameSite=lax`) cookie
`auth_return_to`. After OAuth sign-in the GitHub / Google callbacks consume
that cookie (`_safe_return_to()`) and redirect the user back to the device
page instead of dropping them on the chat home. Only local paths starting
with a single `/` are accepted — `//evil.com/…` and absolute URLs are
rejected to prevent open-redirect through a forged cookie.

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

## `ago chat` — interactive REPL

```text
$ ago chat
ago 0.1.0 — chat mode
connected to https://localhost:5005
agent: backend · model: qwen2.5-coder:7b · provider: ollama · max_steps: 10
conversation: f3a9c2 · type :help for slash commands

> what's 2+2?
4
— 1.34s  35↑/2↓ tokens

> :model qwen2.5:3b
✓ model = qwen2.5:3b

> :reset
✓ new conversation_id = ...

> :quit
```

Inputs prefixed with `:` are slash commands (see table above); anything
else is sent to the active agent with the current conversation_id so the
server's `ConversationManager` restores prior turns. History is persisted
to `${XDG_DATA_HOME:-~/.local/share}/io.agent-orchestrator.ago/chat-history`
so arrow-up works across sessions. The `AGO_INSECURE=1` dev escape hatch
behaves the same as for `ago run`.

## `ago run --local` — embedded Python harness (v0.5.3+)

When no remote orchestrator is reachable (laptop without Docker, CI
sandbox, etc.), `ago run --local "<task>"` spawns
`python3 -m agent_orchestrator.local_cli` as a one-shot subprocess and
runs the agent against the embedded Python harness — no HTTP, no
authentication, no server uptime concerns.

```bash
export ANTHROPIC_API_KEY=sk-...
ago run --local --agent backend --model claude-sonnet-4-6 --provider anthropic \
  "explain the function in @src/main.rs"
```

**Requirements:**

- `python3` must be on `PATH` (override with `AGO_PYTHON=/path/to/python`,
  e.g. when running inside a poetry / conda env).
- `agent_orchestrator` must be importable in that interpreter
  (`pip install agent-orchestrator`).
- The provider's credentials come from the same env vars the dashboard
  uses (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`,
  `GEMINI_API_KEY`, or `OLLAMA_HOST` for the local provider).

**Limitations:**

- `--stream` is ignored (one-shot blocking only — the subprocess writes
  a single JSON object on stdout when done).
- `--resume` is ignored (no shared conversation state across runs;
  v0.6.0 may add a JSON-RPC framing on stdout).
- `ago chat --local` is not implemented in v0.5.3 (would require the
  streaming framing).
- Token usage reports the harness's combined `total_tokens` field with
  zero split between input/output; Provider-level usage splits are a
  v0.5.x follow-up.

## `ago jobs download` (v0.5.2+)

Pull a completed session's artifacts (files written by agent tools) to a
local directory:

```bash
ago jobs download <session_id>                       # → ./.ago-sync/<session_id>/
ago jobs download <session_id> --dir ./my-results    # explicit destination
ago jobs download <session_id> --dir ./out --force   # overwrite existing files
```

Under the hood: `GET /api/jobs/<session_id>/download` returns the session
as a ZIP stream; the CLI extracts it locally with strict path-safety
checks (no `..` traversal, no absolute paths). Without `--force`, the
command refuses to write into a non-empty destination so re-runs do not
silently clobber local edits.

**Limitation:** the session_id must reference a run registered with the
server's `job_logger` — i.e. a run launched **from the dashboard**.
`ago run` invocations use an isolated event bus and write to a tmp
directory the server does not expose. Wiring `ago run` artefacts into
the same flow is a v0.6.0 server-side change.

## `ago self check` / `ago self update` (v0.5.2+)

The CLI can self-upgrade from the GitHub Releases page:

```bash
ago self check     # prints: "ago 0.5.2 → 0.6.0 available — run `ago self update`"
ago self update    # downloads the right archive for this target, extracts, swaps
```

- Target triple is detected at compile time (matches the five-target
  cli-release matrix); a binary built for an unlisted target tells you
  to install manually.
- Archive is downloaded, the `ago` binary inside is extracted to a
  temp file, then atomically swapped in via `rename(2)` (Unix) or a
  rename-aside dance (Windows).
- **Cosign verification is NOT run automatically.** If you need
  supply-chain proof, download the archive + its `.sig` / `.cert`
  manually and run `cosign verify-blob` as documented above. This will
  be hooked into `self update` once a pure-Rust cosign verifier is
  feasible.
- `--force` reinstalls even when already up-to-date (useful after a
  macOS code-sign mishap).

## `AGO.md` — project instructions auto-load (v0.5.0+)

Drop an `AGO.md` (or `.ago.md` fallback) at the root of any project and
the CLI will pick it up walking up from `cwd` — the same algorithm used
for `.ago.yaml`. The file is loaded once at startup and prepended to the
`cache_context` body field on every `ago chat` / `ago run` turn. Because
the OpenRouter `cache_control: ephemeral` marker covers the whole prefix,
re-using the same `AGO.md` across many turns costs ~10% of input tokens
after the first request on Anthropic-routed models.

Use it for the kind of context Claude users keep in `CLAUDE.md`:

```markdown
# Project: payment-gateway

## Style
- Errors are typed (no `panic!` in core/).
- Tests live next to the code (`mod tests` blocks).

## Always
- Run `cargo fmt` after generating Rust.
- Don't suggest adding new dependencies without flagging them.
```

The file is capped at `context.max_file_bytes` (default 8 KB). When the
cap kicks in the CLI prints a hint suggesting you raise
`context.max_file_bytes` in `.ago.yaml`.

## `--resume` (v0.5.0+)

`ago chat --resume` and `ago run --resume "follow-up question"` reuse
the **most recent `conversation_id`** seen on the active server, so the
server's ConversationManager restores prior turns. The id is stored
per-server in `~/.config/ago/state.toml` (mode 0600) — a `https://prod`
session and a `http://localhost:5005` session never collide. First-time
`--resume` with no stored conversation just starts fresh (warns on stderr).

## Code-fence colouring (v0.5.0+)

Assistant output is post-processed to add a vertical bar (`│`) prefix
and a dim-cyan colour to lines inside triple-backtick fences. The
opening fence is rendered as `┌─ <lang>` with the language tag, the
closing fence as `└─`. Pure prose is untouched.

Auto-disabled when:
- stdout is not a TTY (piped to a file, `wc`, etc.),
- `NO_COLOR=1` is set ([no-color.org](https://no-color.org)),
- `--no-color` is passed on the CLI.

Real per-token syntax highlighting (Rust keywords, Python strings, …)
is a v0.5.x follow-up via `syntect` — the surrounding fence machinery
is engine-agnostic so adding it later is a single-function change.

## v0.3+ backlog (deferred / shipped)

Captured here so contributors know what's planned but explicitly out of
scope right now:

| Feature | Status |
|---|---|
| `@file` references in chat prompts | ✅ Done (v0.3.0) |
| `AGO.md` project instructions auto-loaded | ✅ Done (v0.5.0, client-side via `cache_context`) |
| `--resume` to continue last conversation | ✅ Done (v0.5.0) |
| Code-fence colouring in REPL | ✅ Done (v0.5.0); per-token syntect highlighting deferred |
| `ago logs <id> --follow` | Deferred to v0.6.0 — needs server change so CLI runs register in `run_manager` |
| Per-token revocation denylist | Server change; today rotate `JWT_SECRET_KEY` to invalidate |
| Image/file paste attach | Deferred to v0.6.0 — needs multipart upload + storage endpoint |
| Tool approval prompts (`accept-edits` mode) | Deferred to v0.6.0 — needs server pause-and-await-approval |
| Conversation branch / compact / export | Deferred to v0.6.0 — needs persistence endpoints |
| MCP / hook configuration via CLI | Out of scope — already lives in the dashboard |
| Sync-back agent files → CLI cwd | ✅ Done (v0.5.2) for dashboard-launched sessions via `ago jobs download` — `ago run` runs still pending v0.6.0 server change |
| `--local` fallback (subprocess Python harness) | ✅ Done (v0.5.3) for `ago run` — `ago chat --local` requires streaming framing, deferred to v0.6.0 |
| Auto-update channel | ✅ Done (v0.5.2) via `ago self check` / `ago self update` |
| Homebrew tap | Not planned — install via `cargo install --path cli` or download the GitHub Release artifact |

## Limits acknowledged in current revision

- Token revocation requires `JWT_SECRET_KEY` rotation (no per-token
  denylist yet).
- `ago chat` keeps conversation context server-side but files written
  by agent tools land in the session dir on the server, not the CLI's
  cwd. Pull them with `ago jobs download <session_id> --dir <where>`
  (v0.5.2+) for dashboard-initiated sessions. `ago run` runs are not
  yet exposed under `/api/jobs/...` — v0.6.0 server change.
- `ago logs <id> --follow` not implemented — `/api/cli/v1/run` uses an
  isolated EventBus not registered in the server's `run_manager`.
  Deferred to v0.6.0 with a server change.
- `ago run --local` is one-shot only; `ago chat --local` is not
  supported yet (would need a length-prefixed JSON-RPC framing for
  multi-turn). Deferred to v0.6.0.
