#!/usr/bin/env python3
"""Analyze an ``ago --log-file`` session trace into an actionable report.

A raw agent-host log is a flat stream of frames; eyeballing it tells you little
about *how a turn actually went*. This turns one (or more) ``ago-session*.log``
files into a per-turn summary plus **red flags** that point at concrete
orchestrator improvements:

  * a turn that ran many steps but wrote **zero files** (spinning, not building)
  * step overshoot (final step index ≫ the planned ``total``)
  * ``shell_timeout`` (a command run in watch/interactive mode that never exits)
  * the **same command failing repeatedly** (thrash instead of changing tack)
  * cost / token blow-ups

It reads the fields already in the trace (``recv step``, ``tool_call``,
``tool_result``, ``turn_end``) and, when present, the ``args=`` summary the CLI
now logs — so a failing/looping command is named, not just counted.

Usage:
    python scripts/analyze_ago_session.py ~/ago-session_10.log
    python scripts/analyze_ago_session.py ~/ago-session*.log --json
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

_TS = r"^(?P<ts>\S+)\s+DEBUG\s+"
_RE_PROMPT = re.compile(_TS + r"send prompt (?P<bytes>\d+)B")
_RE_STEP = re.compile(
    _TS + r'recv step idx=(?P<idx>\d+) total=(?P<total>\d+) agent="(?P<agent>[^"]*)"'
)
_RE_TOOL_CALL = re.compile(
    _TS + r"recv tool_call id=(?P<id>\S+) name=(?P<name>\S+)(?: args=(?P<args>.*))?$"
)
_RE_TOOL_RESULT = re.compile(
    _TS + r"send tool_result id=(?P<id>\S+) status=(?P<status>\S+)(?P<rest>.*)$"
)
_RE_REASON = re.compile(r'reason="(?P<reason>[^"]*)"')
_RE_ERRCODE = re.compile(r"error_code=(?P<code>\S+)")
_RE_TURN_END = re.compile(
    _TS + r'recv turn_end status="(?P<status>[^"]*)" steps=(?P<steps>\d+) '
    r"in=(?P<in>\d+) out=(?P<out>\d+) cost=(?P<cost>[\d.]+) "
    r'error="(?P<error>[^"]*)"'
)

# A turn is "expensive" / "long" past these; tunes only the red-flag wording.
_COST_FLAG_USD = 0.10
_OVERSHOOT_RATIO = 1.5


@dataclass
class Turn:
    prompt_bytes: int = 0
    agents: set[str] = field(default_factory=set)
    max_step_idx: int = 0
    total_steps: int = 0  # planned budget echoed by the meter
    tool_counts: Counter = field(default_factory=Counter)
    error_reasons: Counter = field(default_factory=Counter)
    # id -> "name args" so a result can be attributed to its command.
    _calls: dict[str, str] = field(default_factory=dict)
    file_writes: list[str] = field(default_factory=list)
    failed_commands: Counter = field(default_factory=Counter)
    status: str = ""
    steps: int = 0
    tok_in: int = 0
    tok_out: int = 0
    cost_usd: float = 0.0
    error: str = ""

    @property
    def tool_errors(self) -> int:
        return sum(self.error_reasons.values())

    def red_flags(self) -> list[str]:
        flags: list[str] = []
        if self.tool_counts.get("file_write", 0) == 0 and self.max_step_idx >= 5:
            flags.append(f"0 file_write in {self.max_step_idx} steps — spinning, not building")
        if self.total_steps and self.max_step_idx > self.total_steps * _OVERSHOOT_RATIO:
            flags.append(f"step overshoot: {self.max_step_idx} vs planned {self.total_steps}")
        if self.error_reasons.get("shell_timeout"):
            flags.append(
                f"{self.error_reasons['shell_timeout']}× shell_timeout — "
                "a command ran in watch/interactive mode and never exited"
            )
        for cmd, n in self.failed_commands.items():
            if n >= 3 and cmd:
                flags.append(f"same command failed {n}×: {cmd}")
        if self.cost_usd >= _COST_FLAG_USD:
            flags.append(f"cost ${self.cost_usd:.4f} this turn")
        return flags


def parse_log(text: str) -> list[Turn]:
    """Parse a raw session log into turns (segments between prompt and turn_end)."""
    turns: list[Turn] = []
    cur: Turn | None = None

    def ensure() -> Turn:
        nonlocal cur
        if cur is None:
            cur = Turn()
            turns.append(cur)
        return cur

    for line in text.splitlines():
        if m := _RE_PROMPT.search(line):
            cur = Turn(prompt_bytes=int(m["bytes"]))
            turns.append(cur)
            continue
        if m := _RE_STEP.search(line):
            t = ensure()
            t.max_step_idx = max(t.max_step_idx, int(m["idx"]))
            t.total_steps = int(m["total"])
            t.agents.add(m["agent"])
            continue
        if m := _RE_TOOL_CALL.search(line):
            t = ensure()
            t.tool_counts[m["name"]] += 1
            label = f"{m['name']} {m['args']}".strip() if m["args"] else m["name"]
            t._calls[m["id"]] = label
            if m["name"] == "file_write" and m["args"]:
                t.file_writes.append(m["args"])
            continue
        if m := _RE_TOOL_RESULT.search(line):
            t = ensure()
            if m["status"] != "ok":
                rest = m["rest"]
                reason = _RE_REASON.search(rest)
                code = _RE_ERRCODE.search(rest)
                label = (reason["reason"] if reason else None) or (
                    code["code"] if code else "error"
                )
                t.error_reasons[label] += 1
                cmd = t._calls.get(m["id"], "")
                if cmd:
                    t.failed_commands[cmd] += 1
            continue
        if m := _RE_TURN_END.search(line):
            t = ensure()
            t.status = m["status"]
            t.steps = int(m["steps"])
            t.tok_in = int(m["in"])
            t.tok_out = int(m["out"])
            t.cost_usd = float(m["cost"])
            t.error = m["error"]
            cur = None
            continue

    return turns


def _turn_dict(i: int, t: Turn) -> dict[str, Any]:
    return {
        "turn": i,
        "agents": sorted(t.agents),
        "steps": t.steps or t.max_step_idx,
        "planned_total": t.total_steps,
        "status": t.status or "(running/unterminated)",
        "tok_in": t.tok_in,
        "tok_out": t.tok_out,
        "cost_usd": round(t.cost_usd, 4),
        "tools": dict(t.tool_counts),
        "tool_errors": t.tool_errors,
        "error_reasons": dict(t.error_reasons),
        "file_writes": t.file_writes,
        "red_flags": t.red_flags(),
    }


def format_report(turns: list[Turn]) -> str:
    out: list[str] = []
    out.append(f"{len(turns)} turn(s)\n")
    header = (
        f"{'#':>2} {'agents':<22} {'steps':>10} {'tok_in':>9} {'cost$':>8} "
        f"{'wr':>3} {'err':>4} {'status':>8}"
    )
    out.append(header)
    out.append("-" * len(header))
    total_cost = 0.0
    all_flags: list[str] = []
    for i, t in enumerate(turns, 1):
        total_cost += t.cost_usd
        steps = f"{t.steps or t.max_step_idx}/{t.total_steps or '?'}"
        agents = ",".join(sorted(t.agents))[:22]
        out.append(
            f"{i:>2} {agents:<22} {steps:>10} {t.tok_in:>9,} {t.cost_usd:>8.4f} "
            f"{t.tool_counts.get('file_write', 0):>3} {t.tool_errors:>4} "
            f"{(t.status or 'run'):>8}"
        )
        for flag in t.red_flags():
            all_flags.append(f"turn {i}: {flag}")
    out.append(f"\ntotal cost: ${total_cost:.4f}")

    if all_flags:
        out.append("\nRED FLAGS")
        out.append("-" * 8)
        out.extend(f"  ⚠ {f}" for f in all_flags)
    else:
        out.append("\nNo red flags.")

    # Surface the most-failed commands across the whole session — the single
    # most useful signal for "what is the agent getting stuck on".
    failed: Counter = Counter()
    for t in turns:
        failed.update(t.failed_commands)
    top = [(c, n) for c, n in failed.most_common(8) if c and n >= 2]
    if top:
        out.append("\nMOST-FAILED COMMANDS (count × command)")
        out.append("-" * 38)
        out.extend(f"  {n:>3} × {cmd}" for cmd, n in top)

    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze ago --log-file session traces into an actionable report."
    )
    parser.add_argument("logs", nargs="+", help="log file(s); globs allowed")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = parser.parse_args(argv)

    paths: list[str] = []
    for pattern in args.logs:
        paths.extend(sorted(glob.glob(pattern)) or [pattern])

    turns: list[Turn] = []
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                turns.extend(parse_log(fh.read()))
        except OSError as exc:
            print(f"error: cannot read {path}: {exc}", file=sys.stderr)
            return 2

    if not turns:
        print("No turns found in the given log(s).", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([_turn_dict(i, t) for i, t in enumerate(turns, 1)], indent=2))
    else:
        print(format_report(turns))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
