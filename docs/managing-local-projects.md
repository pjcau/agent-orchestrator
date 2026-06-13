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

# Optional: project-scoped shell policy for --client-tools runs.
shell:
  allow:                         # pre-approved here → never prompts, never
    - npm                        #   written to the global cache
    - npx
    - node
    - tsc
    - pytest
  deny:                          # HARD block — refused even if confirmed
    - rm                         #   or already in the global cache
    - curl
    - docker
```

Allowed keys: `server`, `agent`, `model`, `provider`, `max_steps`,
`context`, `shell`, `jail`, `jail_image`, `jail_docker`. CLI flags override `.ago.yaml`;
`.ago.yaml` overrides `~/.config/ago/config.toml`. Empty values fall through to
the next layer.

### Shell policy (stop the `allow X? [y/N]` prompts)

By default the first use of each new binary under `--client-tools` asks
`[agent-host] allow `npm`? [y/N]` and, once you say yes, persists it to the
global cache `${XDG_CACHE_HOME:-~/.cache}/ago/shell-allow.json` (permanently
— despite the legacy "for this session" wording). Two ways to pre-empt the
prompts:

- **Global cache** — edit `shell-allow.json` directly: `{"allowed": ["git",
  "npm", "node", …]}`. ⚠️ A *running* `ago` session rewrites this file on its
  next approval, so edit it when no session is live.
- **Project policy (recommended)** — the `.ago.yaml` `shell:` block above. It
  is read fresh every session, is **never persisted**, and is scoped to the
  project. Precedence at the gate:
  1. `deny` — a hard block; wins over the cache, over `allow_all`, and over
     any confirm.
  2. `allow_all` — when `true`, flips the gate to **default-allow**: any
     binary not in `deny` runs with no prompt (see below).
  3. `allow` — pre-approved for this project; runs with no prompt, not saved
     to the global cache.
  4. global cache / interactive confirm (unchanged, fail-closed default).

  All entries match by `argv[0]` basename, so `deny: [rm]` also blocks
  `/usr/bin/rm`. Avoid putting `bash`/`sh`/`zsh` in `allow` — they are full
  shells and bypass the whole gate.

#### "Deny a few, allow everything else"

The default gate is **fail-closed**: an `allow`/`deny` pair still prompts for
anything not listed. To invert it — run anything *except* a blocklist — set
`allow_all: true`:

```yaml
shell:
  allow_all: true                       # run any command…
  deny: [rm, curl, docker, sudo, bash, sh, zsh]   # …except these (hard block)
