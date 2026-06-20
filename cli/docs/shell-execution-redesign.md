# Shell Execution Redesign — Behavior-Based Long-Running Detection

> **Status:** **Phases 1, 2 & a Phase 3 slice IMPLEMENTED** in
> `cli/src/agent_host/runner.rs`. Phase 1 = dual-clock idle/ceiling detection,
> "alive never errors", in-process timeout, markers demoted to a hint. Phase 2 =
> readiness probe (parse a port from the server's output, ANSI-stripped, then a
> local `TcpStream` probe → `ready_port` meta + "verified ready on :PORT" in the
> detail). **Phase 3 slice = persistent working directory + exported env** across
> `shell_exec` calls — a pure `cd`/`export` mutates session state instead of
> spawning, so the agent no longer prefixes `cd apps/01/frontend &&` on every
> command. The full Phase 3 (PTY + PS1 sentinel + `is_input` channel for Ctrl-C /
> interactive input) remains a proposal.
>
> **Phase 4 = session memory & read dedup (cross-cutting, server + CLI):** the
> agent was re-discovering the same files/commands every turn because the
> cross-turn workspace digest was never threaded into the `--client-tools` flow.
> Two fixes: (server) `cli_routes.py` mints one stable `conversation_id` per WS
> connection (= one `ago chat` session) and passes it + the shared digest store
> to `run_agent`/`run_team`, so the digest now persists across turns (was
> `digest="empty"` every turn); (CLI) a per-session `file_read` dedup cache
> returns a compact "unchanged" marker instead of re-sending an unchanged file's
> content (`AGO_READ_CACHE=0` to disable), invalidated by `file_write`. Known
> remaining gaps (not fixed here): within ONE turn the parallel sub-agents still
> don't share in-flight reads (digest updates after the fan-out barrier), and
> there is no AGGREGATE step cap across sub-agents (team-lead 15 + each sub-agent
> 30, no global halt → a turn can reach ~88 steps).
>
> **Phase 4 surface:** server — `cli_routes.py` `_make_agent_host_prompt_handler`
> (per-connection `conversation_id`, `get_digest_store()` passed through); tests
> `test_agent_host_threads_stable_conversation_id_and_digest`,
> `test_agent_host_distinct_connections_get_distinct_conversations`. CLI —
> `read_cache` `Mutex` field + `read_cache_enabled()`; `do_file_read` returns an
> `unchanged` marker on a repeat read with identical (mtime, size); `do_file_write`
> invalidates the entry; tests `read_cache_skips_unchanged_repeat`,
> `read_cache_invalidated_by_write`.
>
> Scope note: Phases 1-3 are CLI-only; Phase 4 also touches the Python server
> (`cli_routes.py`) by explicit user request.
>
> **Phase 3 slice surface:** `cwd`/`env` `Mutex` fields on `LocalToolRunner`;
> `state_change_tokens` + `detect_state_change` recognise a command that is PURELY
> `cd [dir]` / `export NAME=VALUE` (any shell operator disqualifies it);
> `apply_state_change` resolves `cd` against the persistent dir with workspace
> containment and records `export`s; every spawned command runs in the tracked
> cwd with the tracked env. Chained `cd x && cmd` is unchanged; file tools still
> resolve relative to the workspace root. A server-prompt nudge to prefer
> standalone `cd` is a follow-up. Tests: `state_change_detection`,
> `cwd_persists_across_calls`, `export_persists_across_calls`,
> `cd_to_missing_dir_errors`.
>
> **Phase 2 surface:** `extract_listen_ports` (ANSI-stripped, `host:port` /
> `port N`, host-prefixed & unprivileged ordered first), `probe_port`
> (250 ms `TcpStream::connect` to `127.0.0.1:port`), `first_listening_port`;
> wired into the detach block so a detached server gains a `ready_port` meta when
> a port is confirmed open. Needs tokio feature `net`. Tests:
> `listen_ports_are_extracted`, `ready_port_is_probed_and_reported`,
> `no_port_announced_means_no_ready_port`.
>
> **Phase 1 surface:** new `LocalToolRunner` idle fields + `with_shell_idle*`
> builders; reader loop runs an idle clock (reset per output chunk) alongside the
> total ceiling; a still-alive process returns `status:"started"` (idle) or
> `status:"running"` (ceiling), never `shell_timeout`. Config: `AGO_SHELL_IDLE`,
> `AGO_SHELL_IDLE_BUILD`, `AGO_SHELL_CEILING`. Tests:
> `idle_detects_quiet_unknown_command`, `streaming_past_ceiling_returns_running_not_error`,
> `quiet_oneshot_under_idle_exits_normally`, `live_process_at_deadline_is_detached_not_errored`,
> `build_output_is_detected`.
>
> **Author:** investigation from live `~/ago-session.log` sessions (2026-06-16/17
> and 2026-06-20) plus a field survey of OpenHands, Cline/Roo, SWE-agent, Goose,
> Open Interpreter, and Claude Code.

