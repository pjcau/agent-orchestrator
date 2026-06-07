# Agent Host — client-side tool delegation

The agent-host channel lets a chat session running on the remote
dashboard (`agents-orchestrator.com`) execute tools on the user's local
machine. The agent loop, the LLM call, and conversation state stay on
the server; `file_read`, `file_write`, and `shell_exec` run as the user,
in the user's working directory.

Use case: `ago chat --client-tools` reaches feature parity with
`ago run --local` for filesystem and shell, while keeping multi-turn
conversation context server-side.

## Architecture at a glance

```
┌──────────────────────────┐                ┌──────────────────────────┐
│ Rust ago binary          │                │ Dashboard                │
│                          │                │ (agents-orchestrator.com)│
│ ago chat --client-tools  │                │                          │
│                          │                │  /api/cli/v1/agent-host  │
│  └─ spawns subprocess: ──┼── WebSocket ───┤  ┌────────────────────┐  │
│     python -m            │                │  │ RemoteSkillAdapter │  │
│     agent_orchestrator.  │                │  │  ↑                 │  │
│     agent_host           │                │  │ PendingToolCalls   │  │
│       │                  │                │  │  Registry          │  │
│       ├─ LocalToolRunner │                │  │  ↑                 │  │
│       │   ├ file_read    │                │  │ run_agent loop     │  │
│       │   ├ file_write   │                │  │ (LLM + steps)      │  │
│       │   └ shell_exec   │                │  └────────────────────┘  │
│       └─ Path/Shell      │                │                          │
│          sandbox         │                │                          │
└──────────────────────────┘                └──────────────────────────┘
```

The single WebSocket carries every frame in both directions. The
catalogue (`hello`, `ack`, `prompt`, `tool_call`, `tool_result`,
`tool_chunk`, `cancel`, `assistant_text`, `turn_end`, `error`) is
documented in
[`src/agent_orchestrator/agent_host/protocol.py`](../src/agent_orchestrator/agent_host/protocol.py).

## Lifecycle of one chat turn

1. `ago chat --client-tools` (Rust binary) spawns
   `python -m agent_orchestrator.agent_host` with the workspace,
   server URL, and JWT.
2. The subprocess opens a WS, sends `HELLO` (version, cwd, manifest of
   local tools).
3. Server checks the JWT *before* `ws.accept()`, replies `ACK` with the
   server-assigned `run_id` and capabilities.
