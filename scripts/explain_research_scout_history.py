#!/usr/bin/env python3
"""Explain past research-scout runs by walking `.claude/research-scout-state.json`.

Why this script exists
----------------------

The nightly scout commits `research-scout: update state` to main every day,
which makes the git log noisy with no obvious signal about *why* each run
didn't produce a PR. This tool reads the state file, classifies each entry
by outcome (using the explicit `outcome` field when present, falling back
to parsing the legacy `summary` prefix), and prints a markdown summary.

Output goes to stdout — pipe it to a file or paste it in a PR description.

Usage
-----

    python scripts/explain_research_scout_history.py            # last 30 days
    python scripts/explain_research_scout_history.py --days 14  # custom window
    python scripts/explain_research_scout_history.py --all      # everything
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to path so we can import the tracker constants.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_orchestrator.core.bookmark_tracker import (
    OUTCOME_FETCH_ERROR,
    OUTCOME_IMPROVEMENTS_FOUND,
    OUTCOME_LLM_ERROR,
    OUTCOME_LOW_RELEVANCE,
    OUTCOME_NO_IMPROVEMENTS,
    classify_legacy_outcome,
)

STATE_FILE = Path(".claude/research-scout-state.json")

OUTCOME_ORDER = [
    OUTCOME_IMPROVEMENTS_FOUND,
    OUTCOME_NO_IMPROVEMENTS,
    OUTCOME_LOW_RELEVANCE,
    OUTCOME_LLM_ERROR,
    OUTCOME_FETCH_ERROR,
]

OUTCOME_LABELS = {
    OUTCOME_IMPROVEMENTS_FOUND: "✓ Improvements found (PR opened)",
    OUTCOME_NO_IMPROVEMENTS: "○ LLM ran, no actionable items",
    OUTCOME_LOW_RELEVANCE: "↷ Skipped by keyword pre-filter",
    OUTCOME_LLM_ERROR: "✗ LLM call failed (recorded)",
    OUTCOME_FETCH_ERROR: "✗ Fetch failed",
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--state",
        type=Path,
        default=STATE_FILE,
        help="Path to research-scout-state.json (default: %(default)s)",
    )
    p.add_argument(
        "--days",
        type=int,
        default=30,
        help="Only report entries from the last N days (default: %(default)d)",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Report every entry, ignoring --days.",
    )
    return p.parse_args(argv)


def _load_state(path: Path) -> dict:
    if not path.exists():
        sys.stderr.write(f"State file not found: {path}\n")
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8"))


def _classify(entry: dict) -> str:
    """Pull the outcome from the entry, falling back to legacy parsing."""
    outcome = entry.get("outcome")
    if outcome:
        return outcome
    return classify_legacy_outcome(entry.get("summary", ""), entry.get("improvements") or [])


def _format_repo(url: str) -> str:
    """`https://github.com/foo/bar` → `foo/bar` for table compactness."""
    if "github.com/" in url:
        return url.split("github.com/", 1)[1].rstrip("/")
    return url


def _format_reason(entry: dict, outcome: str) -> str:
    """The `reason` field, or a sensible default derived from summary."""
    if entry.get("reason"):
        return entry["reason"]
    summary = entry.get("summary", "")
    if outcome == OUTCOME_LLM_ERROR and ":" in summary:
        return summary.split(":", 1)[1].strip()
    if outcome == OUTCOME_NO_IMPROVEMENTS:
        return "no reason recorded (legacy entry)"
    return summary or "—"


def build_report(state: dict, days: int | None) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days is not None else None
    processed = state.get("processed", {})

    rows: list[tuple[datetime, str, str, str, str]] = []
    counts: Counter[str] = Counter()

    for url, entry in processed.items():
        proc_at = entry.get("processed_at", "")
        try:
            dt = datetime.fromisoformat(proc_at.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if cutoff and dt < cutoff:
            continue
        outcome = _classify(entry)
        counts[outcome] += 1
        rows.append(
            (
                dt,
                _format_repo(url),
                outcome,
                entry.get("summary", ""),
                _format_reason(entry, outcome),
            )
        )

    rows.sort(key=lambda r: r[0], reverse=True)

    window = "all time" if days is None else f"last {days} days"
    out: list[str] = [f"# Research Scout — outcomes ({window})", ""]

    if not rows:
        out.append("_No entries in this window._")
        return "\n".join(out)

    out.append(f"**Total runs analysed:** {len(rows)}")
    out.append("")
    out.append("| Outcome | Count |")
    out.append("|---|---:|")
    for o in OUTCOME_ORDER:
        if counts[o]:
            out.append(f"| {OUTCOME_LABELS[o]} | {counts[o]} |")
    out.append("")

    out.append("## Per-run detail")
    out.append("")
    out.append("| Date | Repo | Outcome | Why |")
    out.append("|---|---|---|---|")
    for dt, repo, outcome, _summary, reason in rows:
        # Strip pipes from reason to keep the markdown table valid.
        reason_md = reason.replace("|", "\\|")
        out.append(
            f"| {dt.strftime('%Y-%m-%d')} | `{repo}` | {OUTCOME_LABELS.get(outcome, outcome)} | {reason_md} |"
        )

    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    state = _load_state(args.state)
    days = None if args.all else args.days
    print(build_report(state, days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