---

## 1. Problem

`shell_exec` cannot reliably handle commands that **never exit on their own**:
dev servers (`pnpm docker:dev`, `docker compose up`, `vite`, `uvicorn`),
watch-mode test runners (`npm test`, `vitest`, `jest`), `tail -f`, interactive
prompts. The agent blocks until a fixed timeout, gets back a misleading
`error reason=shell_timeout`, reads it as "the command is broken", and **retries
in a loop** — burning a 60s timeout per attempt and eventually tripping the
thrash guard.

### Evidence (from real sessions)

| Time | Command | Result | Root cause |
|---|---|---|---|
| 06-17 08:47 | `pnpm docker:dev` | `error shell_timeout` (61s) | server blocks → returned as error → retried 20+× over hours |
| 06-17 08:50 | `timeout 10 pnpm docker:dev` | `shell_spawn_failed: No such file or directory` | **GNU `timeout` is absent on macOS** — the model's own time-box mitigation fails |
| 06-17 08:51 | `timeout 5 docker compose up --build` | `shell_spawn_failed` | same macOS gap |
| 06-20 13:47 | `npm test -- LoginPage.test.tsx` | `error shell_timeout` (60s) | `npm test` defaults to **watch mode**; not in the marker list at all |
| 06-20 13:55 | `npm test -- --testPathPattern=Login` | `error shell_timeout` (60s) | same; model did not learn — burned a 2nd 60s in the same turn |

Two of these (`npm test` watch mode) are **invisible to the current detector**,
which is the whole point: a hardcoded name list cannot keep up.

---

## 2. Current implementation

Detection is **name-based prediction** done *before* the command runs.

- Marker list: `runner.rs:715-744` (`LONG_RUNNING_MARKERS`) — substrings like
  `pnpm dev`, `:dev`, `docker compose up`.
- Detached-form guards: `runner.rs:749-755` (`ALREADY_DETACHED_MARKERS`: ` -d`,
  ` --detach`, `nohup `, `timeout `, …).
- The gate: `is_long_running_command()` `runner.rs:762-774`.
- The effect: a matched command gets an **8s grace** (`LONG_RUNNING_GRACE`,
  `runner.rs:44`) instead of the **60s** `SHELL_DEFAULT_TIMEOUT`
  (`runner.rs:35`); if still alive at grace it is reported `status:"started"` and
  drained in a detached task (`runner.rs:506-597`).

The detached/`started` machinery (`runner.rs:554-597`) is **good and reusable**.
The *trigger* is the problem.

### Why name-matching is structurally wrong

1. **Open-ended input.** Every new tool/script that blocks is a new gap:
   `npm test`, `cargo watch`, `php artisan serve`, `mvn spring-boot:run`,
   `tail -f`, `ngrok`, `prisma studio`, custom `Makefile`/`Procfile` targets.
2. **Trusts a binary that may not exist.** ` timeout ` is treated as
   "already time-boxed" (`runner.rs:754`), but `timeout` is GNU coreutils —
   **absent on macOS and distroless/Alpine** — so the command fails to spawn and
   the grace is skipped anyway.
