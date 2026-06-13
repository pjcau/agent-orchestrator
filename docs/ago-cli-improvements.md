# ago CLI — improvement proposals from a live session analysis

**Date:** 2026-06-08
**Source:** live `--log-file` trace of a real `ago chat --client-tools --agent team-lead`
session on an external project (multiple turns over ~90 min observed).
**Method:** the new `ago --log-file` flag (v0.5.14) recorded the full debug
frame trace; this document consolidates what the trace revealed.

---

> **Implementation status (2026-06-08):** all findings actioned —
> P1 error logging ✅, P1 output cap ✅, P0 compaction ✅, P2 back-off ✅,
> P2 team turn_end ✅, P3 step meter ✅ (richer labels ◑ partial). Each item's
> section below links to its code + docs. The denied-`rm` root cause is now
> both diagnosable (P1) and self-limiting (P2).

> **Live-trace verification (2026-06-09):** re-read `~/ago-session.log`
> (409 tool-calls, 73 errors, 5 turns, 19:23–22:06 on 06-08). The trace
> **straddles the upgrade** — the dashboard was restarted at **21:53**, so
> the P3 meter flips `total=0 → total=40` mid-file — but it is otherwise a
> *pre-improvement* trace and confirms the original diagnosis was real, not
> hypothetical:
>
> | Item | Observed in trace | Reading |
> |---|---|---|
> | P0 compaction | backend context reached **872,379 input tokens** (threshold 60k) | did **not** fire — old server code, confirms the cost driver |
> | P1 error reason | 73× `status=error` with **no** `reason=` | CLI binary predates the 23:14 P1 commit |
> | P1 output cap | single-step `in=` jumps of +60–470k | uncapped — old server code |
> | P2 turn_end | all 5 `turn_end` → `steps=0 in=0 out=0` | split-token mapping not yet live |
> | P3 meter | `total=40` only after 21:53 restart | new server code engaged mid-session |
>
> **Error breakdown:** 65 `shell_exec` + 7 `file_write` = 73 / 409 (**18 %**).
> Consistent with `shell_requires_argv_list` (agent emits shell *strings*),
> but unconfirmable line-by-line until the CLI is rebuilt past the P1 commit.
>
> **Action to get a clean post-fix trace:** rebuild the CLI
> (`cd cli && cargo install --path . --locked`) so `reason=` is logged, restart
> the dashboard (`docker compose up -d --build dashboard`) so P0/P1-cap/P2 run,
> then record a fresh session to `~/ago-session-new.log` and compare the same
> four signals (peak `in=`, `reason=` on errors, `turn_end` tokens, `total`).