```

⚠️ **This is an intentional security relaxation.** With `allow_all` the agent
can run arbitrary binaries on your machine without asking — only `deny` stops
it. The file path-sandbox still applies, but shell commands are wide open.
Always pair it with a `deny` list, and **always deny the shells**
(`bash`, `sh`, `zsh`, …): otherwise the agent can run anything via
`bash -c "…"` regardless of the rest of the blocklist. Prefer the explicit
`allow:` list when you can; reach for `allow_all` only in throwaway/sandboxed
checkouts you don't mind the agent operating freely in.

### Jail-by-default: confine the whole session to the project (`jail`)

The shell policy gates `shell_exec` by **binary name**, not by path — so even
with `deny: [rm]`, an *allowed* binary (or one you approved once) can still
write outside the project with an absolute path. `file_read`/`file_write` are
hard-jailed to the project root, but `shell_exec` runs as your user with full
filesystem access. The only way to *guarantee* nothing outside the root is
touched is OS-level isolation.

That is what `jail` gives you. It defaults to **`true`** (jail-by-default):

```yaml
jail: true     # (default) confine --client-tools runs to this folder
# jail: false  # opt out — run --client-tools natively on the host
```

When `jail` is enabled and a `--client-tools` session starts **un-sandboxed**,
the binary prints a one-line warning. The enforcement is provided by the
bundled `ago` front-end wrapper (`cli/ago`): install it on your `PATH` *ahead*
of the compiled binary (kept at `~/.local/libexec/ago`), and any
`ago … --client-tools` is transparently run inside a container — no separate
command to remember:

```bash
ago chat --client-tools --agent team-lead       # auto-jailed when jail: true
```

The wrapper runs the same `ago` binary inside a container that mounts **only**
the current directory as `/work` (plus your `~/.config/ago` config and
`~/.cache/ago` allowlist, read-write). `shell_exec` then physically cannot
reach anything outside the project — an absolute `rm /home/you/…` hits a path
that does not exist in the container. It sets `AGO_IN_JAIL=1` so the binary
knows the session is sandboxed and skips the warning.

**Token bridge.** Your token lives in the OS keychain (macOS Keychain, Linux
Secret Service), which does **not** exist inside the container — a naive jailed
run fails with `token storage error: Platform secure storage failure:
PermissionDenied`. The wrapper avoids this by reading the token on the host
(via the hidden `ago print-token` helper) and forwarding it into the sandbox
through `AGO_TOKEN`, which the binary's env-override storage prefers. The secret
never touches disk; it lives only in the launcher's environment for the run. If
`AGO_TOKEN` is already set in your shell, the wrapper passes it through
unchanged. When you are not logged in, nothing is forwarded and the container
reports `NotAuthenticated` as usual.

**`--log-file` outside the project.** The jail only mounts `/work`, so a log
path elsewhere (e.g. `--log-file ~/ago-session.log`) would otherwise warn `No
such file or directory`. The wrapper detects an out-of-project `--log-file`,
creates the file on the host, and bind-mounts that **single file** (never its
directory — that would re-expose the host tree) at the same absolute path, so
the log lands where you asked. Paths under the project (e.g. `--log-file
/work/session.log` or a relative `session.log`) are already covered by the
`/work` mount.

`jail` resolution (first match wins): `AGO_JAIL` env (`true`/`false`) →
`.ago.yaml` `jail:` → `~/.config/ago/launcher.toml` `jail =` → `true`. Commands
without `--client-tools` always run natively. Inside the jail `allow_all: true`
is reasonable — the container, not the shell policy, is the boundary.

**Jail image (`jail_image`).** The default jail image is **bare `ubuntu:24.04`**,
which ships almost nothing beyond coreutils. When the agent runs a tool that is
not in the image — `git`, `rg`, `python`, `node`, a build toolchain — the call
fails with `shell_spawn_failed`, and inside the jail the error now spells out
the cause: *"'git' is not installed in the jail image; use a richer image via
`jail_image:` in .ago.yaml or the AGO_JAIL_IMAGE env var"*. The agent usually
adapts, but each miss wastes a step, so point the jail at an image that has your
project's toolchain:

```yaml
# .ago.yaml
jail: true
jail_image: ghcr.io/acme/dev-base:latest   # git + rg + python3 + node, etc.
```

Image resolution (first match wins): `AGO_JAIL_IMAGE` env → `.ago.yaml`
`jail_image:` → built-in `ubuntu:24.04`. The image must already be pullable by
your local Docker/OrbStack (the wrapper does not build it).

**Bundled batteries-included image (`ago-jail:latest`).** The repo ships a
ready-made jail image at [`docker/ago-jail/Dockerfile`](../docker/ago-jail/Dockerfile)
so you don't have to assemble a toolchain. Build it once and point `.ago.yaml`
at it:

```bash
docker build -t ago-jail:latest docker/ago-jail   # OrbStack
```

```yaml
# .ago.yaml
jail: true
jail_image: ago-jail:latest
```

It ships the commands an agent reaches for on a real build/test/devops task:
git, ripgrep, jq, curl/wget, openssh-client, **Python 3** (+ pip/venv),
**Node 22** with **pnpm**/**yarn**, the **build toolchain** (make,
build-essential), network/process probes (**`ss`**, **`lsof`**, **`ps`**,
netstat, ping, dig, nc), database clients (**`psql`**/`pg_isready`,
**`sqlite3`**), the **Docker CLI** + **Compose**/**Buildx** plugins, and a
`sudo` shim (the jail runs as an arbitrary uid with no real root, so the shim
just drops the prefix and execs the rest instead of failing to spawn).

**Docker inside the jail (`jail_docker`).** The image ships only the Docker
*client*. By default the launcher does **not** bind-mount the host Docker
socket, so `docker compose config`/`build`-context checks work but `up`/`ps`
fail with *"cannot connect to the Docker daemon"*. To let the agent actually
drive the stack (e.g. a project whose `package.json` is all `docker compose …`),
opt in:

```yaml
# .ago.yaml
jail: true
jail_image: ago-jail:latest
jail_docker: true          # or env: AGO_JAIL_DOCKER=true
```

When on, the launcher (a) bind-mounts the host Docker socket and adds the
container user to the socket's group, and (b) mounts the project at its **real
host path** (instead of `/work`, with the workdir following) — required so
Compose's relative volume paths resolve on the host daemon rather than pointing
at nonexistent `/work/…` dirs. Socket resolution: `AGO_JAIL_DOCKER_SOCK` env →
`DOCKER_HOST` → active `docker context` → `~/.orbstack/run/docker.sock` →
`/var/run/docker.sock`. The launcher prints a one-line warning each run while
this is active.

> **⚠ Security.** `jail_docker: true` hands the host Docker socket to the
> sandbox — that is root-equivalent on the host (it can launch privileged
> containers and mount the host filesystem) and **punctures the jail's file
> isolation**. Enable it per project only when you accept that trade-off. Leave
> it off (the default) and run the live stack from the host whenever you can.

> **Rebuild to refresh.** A running `--client-tools` session is pinned to the
> image its container started from; rebuilding `ago-jail:latest` only takes
> effect on the **next** `ago chat`/`ago run` invocation.

---

## Daily workflow

```bash
cd ~/projects/my-app

