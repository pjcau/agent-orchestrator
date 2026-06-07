# Managing local projects with the remote orchestrator

`agents-orchestrator.com` (or any self-hosted dashboard) runs the agent
loop, the LLM calls, the conversation memory, the routing, the budget
guards, and the team-orchestration logic. None of those things touch
your filesystem — they live inside the server container.

The **agent-host channel** (CLI flag `--client-tools`) inverts that for
file and shell tools: every time the remote agent decides to call
`file_read`, `file_write`, or `shell_exec`, the call is delegated back
to your machine over a single signed WebSocket and the tool runs in the
working directory of your `ago` process. The agent never sees a file
that isn't in your repo; you never have to upload your project to a
remote sandbox.

This guide is the end-to-end recipe. The protocol details, the threat
model, and the operator runbook live in
[**agent-host.md**](agent-host.md); the broader CLI surface lives in
[**cli.md**](cli.md).

---

## When to use which execution mode

| Goal | Command | Where the agent loop runs | Where tools run |
|---|---|---|---|
| One-shot answer, no file changes | `ago chat --mode prompt` | server | nowhere (no tools) |
| Multi-turn chat, server-side workspace OK | `ago chat` | server | server container |
| Multi-turn chat, files in **your** repo | **`ago chat --client-tools`** | server | **your cwd** |
| One-shot agent task touching your repo, no server needed | `ago run --local` | local Python | local |
| One-shot agent task on the server but writing your files | `ago run --client-tools "…"` | server | **your cwd** |

`--client-tools` is the right answer whenever you want the team-lead,
orchestrator-side routing, multi-turn memory, and budget guards —
*and* you want the resulting `main.py` to end up in your project
folder, not in a server tarball you have to download.

---

## Prerequisites (one-time, ≈ 2 minutes)

1. **The CLI in PATH.** If you have `~/installAgo.sh` from earlier:

   ```bash
   AGO_VERSION=ago-v0.5.4 ~/installAgo.sh --version
   mkdir -p ~/.local/bin
   ln -sf ~/.cache/ago/ago-v0.5.4/ago ~/.local/bin/ago
   hash -r && ago --version       # → ago 0.5.4
   ```

2. **The Python harness.** The Rust binary spawns
   `python -m agent_orchestrator.agent_host` as a subprocess; install
   the library system-wide or in a virtualenv reachable on `PATH`:

   ```bash
   pip install agent-orchestrator        # global
   # OR reuse the project venv:
   source /path/to/agent-orchestrator/.venv/bin/activate
   ```

   Override which interpreter the binary spawns with `AGO_PYTHON=/path/to/python`.

3. **Login.** The OAuth device flow opens a browser; no API key to
   copy-paste:

   ```bash
   ago login --device --server https://agents-orchestrator.com
   ago whoami                            # confirms identity
   ```