> **Clean post-fix verification (2026-06-09, CLI v0.5.18 + jail-by-default):**
> recorded a fresh `--client-tools` team run (`team-lead` → `code-reviewer` +
> `devops`) to `~/ago-session.log` with the **v0.5.18 jailed** launcher. This is
> the clean post-fix trace the block above asked for. Four signals confirmed:
>
> | Signal | Pre-fix (06-08) | Post-fix (06-09, v0.5.18) | Reading |
> |---|---|---|---|
> | `reason=` on errors | 73× error, **none** had `reason=` | **0/N** errors lack `reason=` | **P1 fixed** — every error now diagnosable |
> | P3 meter `total=` | `total=0` until 21:53 restart | `total=40` **from step 1** | **P3 live** |
> | P0 compaction | never fired (peaked 872k `in=`) | **fires** (idx 23: 62k→41k; idx 35: 118k→102k) | **P0 live** but see ⚠️ below |
> | Jail token + log | n/a | **0** `PermissionDenied` / secure-storage / log-file errors | **v0.5.18 jail fix verified live** |
>
> **✅ The v0.5.18 jail fix works end-to-end.** The token bridge (host keychain →
> `AGO_TOKEN` → sandbox) and the out-of-project `--log-file ~/ago-session.log`
> bind-mount both function: the session connected and logged with **zero**
> `Platform secure storage failure: PermissionDenied` and **zero**
> `No such file or directory` on the log path — the two errors that originally
> blocked the run.
>
> **Two new findings from the post-fix trace — both actioned (2026-06-09):**
>
> ✅ **P0 compaction now bounds the peak dynamically.** It used to trim yet let
> per-step `in=` climb to **140,476 tokens** — 2.3× the 60k threshold — because
> the kept tail was a fixed message count that could itself exceed the
> threshold. Fixed: compaction now sizes the retained tail to a **fraction of
> the threshold** (`compaction_target_ratio`, default 0.6) and triggers on a
> **pre-call estimate** as well as the last billed turn, so a single ballooning
> turn is caught before it is sent. Lower threshold ⇒ fewer tokens retained.
> See [P0 follow-up — compaction did not bound the peak](#p0-follow-up--compaction-did-not-bound-the-peak).
> *(Server-side: takes effect once the orchestrator is redeployed with this
> `core/agent.py`.)*
>
> ✅ **`shell_spawn_failed: No such file or directory (os error 2)`** — was 2× in
> the jailed `devops` turn, a binary absent from the **bare `ubuntu:24.04` jail
> image**. Fixed client-side (ago v0.5.18): the in-jail error now names the cause
> and the fix, and `.ago.yaml` gained `jail_image:` to run the sandbox in an
> image that has the project toolchain. See
> [P2 — Jail base image lacks common tooling](#p2--jail-base-image-lacks-common-tooling).

> **Live verification + delivery (2026-06-09, ago v0.5.18, prod redeploy):**
> The remote was redeployed with the dynamic-compaction core (deploy run
> succeeded; a non-jailed `ago run` round-tripped `OK` in 3.9s — prod loop
> healthy). The spawn-hint was then confirmed in a **real jailed session**: a
> piloted `ago chat --client-tools` ran `cowsay ciao` and the tool result was
>
> ```
> reason="shell_spawn_failed: No such file or directory (os error 2) — 'cowsay'
>  is not installed in the jail image; use a richer image via `jail_image:` in
>  .ago.yaml or the AGO_JAIL_IMAGE env var"
> ```
>
> The controlled run made exactly ONE attempt, got the hint, and stopped at
> step 2 — correct. ago v0.5.18 was tagged for release on the strength of this.
>
> **Follow-ups:**
>
> ✅ **Agent burns all `max_steps` on an unrecoverable jail spawn-fail — FIXED.**
> A separate interactive session hit the full 40 steps because the agent kept
> trying to *obtain* the missing binary (`apt install …`, alternatives) — which
> the bare, network/sudo-less jail can never satisfy. The P2 back-off only
> short-circuits *identical* repeated calls, so a varying sequence looped to the
> ceiling. Fixed with a **consecutive-failure circuit breaker**
> (`max_consecutive_tool_failures`, resets on any success) that stops the run
> with a `jail_image`-actionable message. See
> [P3 — agent loops on unrecoverable jail spawn-fail](#p3--agent-loops-on-unrecoverable-jail-spawn-fail).
>
> 🔲 **`ago run --client-tools` one-shot hangs after handshake.** The jailed
> *non-interactive* `run` path connects (handshake OK, token bridged) but never
> sends the prompt / advances — the container sits idle. `chat --client-tools`
> (jailed) is unaffected and works. Likely an EOF/stdin or one-shot
> agent-host-lifecycle issue specific to `run`, **not** the v0.5.18 jail
> mechanics. See [P3 — `ago run --client-tools` one-shot hangs](#p3--ago-run---client-tools-one-shot-hangs).
>
> ✅ **`turn_failed: 'NoneType' object is not subscriptable` — FIXED.** Surfaced
> live during a testAgo run on `tencent/hy3-preview`: the agent had read a few
> files (context ~15.5k, well below the compaction threshold — so unrelated to
> the new compaction/breaker) and the turn crashed. Root cause: `OpenAIProvider.
> complete()` did a bare `response.choices[0]`, and the preview model returned a
> 200 with `choices=None` (refusal / moderation / upstream error). Fixed by
> guarding `response.choices` and raising a clear, catchable error so the
> OpenRouter fallback chain tries another model instead of crashing the turn.
> Tests: `tests/test_openai_provider.py`.

> **⚠️→✅ Root-cause: TWO agent loops had drifted (2026-06-09).** A clean testAgo
> retry exposed that the compaction + breaker work had no effect in production:
> per-step context peaked at **251k** (threshold 60k) and the minimal-change
> rule was ignored. Reason: the dashboard / agent-host actually run
> `dashboard/agent_runner.py::_instrumented_execute` — a second copy of the
> agent loop (for live EventBus events) — **not** `core.Agent.execute`, where
> compaction, the circuit breaker, and the P1 tool-result cap had been added.
> The earlier "compaction firing" readings were misattributed: the `in=` dips
> were sub-agent **handoffs** (fresh context), not compaction.
>
> **Fixed** by porting all three into `_instrumented_execute`, **reusing the
> core helpers** (`compact_messages`, `estimate_message_tokens`,
> `cap_tool_result_content`, `_UNRECOVERABLE_ENV_ERRORS`,
> `recover_dangling_tool_calls`) so the two loops can't silently diverge on this
> behaviour again, plus a `_MINIMAL_CHANGES_STEER` appended to every agent's
> system prompt (anti-sprawl, applies to all projects). Tests now run against
> the REAL loop: `test_instrumented_compaction_bounds_sent_context`,
> `test_instrumented_breaker_stops_varying_failure_grind`,
> `test_instrumented_system_prompt_carries_minimal_changes_steer`.
>
> 🔲 **Follow-up — per-project `AGO.md` is dropped under `--client-tools`.** The
> agent-host `Prompt` frame carries only `text` (no `cache_context` field), and
> the client-tools path never folds `AGO.md` into the prompt — so a project's
> `AGO.md` never reaches the server (the `_MINIMAL_CHANGES_STEER` covers the
> sprawl case regardless). Fixing the general feature needs a CLI change
> (fold instructions into the agent-host prompt) + release.

## TL;DR

The multi-agent orchestration **works and completes tasks** — including
3 sub-agents running in parallel (frontend + backend + code-reviewer) with
all file/shell tools delegated to the local machine. But each turn is **slow
and expensive**, and the trace shows exactly why: **unbounded per-agent
context growth** and **uncapped tool outputs**, compounded by **opaque,
repeatedly-failing tool calls**.

Across the full ~90-minute observation the team-lead routed to a healthy
spread of specialists — **backend, frontend, code-reviewer, architect** —
in single- and parallel-agent turns. Several turns completed `status="ok"`:

| Turn | Agents | Steps | Wall time | Result | Cost |
|---|---|---|---|---|---|
| 1 | backend | 69 | ~6.5 min | ✅ ok | **$0.087** |
| 2 | frontend + backend + code-reviewer | 81 | ~5.5 min | ✅ ok | **$0.107** |
| 3 | backend | ~22 | ~6 min | ✅ ok | **$0.048** |
| 4+ | code-reviewer / frontend+backend / architect | — | — | ✅ ok | $0.05–0.10 each |

Completed tasks cost **$0.05–0.11 each**. The dominant cost is re-reading
context, not doing new work.

---

> **✅ RESOLVED (2026-06-09, ago v0.5.19).** A fresh long trace still showed
> this as the single most frequent error (35× in one session) — every one a
> wasted step, regardless of model. Fixed in `runner.rs`: `shell_exec` now
> accepts a command **string** and tokenizes it with `shlex` (shell-FREE — no
> `sh -c`, so metacharacters are not interpreted and `argv[0]` still passes the
> allowlist/deny policy). Unbalanced quotes return a clear `shell_unparseable_command`
> with the `["bash","-lc","<cmd>"]` hint for genuine pipelines. Tests:
> `shell_argv_string_is_tokenized_not_rejected`, `shell_string_runs_when_allowed`,
> `shell_string_deny_still_applies_to_argv0`, `shell_unparseable_string_is_clear`.
> The section below is the original diagnosis, kept for context.

> **✅ RESOLVED — false `path_outside_workspace` on new nested files (ago v0.5.20).**
> A live run that DID start committing still threw 11× `path_outside_workspace`
> on paths plainly inside `/work` (e.g. `/work/apps/01/src/backend/main.py`).
> Root cause in `sandbox.rs::enforce_workspace`: for a non-existent target it
> canonicalized only the **immediate** parent — if intermediate dirs didn't
> exist yet (a fresh nested path), that failed and the path was wrongly rejected
> as an escape. Fixed by walking up to the **nearest existing ancestor**,
> canonicalizing it, then rejoining the missing tail (with a `..`-in-tail guard
> so the rejoin can't be fooled). `file_write` already `mkdir -p`s the parents,
> so writes into deep new dirs now succeed. Tests:
> `deep_nonexistent_nested_path_accepted`, `deep_nonexistent_absolute_inside_accepted`,
> `nonexistent_tail_with_dotdot_rejected`.

> **✅ RESOLVED — multi-line paste fired N parallel turns (ago v0.5.21).** The
> `--client-tools` REPL read stdin line-by-line, so pasting a 5-line block sent
> 5 prompts → 5 team runs at once (live log: 8 prompts in 1.5 ms). Fixed by
> enabling **bracketed paste** (`ESC[?2004h`): a `feed_paste_line` state machine
> collapses an `ESC[200~…ESC[201~` block into ONE prompt (inner newlines kept; a
> pasted `:quit` is content, not a command). Tests:
> `feed_multiline_paste_is_one_prompt`, `feed_single_line_paste_collapses`,
> `feed_typed_line_passes_through`, `feed_pasted_quit_is_not_a_command`.

> **✅ RESOLVED — stateless `cd` cascade / `shell_nonzero_exit` (ago v0.5.22).**
> `shell_exec` ran one stateless process, so the model's natural `cd subdir`
> (a builtin) failed, then the *next* command ran in `/work` not the subdir and
> failed too — the dominant error class in a live trace (77× `shell_nonzero_exit`).
> Fix: **when `allow_all` is set** (the sandbox/container is the boundary, not a
> per-binary allowlist), a command **string** runs through `bash -lc`, so
> `cd x && cmd`, pipes, redirects, globs and builtins work as written. Gated on
> `allow_all` so a real `deny`/`allow` policy is never bypassed (`bash` itself
> still passes the deny gate). Argv **lists** and non-`allow_all` strings keep
> the strict, shell-free path. Tests: `shell_string_allow_all_runs_through_shell_with_cd`,
> `shell_string_allow_all_runs_pipeline`, `shell_string_allow_all_still_honors_deny_bash`.

> **✅ RESOLVED — REPL hangs forever on a dead/silent server (ago v0.5.23).** A
> live session froze: the server went silent after a step (hung LLM call or a
> dropped socket) and the client's receive loop `await`ed a frame that never
> came, with no way to notice. Added a liveness probe: after `IDLE_PROBE` (45 s)
> of silence the client pings; a live server (even mid slow-LLM-call) auto-pongs
> and the timer resets, so slow turns are never aborted; only after
> `MAX_SILENT_PROBES` (3) unanswered pings in a row (~135 s) does it declare the
> socket dead and tell the user to retry with `--resume`. Test:
> `liveness_probe_thresholds_are_sane`.

> **✅ RESOLVED — `cd` cascade also when sent as an argv LIST (ago v0.5.24).**
> v0.5.22 only routed STRINGS through `bash -lc`, but the model usually emits
> `argv` as a LIST (`["cd","app"]` or `["cd","app","&&","pytest"]`), which kept
> spawning `cd` directly → `'cd' is a shell builtin` + the downstream
> `shell_nonzero_exit` cascade persisted. Now, under `allow_all`, a list that
> leads with a builtin or contains a shell operator (`&&`, `|`, `>`, …) is
> rebuilt into a `bash -lc` line — operators kept raw, other tokens shell-quoted
> (`list_needs_shell` / `join_for_shell`). Plain commands (`["ls","-la"]`) stay a
> direct spawn. Tests: `shell_list_cd_chain_runs_via_shell`,
> `shell_list_bare_cd_is_noop_success_not_error`, `shell_list_normal_command_stays_direct`,
> `shell_list_cd_strict_still_fails_without_allow_all`, `join_for_shell_quotes_args_keeps_operators`.
> NB: a bare `cd` in one call followed by a command in the NEXT call still won't
> persist cwd (needs per-agent stateful cwd — deferred).

> **✅ RESOLVED — per-project `AGO.md` now reaches the server under
> `--client-tools` (ago v0.5.25).** The agent-host `Prompt` frame carries only
> text (no `cache_context` field), so a project's `AGO.md` was silently dropped
> in client-tools mode — the agent never saw rules like "don't run docker in the
> jail, prepare the compose instead." Now `run_native_agent_host` reads
> `rt.instructions` and `run_repl` folds it into the **first** prompt of the
> session (`fold_instructions`, consumed once; the server keeps it in the
> conversation). Tests: `fold_instructions_prepends_once_then_consumes`,
> `fold_instructions_none_is_passthrough`. Closes the earlier follow-up.

## ⭐ Confirmed root cause of the recurring tool errors: `shell_requires_argv_list`

Across **every** turn the trace showed `shell_exec` results coming back
`status=error` — ~20+ of them, in clusters. With the (then) opaque logging we
could only see *that* they failed, not *why*, so the first hypothesis was a
denied `rm` (the operator's symptom: *"I keep telling it to delete the
duplicate files and it doesn't work."*).

**The P1 fix proved that hypothesis wrong.** Once the reason was logged
(ago v0.5.16), the live trace showed the actual cause:

```
status=error reason="shell_requires_argv_list: shell_exec via agent-host
                      expects argv as a list; got a string"
```

Every diagnosable failure so far is **`shell_requires_argv_list`**, not a
denied binary. The agent (LLM) writes shell commands the way a human would —
as a **string** with shell operators (`&&`, `|`, `>`, `$(…)`, globs, `for …`
loops) — but the agent-host's `shell_exec` deliberately **refuses string
commands** and only accepts an **argv list** (no `shell=True` → no injection).
So a command like `find . -name '*.dup' -delete`, `rm $(…)`, or
`for f in …; do rm "$f"; done` is rejected outright.

This is almost certainly why "delete the duplicates" never worked: not because
`rm` is denied, but because the agent expresses the deletion as a **shell
string** the host won't run. The form of the command, not the binary.

**Lessons:**

1. **P1 (log the error reason) was the highest-leverage fix in this document** —
   it turned an hour of "mysterious `status=error`" into a one-line diagnosis,
   and corrected a wrong hypothesis (`rm` denied) into the real one
   (`shell_requires_argv_list`). ✅ shipped in v0.5.16.
2. **New finding — argv-list mismatch (P1-class):** the orchestrator should
   teach sub-agents that `shell_exec` is *one program + argv, no shell
   operators*, and/or offer a sandboxed `sh -c` path for compound commands
   (weighed against the injection risk the current design avoids on purpose).
3. **P2 (back off on repeated failures)** still applies — the agent re-issued
   the same string-form command many times instead of switching to argv.

---

## What's already good (keep it)

- **Multi-agent fan-out is solid** — team-lead decomposes and delegates,
  including 3 specialists in parallel; their steps interleave correctly.
- **Client-tools delegation works** — `file_read` / `file_write` /
  `shell_exec` run locally, mostly in single-digit milliseconds.
- **Tasks complete** — both observed turns ended `status="ok"` with a real
  synthesized answer (1.5 KB, 4 KB).
- The recent `--log-file`, per-agent colours, and project shell-policy
  features all behaved as intended.

---

## Findings, prioritised by impact

### P0 — No context compaction inside a sub-agent run (biggest cost driver) — ✅ DONE

> **Status: implemented.** `core/agent.py` now compacts mid-run: after every
> completion it records the billed `input_tokens`, and when that crosses
> `AgentConfig.compaction_token_threshold` (default **60 000**, `0` disables)
> the loop elides the oldest middle messages via `compact_messages()` —
> keeping `compaction_keep_head` setup messages + `compaction_keep_tail`
> recent ones, with a `[context compacted: …]` marker. It runs *before*
> `recover_dangling_tool_calls`, which repairs any tool_call the elision left
> dangling; the kept tail never starts on a `Role.TOOL` message, so no orphan
> tool responses are produced. A `agent.compactions` span attribute records
> how often it fired. See
> [cache-strategy.md § Mid-run context compaction](cache-strategy.md).
> Original analysis below.


**Evidence.** Within a single agent run the cumulative input grows monotonically
and never resets:

```
turn 1 backend:  step4 2.9k → step8 14.7k → step26 224k → step68 791k → step69 852k
turn 2 backend:  step9 25k  → step26 224k → step42 439k → step63 736k
turn 3 code-rev: step4 117k → step8 587k          (+470k in 4 steps)
```

The only resets in the trace coincide with a **new sub-agent starting**
(cumulative counter back to 0), **not** with compaction. So a long agent run
re-sends its entire accumulated history on every LLM call.

**Impact.** Cost and latency grow with the square of the run length. Most of
the $0.087 / $0.107 per turn is the model re-reading old tool output. Steps
also visibly slow down as the context grows.

**Proposed fix.** In the agent loop (`src/agent_orchestrator/dashboard/agent_runner.py`,
`run_agent`), compact when the working context crosses a threshold
(e.g. 60–100k tokens): summarise or drop the oldest tool results, keep the
task + recent turns. The project already documents compaction
(`docs/cache-strategy.md`) — confirm whether it runs for **sub-agents** and,
if so, **lower the trigger threshold** (today it effectively never fires
before ~800k).

---

### P0 follow-up — compaction did not bound the peak — ✅ DONE

> **Status: implemented (server-side, `core/agent.py`).** The 2026-06-09 trace
> showed compaction *firing* yet per-step `in=` still reaching **140k tokens**
> (2.3× the 60k threshold). Two root causes, both fixed.

**Cause.** (1) The kept tail was a **fixed message count** (`keep_tail=20`);
twenty recent messages — each a capped-but-non-trivial tool result — can alone
exceed the threshold, so compaction could never get the context below it. (2)
The trigger was **purely reactive** (`last_input_tokens > threshold`), so a
single turn that ballooned mid-run was only caught *after* it had already been
billed.

**Fix.**
- **Dynamic, threshold-scaled tail.** `compact_messages` now takes a
  `token_budget`; `_dynamic_keep_tail` keeps the largest suffix (≤ `keep_tail`,
  ≥ `compaction_min_keep_tail`) whose **estimated tokens** fit the budget. The
  budget is `compaction_target_ratio × threshold` (default 0.6), so a *lower
  threshold retains fewer tokens* — the context the next call sends scales with
  the threshold instead of overshooting to a multiple of it.
- **Proactive trigger.** The loop estimates the current history
  (`estimate_message_tokens`, ~4 chars/token over content + tool-call args) and
  compacts when **either** that estimate **or** the last billed input crosses
  the threshold — catching a single ballooning turn before it is sent.
- **Few-but-huge handled.** With a budget set, the old "history is short →
  skip" early return is bypassed, so a handful of enormous messages still
  compact.

Config: `compaction_target_ratio` (0.6), `compaction_min_keep_tail` (4) on
`AgentConfig`. Tests: `test_dynamic_*` in `tests/test_agent_context_cap.py`.
*Effect on the live remote run requires redeploying the orchestrator with this
`core/agent.py`.*

---

### P1 — Tool outputs are not capped before re-entering context — ✅ DONE

> **Status: implemented.** `core/agent.py` now folds each tool result into
> the conversation through `cap_tool_result_content(str(result),
> config.max_tool_result_chars)` (default **8000 chars**, `0` disables). It
> keeps a head-heavy head+tail slice with a `…[truncated N chars]…` marker,
> so a single large `file_read`/`shell_exec` can no longer dominate the rest
> of the run. This is a *context* cap, independent of the 10 MB transport
> cap. See [cache-strategy.md § Tool-result context cap](cache-strategy.md).
> Original analysis below.


**Evidence.** Single-step input jumps of **+60k tokens** (turn 1, step 55→57),
and **+470k over 4 steps** for code-reviewer reading files. The shell output
cap is `SHELL_OUTPUT_CAP = 10 MB` (`cli/src/agent_host/runner.rs`) ≈ 2.5M
tokens — far too large to put back into an LLM prompt.

**Impact.** A single `cat`/large `file_read`/verbose command can balloon the
context and dominate the bill for the rest of the run.

**Proposed fix.** Cap each tool result that re-enters the LLM context to a few
KB (configurable), keeping a head+tail slice with an explicit
`…[truncated N bytes]…` marker. Apply to `file_read` and `shell_exec`
especially. This is independent of the 10 MB transport cap (which can stay) —
it's a *context* cap, applied where the tool result is folded into the
conversation server-side.

---

### P1 — Tool errors are opaque (can't diagnose failures) — ✅ DONE (ago v0.5.16)

> **Status: implemented.** `debug_frame` and `send_tool_result` in
> `cli/src/agent_host/client.rs` now log the typed `error_code` plus the
> runner's best metadata field via a pure `failure_reason()` helper, so a
> failed tool call reads
> `status=error reason="shell_denied_by_policy: rm not allowed …"` instead
> of a blank `status=error`. Documented in
> [agent-host.md § Debug trace](agent-host.md). Original analysis below.


**Evidence.** ~20+ `status=error` tool results across the session
(`shell_exec` mostly, one `file_write`), often in clusters (5 in a row at
turn 1 steps 27–28). The debug trace shows only:

```
DEBUG send tool_result id=… status=error
```

No error code, no message — so neither the user nor an analyst can tell **why**
a command failed (denied by allowlist? missing binary? non-zero exit? path
outside workspace?).

> **This actually bit us.** The ~20 failures in this session were a **denied
> `rm`** (the agent trying to delete duplicate files; `rm` not in the
> allowlist) — but it took the operator telling us "deleting duplicates
> doesn't work" to realise it, because the log only said `status=error`. With
> the reason logged it would have been obvious from line one. See the
> "Confirmed root cause" section above.

**Proposed fix (small, CLI-side).** The client already *has* the error detail
(it executed the tool). Extend `debug_frame` in
`cli/src/agent_host/client.rs` to log the error code/first line of the message
on `status=error`:

```
DEBUG send tool_result id=… status=error reason="shell_denied: 'foo' not allowed"
```

This single change makes sessions like this self-diagnosing.

---

### P2 — No back-off on repeatedly-failing commands — ✅ DONE

> **Status: implemented.** The agent loop now tracks failures per approach
> (`tool name + arguments`) in `failure_counts`. Once an identical call has
> failed `AgentConfig.max_tool_failures_per_approach` times (default **2**,
> `0` disables), the next identical call is **not executed** — the loop
> appends a `[not executed] … already failed N times … try a different
> approach` tool message and moves on, so the agent stops burning steps +
> context on a doomed command (the denied-`rm` loop) and is steered to
> pivot, rather than stalling the whole run. Original analysis below.


**Evidence.** Error clusters recur throughout; the agent keeps re-issuing
commands that fail, burning steps **and** inflating context with the failure
output each time.

**Proposed fix.** Detect an identical (or near-identical) `argv` that has
already failed N times in the run and short-circuit it: return a terse
"this command has failed N times, do not retry it" result instead of executing
again. Server-side in the agent loop, or client-side in the runner.

---

### P2 — `turn_end` reports zero tokens/steps for team runs — ✅ DONE

> **Status: implemented.** `run_team` in `dashboard/agent_runner.py` now
> accumulates `total_input_tokens` / `total_output_tokens` / `total_steps`
> alongside the existing `total_tokens` (team-lead plan/validation/summary
> each count one step; sub-agents contribute their `input_tokens` /
> `output_tokens` / `steps_taken`) and returns them, so the `TurnEnd` in
> `cli_routes.py` reports real `↑/↓` and `steps` instead of zeros. Original
> analysis below.


**Evidence.**

```
recv turn_end status="ok" steps=0 in=0 out=0 cost=0.10743…
```

Cost is correct, but `steps`, `input_tokens`, and `output_tokens` are all 0 on
a team (`--agent team-lead`) turn, so the closing summary shows `↑0 ↓0`.

**Cause.** The agent-host handler maps `input_tokens`/`output_tokens` from the
runner result, but `run_team` only returns `total_tokens` (no split) — see the
team-lead branch in `src/agent_orchestrator/dashboard/cli_routes.py`.

**Proposed fix.** Map `run_team`'s `total_tokens` (and a step count) into the
`TurnEnd` fields so the summary reflects real usage.

---

### P2 — Jail base image lacks common tooling — ✅ DONE (ago v0.5.18)

> **Status: implemented.** Surfaced by the 2026-06-09 post-fix trace (see the
> verification block near the top): two `shell_spawn_failed: No such file or
> directory (os error 2)` errors in a jailed `devops` turn.
>
> **Shipped:** (1) the runner enriches the in-jail spawn error
> (`cli/src/agent_host/runner.rs`, `spawn_failure_detail`) so a missing binary
> reads *"'X' is not installed in the jail image; use a richer image via
> `jail_image:` in .ago.yaml or the AGO_JAIL_IMAGE env var"* instead of a bare
> `os error 2`; (2) `.ago.yaml` gained **`jail_image:`**
> (`ProjectPreset.jail_image`), and the `cli/ago` launcher resolves the image
> `AGO_JAIL_IMAGE` env → `.ago.yaml jail_image:` → `ubuntu:24.04`. Tests:
> `spawn_detail_*` in runner.rs, `jail_image_*` in project.rs. Docs:
> `managing-local-projects.md` § Jail-by-default.

**Symptom.** Under jail-by-default (v0.5.17+), `--client-tools` `shell_exec`
runs inside the launcher's container, whose default image is **bare
`ubuntu:24.04`** (`cli/ago`, `AGO_JAIL_IMAGE`). Common dev tools the agent
reaches for — `git`, `rg`, `python`, `node`, build toolchains — are not in that
base, so the spawn fails with `os error 2`. The agent self-recovers (the next
command returns `ok`), so the run still completes, but every miss burns a step
and a model round-trip.

**Cause.** The jail mounts the project and the binary but installs no tooling;
`ubuntu:24.04` ships almost nothing beyond coreutils.

**Proposed fix (any of, in order of effort).**
1. **Document + nudge:** the `AGO_JAIL_IMAGE` override already exists — call it
   out in `docs/managing-local-projects.md` with a recommended image, and emit a
   clearer error hint when a spawn fails inside the jail
   (`binary 'X' not found in jail image; set AGO_JAIL_IMAGE=…`).
2. **Ship a batteries-included default image** (`ago/jail:latest` with git, rg,
   python3, node, build-essential) and point `AGO_JAIL_IMAGE` at it by default.
3. **Let `.ago.yaml` declare a `jail_image:`** so a project pins its own
   toolchain.

---

### P3 — Step progress meter shows `total=0` — ✅ DONE

> **Status: implemented.** The agent-host `Step` frame now carries
> `total=max_steps` (resolved up front in `cli_routes.py`), so the Rust
> client's existing `[{index}/{total}]` renderer shows a real `[3/30]` meter.
> Labels are also enriched via `_step_label()`: it prefers an explicit
> `action`/`tool`/`message`, and otherwise surfaces a named phase
> (team-lead's `fallback` / `atomic_validation`) plus any `reason`, so steps
> no longer render blank. Original analysis below.


**Evidence.** Every `Step` frame carries `total=0`, so the client can't render
`[n/N]`. With parallel agents the `idx` is also a single global counter shared
across agents, which reads as out-of-order.

**Proposed fix.** Send `max_steps` as `Step.total`. For parallel team runs,
consider a per-agent step index (or label the line with the agent so the
global counter is less confusing — the per-agent colours already help here).

---

### P3 — Generic step labels — ◑ PARTIAL

> **Status: partially implemented.** `_step_label()` (see the meter item
> above) now surfaces named phases + reasons so team-lead steps aren't blank.
> Carrying the *concrete tool + args* (e.g. `working: shell_exec npm test`)
> still needs the single-agent loop to emit the tool name on its AGENT_STEP
> event — today it only emits `message="thinking"/"working"` before the tool
> call is known. Left as a follow-up. Original note below.


`label` is usually `"thinking"`/`"working"`/empty. Carrying the current action
or tool name (e.g. `working: shell_exec npm test`) would make the live trace
far more readable. Minor, but cheap.

---

### P3 — agent loops on unrecoverable jail spawn-fail — ✅ DONE

> **Status: implemented (2026-06-09, server-side `core/agent.py`).** Observed
> live: a jailed session hit the full `max_steps=40` after `cowsay` was missing
> — the agent kept trying to *obtain* the binary (`apt install …`, alternatives)
> instead of giving up.

**Symptom.** Inside the jail, a binary that is not in the image can usually not
be installed either (no network, no sudo). The agent doesn't know that, so it
spends the whole step budget on doomed `apt`/`pip`/`curl` attempts. The P2
back-off (`max_tool_failures_per_approach`) only catches *identical* repeated
calls; a varying command sequence sidesteps it.

**Fix — a consecutive-failure circuit breaker** (deliberately general, not
keyed to a CLI string). `AgentConfig.max_consecutive_tool_failures` (default 6,
0 disables) counts tool failures with **no successful tool call in between**,
regardless of whether the failing calls were identical — so the varying grind
(`cowsay` → `apt-get` → `pip` → …) is caught where the identical-args back-off
is blind. The counter **resets on any tool success**, so a healthy run that
occasionally retries is never cut short. On trip the run stops `STALLED` with an
**actionable** message: when the streak's error codes intersect
`_UNRECOVERABLE_ENV_ERRORS` (currently `shell_spawn_failed`), it tells the
operator the sandbox is missing tools it cannot install — i.e. set `jail_image:`
— rather than emitting a vague stall. Pairs with the client-side spawn-hint
(which already names the fix in the tool result). Tests:
`test_circuit_breaker_stops_varying_failure_grind`,
`test_circuit_breaker_resets_on_success`. *Takes effect on the remote once
redeployed.*

---

### P3 — `ago run --client-tools` one-shot hangs — 🔲 OPEN

> **Status: open (2026-06-09).** The jailed *non-interactive* `ago run
> --client-tools` connects (handshake OK, token bridged) but never sends the
> prompt or advances — the container sits idle until killed.

**Symptom.** `ago run --client-tools --agent X "task"` reaches `connected
run_id=…` then produces zero `send prompt` / `recv step` frames.
`chat --client-tools` (jailed) is unaffected and runs tools normally, so this
is **not** a jail-mechanics defect (token bridge, mounts, image all verified)
— it is specific to the one-shot `run` path.

**Likely cause / next step.** Probably an EOF/stdin or agent-host one-shot
lifecycle difference: piloting `chat` via a piped stdin showed the connection
tearing down at EOF mid-step, hinting the one-shot `run` may close the
agent-host session before the first prompt round-trips. Reproduce with
`--log-file`, compare the `run` vs `chat` agent-host teardown order.

---

## Suggested order of work

1. **P1 tool-error logging** (`debug_frame`) — tiny CLI change, immediately
   unblocks diagnosing the failures in this very session.
2. **P0 context compaction threshold** + **P1 context-side tool-output cap** —
   the two changes that actually cut cost/latency. Server-side.
3. **P2 repeated-failure back-off**, **P2 turn_end tokens**, **P3 meter/labels**
   — polish.

## How to reproduce / verify

```bash
ago --log-file ~/ago-session.log chat --client-tools --agent team-lead
# run a real multi-file task, then inspect the trace:
grep -E "status=error|turn_end|idx=" ~/ago-session.log
```

After the P1 logging fix, the `status=error` lines will carry the reason and
the remaining failures can be triaged directly.