# Multi-turn chat. The first time you run a shell command in a new
# binary, the CLI asks for confirmation; subsequent calls hit the
# allowlist cache.
ago chat --client-tools

> :help                                  # slash commands inside the REPL
>                                         # (type ':' then Tab for a dropdown)
> Read README.md and write a one-paragraph summary into NOTES.md
… team-lead delegates to backend/frontend/ai-engineer as needed …
> :cost                                   # tokens + USD spent so far this session
> :quit
```

**Single agent vs. team.** With the default `--agent team-lead` the
server runs the **multi-agent orchestrator**: team-lead decomposes the
task and fans out to specialist sub-agents, and — because this is a
`--client-tools` session — every sub-agent's `file_write` / `shell_exec`
runs in **your** cwd, not the server container. Pass any other agent
(`--agent backend`) to get a single-agent loop instead. See
[agent-host.md § Single-agent vs multi-agent turns](agent-host.md#single-agent-vs-multi-agent-turns).

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

### Live token meter

During a turn the REPL no longer goes silent between steps. Each
orchestrator step prints a dim status line with a **live token meter** —
upstream (prompt) vs downstream (completion) tokens, accumulated cost,
and current throughput — and the turn closes with a summary:

```
> Read README.md and write a summary into NOTES.md
  [1] team-lead: planning            ↑1.2k ↓340 · $0.0021 · 88 tok/s
  ↳ file_read(path=README.md)
  ✓ file_read in 4ms — 1 file
  [2] backend: writing NOTES.md      ↑3.4k ↓910 · $0.0061 · 132 tok/s
  ↳ file_write(path=NOTES.md)
  ✓ file_write in 6ms — 1 file
  ✓ turn ok · 2 steps · ↑3.4k ↓910 · $0.0061
```

The meter is read from the `STEP` / `TURN_END` frames (fields
`input_tokens` / `output_tokens` / `cost_usd`); `tok/s` is computed
client-side. It renders on **stderr**, so piping stdout to a file
(`ago run --client-tools "…" > out.md`) keeps the artefact clean.

In a team run each agent name is printed in its own **stable colour**
(team-lead, backend, frontend, … stay visually distinct for the whole
run), making the interleaved fan-out easy to follow. Colour is disabled
by `--no-color`, by `NO_COLOR=1`, or when stderr is not a TTY.

The assistant's reply is rendered as **Markdown** on stdout: headings and
`**bold**` are bold, `` `code` `` and ``` fenced blocks ``` are highlighted,
and list markers are coloured. To keep the rendering coherent the reply is
buffered and printed at end-of-turn (progress stays live on the Step lines).
When stdout is piped or `--no-color`/`NO_COLOR` is set, the text is emitted
byte-for-byte unchanged, so `ago run --client-tools "…" > out.md` stays clean.

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

- 5 min TTL per delegated tool call — long enough to answer an
  interactive confirmation without the connection dropping. Override with
  `AGENT_HOST_TOOL_TTL_SECONDS` on the dashboard. (Was 60 s, which timed
  out mid-confirmation; see Troubleshooting below.)
- 10 MB per call output cap, 4 concurrent streams per run.
- `--mode prompt` is ignored when `--client-tools` is set — the agent
  loop is always on for client-side delegation to make sense.
- Up to `--max-steps` agent steps per turn (default **30**, server-clamped
  to 100). Sent in the handshake, so `ago chat --client-tools --max-steps 50`
  gives a long multi-step task more room before `Max steps reached`.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `error: not authenticated` | First-time CLI, or token expired | `ago login --device --server https://agents-orchestrator.com` |