3. **No serious agent does this.** The field has converged on
   *behavior-based* handling (see §3).

---

## 3. What the field does (verified survey)

The convergent design across the ecosystem:

> **Persistent shell (PTY) + completion detection by sentinel + a *dual* timeout
> (total + idle) + a way to keep interacting (send Ctrl-C / input) instead of
> kill-and-retry.**

| Project | Mechanism | Source |
|---|---|---|
| **OpenHands** | libtmux persistent session; **`NO_CHANGE_TIMEOUT=30s` idle timeout** returns control without killing; PS1 sentinel for exit-code+cwd; `is_input` channel to send `C-c`/keys to the live process | `openhands/runtime/utils/bash.py`; PRs #4881, #6280 |
| **Cline / Roo** | **Hot/cold idle timer**: `PROCESS_HOT_TIMEOUT_NORMAL=2000ms`, `…_COMPILING=15000ms`; `isCompiling()` extends the window when output contains `compiling/building/bundling`; "Proceed While Running" hands control back while still streaming | `BaseTerminalProcess.ts` |
| **SWE-agent / SWE-ReX** | Persistent pexpect PTY; **sentinel** `echo <UUID>$?` for done+exit-code; explicit `is_interactive` mode for non-exiting tools | `swerex/runtime/local.py` |
| **Goose (Rust)** | `tokio::process` + `Stdio::piped()`; **in-process** `tokio::time::timeout` → `start_kill()`; `process_group(0)`; `PR_SET_PDEATHSIG`. Kills on timeout (loses the server) | `crates/goose/.../shell.rs` |
| **Open Interpreter** | One persistent `Popen`; reader threads; **injected echo sentinels** (`##end_of_execution##`) | `interpreter/.../shell.py` (v0.4.2) |
| **Claude Code** | **No auto-detection** — model sets `run_in_background:true` → `bash_id`; `BashOutput(bash_id)` drains only new output; `KillShell`; `/bashes` registry. Foreground timeout 120s default / 600s max, configurable | official docs |

Named patterns worth stealing:

- **Idle timeout** (a.k.a. no-change / quiescence) — distinct from total runtime.
  Canonical reference: Symfony Process `setIdleTimeout()`. "Alive but no output
  for N s" ⇒ settled/waiting; "still emitting" ⇒ working; resets on every byte.
- **In-process timeout** — never shell out to `timeout`/`gtimeout`
  (macOS/distroless). Unanimous.
- **Readiness probe** — turn quiescence into confident "ready" via a TCP/HTTP
  probe or a `listening on PORT` regex (wait4x, dockerize, wait-on's stability
  window).
- **Keep-interacting channel** — after a soft timeout, send `C-c` / input on the
  *same* shell instead of kill+retry (OpenHands `is_input`).

### Rust crates (we are already Rust)

- `portable-pty` (wezterm) — cross-platform PTY incl. Windows ConPTY.
- `pty-process` — async, `AsyncRead`/`AsyncWrite`, wraps `tokio::process`.
- `expectrl` — pexpect-for-Rust: `expect(regex)`, `send_line`.

---

## 4. Proposed design

Replace name-based prediction with **runtime behavior classification**. A live
process is in one of three *observable* states at any moment — independent of the
command name:

| Observed state | Meaning | Action |
|---|---|---|
| **Exited** | one-shot (incl. `CI=1 npm test`) | return the real outcome (exit code + output) |
| **Alive + output idle ≥ Q** | server/watch that has settled and is waiting | `status:"started"` + snapshot, detach + drain, **never `error`** |
| **Alive + output still flowing** | build/test that is working | keep waiting up to the total ceiling, then `status:"running"` + snapshot |

The single new primitive is **output quiescence** = "alive but no new bytes for
`Q` seconds". It generalizes every case:

- **server** (`docker:dev`, `uvicorn`, `vite`): prints a banner → quiet → `started`.
- **watch test** (`npm test`, `vitest`, `jest`): prints results → "Watching for
  changes" → quiet → `started`, **and the snapshot already contains the
  pass/fail** because it printed before settling.
- **slow build** (45s): keeps printing → never quiet → blocks until done or
  ceiling.
- **fast one-shot**: exits → real result.
- **truly hung** (alive, zero output): caught by idle window → detached, not
  killed.

### Invariant that breaks the retry loop

> **A process that is still alive never returns `status:"error"`.**
> `error` is reserved for processes that *die* with a non-zero exit code. A
> blocked-but-alive process returns `started`/`running` + partial output + a
> `detail` telling the model not to re-run it. There is nothing left to "retry".

### Dual clock

Run two timers simultaneously, idle resetting on every chunk:

- **Idle window** `Q` (`AGO_SHELL_IDLE`, default ~6–8s) — extended to ~15s when
  recent output contains build keywords (`compiling|building|bundling|
  installing|downloading`) and not yet a terminal keyword (`compiled|ready|
  done|error|fail`). (Cline/Roo `isCompiling()`.)
- **Total ceiling** `T` (`AGO_SHELL_CEILING`, default = current `shell_timeout`,
  60s) — absolute max wall-time we are willing to *block* the turn. Hitting it
  while alive ⇒ `status:"running"` + snapshot, detach (not error, not kill).

The marker list (`runner.rs:715-744`) is **retired** as the gate. It may survive,
optionally, only as a hint to shorten `Q` for known servers — never as the sole
trigger.

---

## 5. Concrete change to `runner.rs`

The existing reader loop (`runner.rs:498-547`) already streams chunks; adding
idle detection is small and local.

**Add** a `last_output_at: Instant` updated on every stdout/stderr chunk, and
**split** the single `timeout_fut` into two branches inside the `select!`:

```rust
// before the loop
let mut last_output_at = Instant::now();
let total_ceiling = self.shell_timeout;          // T
let idle_window    = self.shell_idle;            // Q (new field)

let total_fut = tokio::time::sleep(total_ceiling);
tokio::pin!(total_fut);

loop {
    // recompute idle deadline each iteration (resets on output)
    let idle_fut = tokio::time::sleep_until(last_output_at + idle_window);
    tokio::pin!(idle_fut);

    tokio::select! {
        biased;
        _ = &mut cancel_fut => { cancelled = true; let _ = child.kill().await; break; }

        // IDLE: alive but quiet for Q  →  settled/server/watch → detach, NOT error
        _ = &mut idle_fut, if !out_buf.is_empty() || started.elapsed() >= MIN_GRACE => {
            detached = true;            // reuse the existing detached path (554-597)
            break;
        }

        // TOTAL CEILING: still working past T  →  running snapshot, NOT error, NOT kill
        _ = &mut total_fut => {
            detached = true;            // same detached path; detail says "running"
            break;
        }

        res = read_chunk(&mut stdout, SHELL_CHUNK_BYTES), if !stdout_done => {
            match res {
                Ok(Some(buf)) => { last_output_at = Instant::now(); /* …existing append… */ }
                Ok(None) | Err(_) => stdout_done = true,
            }
        }
        res = read_chunk(&mut stderr, SHELL_CHUNK_BYTES), if !stderr_done => {
            match res {
                Ok(Some(buf)) => { last_output_at = Instant::now(); /* …existing append… */ }
                Ok(None) | Err(_) => stderr_done = true,
            }
        }
        status = child.wait(), if stdout_done && stderr_done => {
            return finalise_shell(/* …unchanged: real one-shot outcome… */);
        }
    }
}
```

The detached block (`runner.rs:554-597`) stays almost as-is. Two refinements:

1. Drop `long_running`/marker dependency; the `detail` text becomes generic:
   *"still running after Ns with no new output — reported as started and left in
   the background. Do NOT re-run it; verify with a health check (curl / docker
   compose ps) or read its output."*
