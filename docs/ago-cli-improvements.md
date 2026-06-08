# ago CLI — improvement proposals from a live session analysis

**Date:** 2026-06-08
**Source:** live `--log-file` trace of a real `ago chat --client-tools --agent team-lead`
session on an external project (multiple turns over ~90 min observed).
**Method:** the new `ago --log-file` flag (v0.5.14) recorded the full debug
frame trace; this document consolidates what the trace revealed.

---

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

## ⭐ Confirmed root cause of the recurring tool errors: a blocked `rm`

Across **every** turn the trace shows `shell_exec` results coming back
`status=error` — ~20+ of them, in clusters. With the (then) opaque logging we
could only see *that* they failed, not *why*. The operator then reported the
real-world symptom: **"I keep telling it to delete the duplicate files and it
doesn't work."**

These are the same thing. `rm` is **not** in the global allowlist
(`~/.cache/ago/shell-allow.json` held only `docker, find, git, ls, mkdir,
node, npm, npx`), and the project shell policy denied it. So every time the
agent ran `rm <duplicate>` the gate refused it → `status=error` → the file
was never deleted → the user re-asked → the agent retried → more errors and
more wasted context. A perfect, self-inflicted loop.

**Two lessons, both already in the proposals below:**

1. **P1 (log the error reason)** would have made this *instantly* obvious —
   `status=error reason="shell_denied: rm not allowed"` instead of a blank
   `status=error`. This single change turns "it doesn't work" into a
   one-line diagnosis. **Highest-leverage fix in this document.**
2. **P2 (back off on repeated failures)** would have stopped the agent from
   re-issuing the same denied `rm` dozens of times, saving steps and context.

Operator fix for the deletion itself: allow `rm` for that project (remove it
from `.ago.yaml` `shell.deny`, add it to `shell.allow`) — `deny` is a hard
block that even a confirmation can't override.

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

### P2 — No back-off on repeatedly-failing commands

**Evidence.** Error clusters recur throughout; the agent keeps re-issuing
commands that fail, burning steps **and** inflating context with the failure
output each time.

**Proposed fix.** Detect an identical (or near-identical) `argv` that has
already failed N times in the run and short-circuit it: return a terse
"this command has failed N times, do not retry it" result instead of executing
again. Server-side in the agent loop, or client-side in the runner.

---

### P2 — `turn_end` reports zero tokens/steps for team runs

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

### P3 — Step progress meter shows `total=0`

**Evidence.** Every `Step` frame carries `total=0`, so the client can't render
`[n/N]`. With parallel agents the `idx` is also a single global counter shared
across agents, which reads as out-of-order.

**Proposed fix.** Send `max_steps` as `Step.total`. For parallel team runs,
consider a per-agent step index (or label the line with the agent so the
global counter is less confusing — the per-agent colours already help here).

---

### P3 — Generic step labels

`label` is usually `"thinking"`/`"working"`/empty. Carrying the current action
or tool name (e.g. `working: shell_exec npm test`) would make the live trace
far more readable. Minor, but cheap.

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