| `error: connection failed: ...` | Wrong `server:` in `.ago.yaml`, or dashboard down | `ago whoami` to confirm; check `https://agents-orchestrator.com/health` |
| `agent-host requires the websockets package` | Python harness not installed | `pip install agent-orchestrator` or `AGO_PYTHON=…` |
| `path_outside_workspace` in agent output | Agent tried to write outside cwd | Run `ago` from a higher directory, or set `cwd` argument when spawning |
| `shell_denied` | Non-interactive call to a new binary | Re-run in an interactive shell to confirm, or pre-populate the allow file |
| `peer closed connection` / `Broken pipe` while answering `allow … [y/N]` | You took longer than the tool TTL to confirm; the server timed out the call and the WS dropped | Fixed: the default TTL is now 5 min. Confirm promptly, or raise `AGENT_HOST_TOOL_TTL_SECONDS` on the dashboard |
| `tool_timeout` | The local tool exceeded the TTL (default 5 min) | Split into smaller calls, or raise `AGENT_HOST_TOOL_TTL_SECONDS` |
| Subprocess hangs on Ctrl-C | First Ctrl-C is the REPL's empty-line; second exits | press it twice |
| `✗ turn error` with a reason | The turn failed server-side; the reason is now shown after the `—` (e.g. `Max steps (10) reached`) | Act on the reason; rerun, raise `--max-steps`, or simplify the task |
| Turn looks stuck / agent seems frozen | A long LLM step with no output, or a swallowed error | Run with debug frames (below) and share the output |
| Stuck right after `allow … [y/N]` (your `y` shows as a new prompt) | Fixed in ago ≥ 0.5.9 — the REPL reader and the confirmation prompt used to race for stdin, so `y` was sent as a chat message and the confirmation hung | `ago self update` to 0.5.9+ |

### Debug mode (frame-level trace)

When something looks stuck or the meter/summary is missing, run with
debug logging to see every frame crossing the wire — kinds, token
fields, error reasons, and ordering:

```bash
ago -vv chat --client-tools          # -v info, -vv debug, -vvv trace
# or, equivalently, via env:
AGO_LOG=debug ago chat --client-tools
```

You'll get lines like:

```
DEBUG ago::agent_host::client: send prompt 42B
DEBUG ago::agent_host::client: recv step idx=1 total=0 agent="team-lead" label="thinking" in=0 out=0 cost=0
DEBUG ago::agent_host::client: send tool_result id=tc-1 status=ok
DEBUG ago::agent_host::client: recv turn_end status="error" steps=1 in=510 out=456 cost=0.0002 error="Max steps (10) reached"
```

Logs go to **stderr**, so they don't pollute a piped stdout. Paste this
trace when reporting a problem — it shows exactly what the server sent
and when.

#### Record a whole session to a file

To capture a session for later analysis (or to share), add the global
`--log-file` flag. The file **always** records full debug logs regardless of
`-v`, so your terminal can stay quiet while the file gets everything:

```bash
ago --log-file ago-session.log chat --client-tools
# terminal at the default level; ago-session.log gets the full debug trace
```

The file is **appended** (so several invocations accumulate into one record),
ANSI-free, and includes the WS frame trace, tool calls, errors, and timing —
the same content as `-vvv` but written to disk. Hand off `ago-session.log`
when you want someone to review what happened and suggest improvements.

Under the jail (the default for `--client-tools`), an absolute log path outside
the project — e.g. `--log-file ~/ago-session.log` — is handled transparently:
the launcher bind-mounts that single file into the sandbox so the write lands
on your host. See [Jail-by-default](#jail-by-default-confine-the-whole-session-to-the-project-jail).

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