4. User types a prompt → `PROMPT` frame.
5. Server runs the agent loop. Every time the agent decides to call a
   tool listed in the client manifest, the server's
   `RemoteSkillAdapter` proxies through `PendingToolCallsRegistry`:
   - Mints `tool_call_id` + `nonce`.
   - HMAC-SHA-256 of `(run_id, tool_call_id, nonce, name)` using
     `JWT_SECRET_KEY` becomes the `signature`.
   - Sends `TOOL_CALL`, awaits the matching `TOOL_RESULT` (TTL = 5 min
     default, see [Tuning the tool TTL](#tuning-the-tool-ttl)).
6. Client verifies the HMAC, executes the tool, streams stdout in
   `TOOL_CHUNK` frames (for `shell_exec`), and finalises with a
   `TOOL_RESULT` signed with the same nonce.
7. Agent loop continues. Every orchestrator step also emits a `STEP`
   frame carrying the **cumulative token meter** for the turn
   (`input_tokens` upstream / `output_tokens` downstream / `cost_usd`),
   so the client can render a live `↑12.3k ↓4.5k · $0.0123 · 78 tok/s`
   status line instead of going silent. Final reply is streamed to the
   client as `ASSISTANT_TEXT` chunks, terminated by `TURN_END` — which
   carries the turn totals (`step_count`, `input_tokens`,
   `output_tokens`, `cost_usd`) for a closing summary.

   The token fields are additive: a v1 client that ignores them keeps
   working (`Frame.from_dict` drops unknown fields and defaults missing
   ones to 0). The server fills them from the `TOKEN_UPDATE` events that
   `run_agent` emits after each step — see
   [`dashboard/cli_routes.py`](../src/agent_orchestrator/dashboard/cli_routes.py)
   `forward_steps`.

## Security model

| Threat | Guard | Test |
|---|---|---|
| Cross-WS tool_result injection | HMAC binds result to `(run_id, tool_call_id, nonce, name)`; verified before resolve | [`test_signature_tamper_dropped`](../tests/test_agent_host_server.py) |
| Replay of an old tool_result | `tool_call_id` single-use server-side | `test_unknown_id_dropped` |
| Nonce reuse | New 16-byte CSPRNG nonce per call; rejected if echoed value mismatches | `test_nonce_mismatch_dropped` |
| Forged frame on the wire | TLS at transport; signature for tamper evidence + non-repudiation | (TLS is operator's responsibility) |
| Protocol drift | `version: int = 1` in HELLO; server rejects mismatch loudly | `test_version_mismatch` |
| Path traversal (`..`, absolute, symlink) | Strict `enforce_workspace` on every `file_*` / `shell_exec` cwd | `TestEnforceWorkspace::*` |
| Command injection via shell | `shell_exec` refuses `argv` as a string; only argv lists accepted | `test_shell_argv_string_refused` |
| argv[0] aliasing | Allowlist keyed by basename; path separators in `argv[0]` rejected | `test_path_in_argv0_rejected` |
| Unconfirmed first-use of new binary | Fail-closed: no confirm available → refused, not allowed | `test_shell_non_interactive_refuses_unknown` |
| Resource exhaustion via runaway output | 10 MB per-call cap, 4 concurrent streams per run | `test_chunk_too_large_rejected` |
| Out-of-order or duplicate chunks | Strict monotonic `seq` from 0 | `test_chunk_out_of_order_dropped` |
| Zombie processes after CANCEL | SIGKILL + bounded drain reap | `test_server_cancel_kills_shell` |
| Auth bypass on WS | JWT validated *before* `ws.accept()`, same pattern as `/ws/stream` | covered by import-boundary + middleware tests |
| Open WS DoS | Auth required; rejected sessions never count against quota | n/a |

### Secret management

The HMAC signing key is `JWT_SECRET_KEY`, the same secret used by the
session-cookie code in `dashboard.auth`. Rotating it invalidates *every*
outstanding agent-host signature, the same property that already applies
to session cookies. **Do not introduce a separate secret** for the
agent-host channel — single-source-of-truth here is intentional.

### Sandbox boundaries

* **Filesystem.** `enforce_workspace` lives in
  [`agent_host/path_sandbox.py`](../src/agent_orchestrator/agent_host/path_sandbox.py).
  It is strict on purpose: rejects escapes rather than silently
  remapping them (the existing `skills.filesystem._confine` is the
  permissive variant used for trusted local agents). Default rejects
  any symlink on the path; `follow_symlinks=True` is an explicit
  opt-in.
* **Shell.** `shell_exec` runs via `asyncio.create_subprocess_exec`
  (argv list, never `shell=True`). The allowlist
  ([`shell_allowlist.py`](../src/agent_orchestrator/agent_host/shell_allowlist.py))
  is keyed by `argv[0]` basename and persisted to
  `${XDG_CACHE_HOME:-~/.cache}/ago/shell-allow.json`. First-use of a
  new binary prompts the user; later sessions hit the cache.
  General-purpose shells (`bash`, `sh`, `zsh`, `dash`, …) are flagged
  high-risk so the prompt carries an explicit warning.

## Telemetry

Metrics live in
[`agent_host/telemetry.py`](../src/agent_orchestrator/agent_host/telemetry.py)
and emit on the injected `MetricsRegistry` (the dashboard wires it
through `app.state.metrics_registry`). Labels are intentionally small
to keep Prometheus cardinality bounded; user input is never used as a
label.

| Metric | Type | Labels |
|---|---|---|
| `agent_host_tool_call_latency_seconds` | histogram | `tool, status` |
| `agent_host_active_streams` | gauge | `run_id_hash` (16-hex SHA-256) |
| `agent_host_disconnect_total` | counter | `reason` (stable string from `serve_agent_host`) |
| `agent_host_chunk_rejected_total` | counter | `reason` |

The `run_id_hash` keeps the label opaque so dashboards cannot correlate
a single user session by glancing at the metrics page. The underlying
`run_id` continues to appear in the structured audit logs.

## Operating

### Enabling the endpoint

It's on by default if the package is installed and the dashboard imports
`dashboard.cli_routes`. The route handler returns 401 to unauthenticated
WS handshakes — no env var to flip.

### Disabling it

Remove the `agent_host_endpoint` route registration in
`cli_routes.py` (or guard it behind an env flag in a downstream
override).  The protocol module remains harmless on its own.

### Tuning the tool TTL

When the server proxies a `TOOL_CALL`, the clock until the matching
`TOOL_RESULT` starts immediately — and that window **includes any
interactive confirmation the client shows the user** (e.g.
``allow `ls`? [y/N]``). The original 60 s TTL was shorter than a human
typically takes to read and answer such a prompt, so the call timed out
mid-confirmation and the connection was torn down with a `Broken pipe` /
`peer closed connection without sending TLS close_notify` error on the
client.

The default is now **300 s (5 min)** and overridable:

```bash
# Give users longer to answer confirmation prompts (seconds)
export AGENT_HOST_TOOL_TTL_SECONDS=600
```

Invalid or non-positive values fall back to the default (logged at
WARNING). See `_tool_ttl_from_env` in
[`agent_host/server.py`](../src/agent_orchestrator/agent_host/server.py)
and `TestToolTTLConfig` in
[`tests/test_agent_host_server.py`](../tests/test_agent_host_server.py).

> **Native client note.** The Rust `ago` binary should also avoid
> blocking the WS read loop while it waits on stdin for a confirmation,
> and may render the new `STEP`/`TURN_END` token fields. Those are
> client-side changes that live in the `ago` repo; the server fix above
> already prevents the timeout regardless of client.

### Debugging a stuck session

* Check `agent_host_disconnect_total{reason="<...>"}` — the stable
  reason string says exactly why a session ended.
* `agent_host_chunk_rejected_total{reason="signature_invalid"}`
  spiking signals either a key rotation in progress or a misbehaving
  client. Audit `JWT_SECRET_KEY` rotation timestamps first.
* The structured log line `agent-host: session ended reason=...
  identity=...` ties the session to the authenticated identity.

### Roll-back plan

The five commits are independent enough to revert in isolation:

1. `feat/agent-host-protocol` commit #5 (this doc + telemetry) — purely
   additive, safe to revert.
2. Commit #4 (streaming + cancellation) — the client falls back to
   buffered mode; the server registry still accepts `TOOL_RESULT` but
   no longer recognises `TOOL_CHUNK`.
3. Commit #3 (Python client + sandbox + allowlist) — removes the
   subprocess entrypoint.
4. Commit #2 (WS endpoint + adapter) — removes the route. After this
   revert the agent-host can no longer accept connections.
5. Commit #1 (protocol + signing) — removes the package.

Reverting in this order (5 → 1) keeps the codebase in a consistent
state at every step.