2. Distinguish the two exit reasons in `detail` (idle ⇒ "started", ceiling ⇒
   "running, still producing output").

**Delete the `timeout 10 …` failure class** by never relying on the `timeout`
binary: the in-process dual clock already time-boxes everything, and ` timeout `
should be removed from `ALREADY_DETACHED_MARKERS` (`runner.rs:754`) so a
user-supplied `timeout N` no longer disables our own clock (and no longer
silently fails on macOS).

---

## 6. New configuration

Today neither the timeout nor the grace is env-configurable. Add:

| Env var | Default | Meaning |
|---|---|---|
| `AGO_SHELL_IDLE` | `7s` | idle/no-output window `Q` before a live process is treated as settled |
| `AGO_SHELL_IDLE_BUILD` | `15s` | extended `Q` while output shows build keywords |
| `AGO_SHELL_CEILING` | `60s` | total wall-time `T` we will block before detaching as "running" |
| `AGO_SHELL_MIN_GRACE` | `2s` | minimum runtime before idle can fire (avoids instant-detach of quick no-output commands) |

Mirror in `.ago.yaml` under a `shell:` block, same precedence order as the
existing `guard:` block (env > yaml > default).

---

## 7. Phasing

1. **Phase 1 (this proposal, smallest):** dual-clock idle detection + "alive never
   errors" + retire marker gate + in-process timeout only. Fixes every case in §1
   with no new dependency. ~1 file, ~40 lines.
2. **Phase 2 (optional):** readiness probe helper — after detach, optionally
   `TcpStream::connect` retry / HTTP `/health` / `listening on PORT` regex, folded
   into `detail` so the model gets "ready on :3000" instead of "started".
3. **Phase 3 (larger):** persistent PTY shell via `portable-pty` + PS1/`echo
   <UUID>$?` sentinel. Gives **persistent cwd + env** (kills the repeated
   `cd apps/01/frontend && …` seen in every logged command) and an `is_input`
   channel to send `C-c`/input to a live process. This is the OpenHands blueprint;
   schedule only if Phase 1/2 prove insufficient.

---

## 8. Test plan

Unit (no real servers needed — use scripted fixtures):

- `exits_fast` → real outcome, exit code preserved.
- `prints_then_idles` (`echo hi; sleep 60`) → detaches as `started` within `Q`,
  snapshot contains `hi`, status never `error`.
- `streams_past_ceiling` (`while true; do echo .; sleep 0.2; done`) → blocks to
  `T`, returns `running`, status never `error`.
- `build_keywords_extend_idle` → does not detach at `Q` while emitting
  `building…`.
- `hung_no_output` (`sleep 60`) → detaches at `Q` (alive, not killed/errored).
- `nonzero_exit` (`exit 3`) → `status:"error"` with code 3 (the *only* error path).
- `no_timeout_binary` — assert we never spawn `timeout`/`gtimeout`.

Carry over the existing `long_running_detects_*` / `…_ignores_*` tests
(`runner.rs:1193-1227`) as behavior tests against the new classifier where still
meaningful.

---

## 9. References

- OpenHands `bash.py` (libtmux, `NO_CHANGE_TIMEOUT`, `is_input`), PRs #4881 / #6280.
- Cline/Roo `BaseTerminalProcess.ts` (hot/cold, `isCompiling`).
- SWE-ReX `local.py` (PS1 sentinel).
- Goose `shell.rs` (in-process `tokio::time::timeout`, `process_group`).
- Claude Code Bash tool / `BashOutput` / `KillShell` / `/bashes` (official docs).
- Symfony Process `setIdleTimeout()` (idle-timeout reference).
- macOS `timeout` gotcha: GNU coreutils installs it as `gtimeout`; absent by default.
- Rust crates: `portable-pty`, `pty-process`, `expectrl`.
- Terminal-Bench 2.0 / Terminus 2 (arXiv:2601.11868); "Building Effective AI
  Coding Agents for the Terminal" (arXiv:2603.05344).
</content>
</invoke>