4. **OpenRouter (or other LLM provider) configured server-side.**
   The dashboard owner sets `OPENROUTER_API_KEY` (or
   `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …) once in its environment.
   You do **not** need to export it on your laptop — the LLM call
   happens on the server.

---

## Per-folder defaults: `.ago.yaml`

Drop a `.ago.yaml` at the root of each project so `ago` picks up the
same defaults every time you `cd` into it:

```yaml
# .ago.yaml — checked in OR git-ignored, whichever you prefer
server:   https://agents-orchestrator.com
agent:    team-lead              # multi-agent coordinator
provider: openrouter
model:    tencent/hy3-preview    # cheap + fast
max_steps: 15                    # team-lead delegates; give it room
```

Allowed keys: `server`, `agent`, `model`, `provider`, `max_steps`,
`context`. CLI flags override `.ago.yaml`; `.ago.yaml` overrides
`~/.config/ago/config.toml`. Empty values fall through to the next layer.

---

## Daily workflow

```bash
cd ~/projects/my-app

# Multi-turn chat. The first time you run a shell command in a new
# binary, the CLI asks for confirmation; subsequent calls hit the
# allowlist cache.
ago chat --client-tools

> :help                                  # slash commands inside the REPL
> Read README.md and write a one-paragraph summary into NOTES.md
… team-lead delegates to backend/frontend/ai-engineer as needed …
> :quit
```

```bash
# One-shot run for a scriptable task.
ago run --client-tools "Generate a FastAPI scaffold under api/ with a /health endpoint and a pytest test."

# Resume the previous server-side conversation
ago chat --client-tools --resume

# Override defaults on the fly
ago chat --client-tools --agent backend --model qwen/qwen3.6-plus
```

The CLI prints when it spawns the subprocess and when the WebSocket
handshake succeeds:

```
· spawning python3 -m agent_orchestrator.agent_host (client-tools)
[agent-host] connected run_id=a1b2c3d4 agent=team-lead model=tencent/hy3-preview
> _
```

---

## Security defaults (do not weaken without thought)

The CLI side enforces three guards before any tool runs:

1. **Path sandbox.** Every `file_read` / `file_write` resolves against
   the workspace root (`cwd` of the `ago` process). Paths that escape
   via `..`, absolute paths outside the workspace, or symlinks-out-of-
   workspace are rejected with `path_outside_workspace`. Set
   `follow_symlinks=True` only when you genuinely need to follow a
   pinned generated dir.
2. **Shell allowlist.** `shell_exec` accepts only an `argv` **list**
   (string commands are refused outright). First use of a new binary
   triggers an interactive `[agent-host] allow 'pytest' for this
   session? [y/N]` prompt. Decisions are persisted to
   `${XDG_CACHE_HOME:-~/.cache}/ago/shell-allow.json` (JSON, atomic
   write, basename-keyed). Inspect or roll back manually any time.
   General-purpose shells (`bash`, `sh`, `zsh`, `dash`) are flagged
   high-risk so the prompt warns you.
3. **HMAC signatures.** Every `tool_call` on the WebSocket is signed
   with the dashboard's `JWT_SECRET_KEY` over
   `(run_id, tool_call_id, nonce, name)`. Replays, cross-session
   injections, and tampered chunks are dropped silently server-side
   (no DoS surface). Full threat-and-mitigation matrix:
   [agent-host.md § Security model](agent-host.md#security-model).

Resource bounds the server enforces:

- 60 s TTL per delegated tool call (configurable in the registry).
- 10 MB per call output cap, 4 concurrent streams per run.
- `--mode prompt` is ignored when `--client-tools` is set — the agent
  loop is always on for client-side delegation to make sense.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `error: not authenticated` | First-time CLI, or token expired | `ago login --device --server https://agents-orchestrator.com` |
| `error: connection failed: ...` | Wrong `server:` in `.ago.yaml`, or dashboard down | `ago whoami` to confirm; check `https://agents-orchestrator.com/health` |
| `agent-host requires the websockets package` | Python harness not installed | `pip install agent-orchestrator` or `AGO_PYTHON=…` |
| `path_outside_workspace` in agent output | Agent tried to write outside cwd | Run `ago` from a higher directory, or set `cwd` argument when spawning |
| `shell_denied` | Non-interactive call to a new binary | Re-run in an interactive shell to confirm, or pre-populate the allow file |
| `tool_timeout` | The local tool exceeded 60 s | Split into smaller calls, or raise the registry TTL in the dashboard config |
| Subprocess hangs on Ctrl-C | First Ctrl-C is the REPL's empty-line; second exits | press it twice |

Per-feature deep dives:

- Wire protocol catalogue: [agent-host.md § Architecture at a glance](agent-host.md#architecture-at-a-glance)
- Lifecycle of one chat turn: [agent-host.md § Lifecycle of one chat turn](agent-host.md#lifecycle-of-one-chat-turn)
- Operator runbook (telemetry, rollback): [agent-host.md § Operating](agent-host.md#operating)
- Plain `ago chat` / `ago run` (no client-tools): [cli.md](cli.md)
- Project-level config (`.ago.yaml`): [cli.md § Per-project preset](cli.md#per-project-preset-agoyaml)

---

## How this fits the orchestrator architecture

The agent-host channel is the bridge that lets the multi-agent
orchestrator (team-lead coordinating 25+ specialist agents on the
server) act on your actual files without ever holding a copy. Concretely:

- **The orchestrator still picks the right agent.** `team-lead`
  decomposes your task, calls `backend`/`frontend`/`ai-engineer`/etc.
  as sub-tools, and merges their results — all on the server.
- **Each specialist's tool calls run locally.** When `backend` decides
  to write `api/main.py`, the call is delegated through `ago` and the
  file lands in your repo, not in a server-side temp dir.
- **Conversation memory stays on the server.** `--resume` works the
  same way it does without `--client-tools`; the `conversation_id` is
  attached to your identity and persisted in the dashboard's store.
- **Budgets and guardrails still apply.** Server-side `CostGuard`,
  `PIIScanner`, `SecretsScanner`, `PromptInjectionDetector`, and the
  output-schema guard run before *any* tool call (local or remote),
  so a delegated call cannot bypass them.

That makes `ago chat --client-tools` the recommended entry point for
day-to-day project work against a hosted dashboard. Pure
`ago run --local` remains useful when you want to bypass the server
entirely (no network, no auth, offline-friendly), and plain
`ago chat` is the right call when the agent works on an artefact you
do not need locally (server-side data analysis, evaluation report,
etc.).
