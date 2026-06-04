#!/usr/bin/env python3
"""One-shot audit driver for the research scout.

Re-runs the scout's LLM analysis on every past `no-improvements` and
`llm-error` state entry and writes per-repo findings files for the
CI workflow to turn into PRs.

Why this exists
---------------

Two weeks of nightly runs on the old free Qwen model produced
`improvements: []` for ~9 repos and `llm-error: HTTP 429` for ~4 more.
The model was the limiter, not the repos. After switching the scout
to `tencent/hy3-preview` with `reasoning.effort: low`, a hand-run on
one of those repos (book-to-skill) yielded 8 actionable improvements.

This driver replays the same call against every other previously
unproductive URL so we can recover the PR opportunities that were
silently dropped.

State is NOT modified (the script never touches
`.claude/research-scout-state.json`). That keeps the nightly cron's
view of the world clean — audit findings live in
`.claude/audit-findings/` and are consumed by the workflow, then
deleted after PRs are opened.

Outputs
-------

- `.claude/audit-findings/<owner>__<repo>.md`: PR-body-shaped findings
  file, one per repo with at least `--min-value-score` improvements.
- `.claude/audit-findings/_summary.json`: machine-readable summary
  consumed by the workflow's step summary.
- Per-repo files are only written for repos that pass the value-score
  filter — the workflow turns each into one PR.

Usage
-----

    # Drive from CI (`OPENROUTER_API_KEY` + `CI=1`):
    python scripts/audit_research_scout_history.py --min-value-score 6

    # Drive locally with claude CLI:
    python scripts/audit_research_scout_history.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT / "scripts" / "run_research_scout.py"
STATE = PROJECT / ".claude" / "research-scout-state.json"
OUTPUT_DIR = PROJECT / ".claude" / "audit-findings"
SUMMARY_FILE = OUTPUT_DIR / "_summary.json"

sys.path.insert(0, str(PROJECT / "src"))

_spec = importlib.util.spec_from_file_location("run_research_scout", str(SCRIPT))
_scout = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_scout)

# Reuse: same fetch, same LLM call, same parser as the nightly scout.
_call_llm = _scout._call_llm
_fetch_url = _scout._fetch_url
_parse_improvements = _scout._parse_improvements
CODEBASE_SUMMARY = _scout.CODEBASE_SUMMARY
MAX_IMPROVEMENTS = _scout.MAX_IMPROVEMENTS

_GH_REPO_RE = re.compile(r"github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/|$)")


def _is_audit_candidate(entry: dict) -> bool:
    """Pick state entries the new model has a chance of cracking.

    Excludes: `low-relevance` (keyword pre-filter would still reject) and
    `fetch-error` (the URL itself is the problem). Includes anything that
    ran the LLM and got no useful output (empty array, parse failure,
    HTTP 429)."""
    summary = entry.get("summary", "")
    improvements = entry.get("improvements") or []
    if summary.startswith("low-relevance") or summary.startswith("fetch-error"):
        return False
    return not improvements


def _build_prompt(text: str) -> str:
    return f"""## Our codebase
{CODEBASE_SUMMARY}

## Repository to analyze
{text[:8000]}

## Task
Analyze this repository and find up to {MAX_IMPROVEMENTS} concrete improvements
we could apply to our agent-orchestrator codebase. Quality over quantity — only
include improvements that:
- Are clearly inspired by a pattern/technique from this repo
- Map to a specific file in our codebase
- Include a code snippet showing the improvement

We rank proposals by **value_score** (higher = apply first), so be honest with
the scoring so the top candidates reflect real impact, not enthusiasm.

Respond with ONLY a JSON array, no other text. Each item must have:
- "component", "title", "description", "file", "code", "benefit"
- "impact", "effort", "risk", "value_score" (integers 1-10)

Prefer fewer, high-value items over many mediocre ones. If you find more than
{MAX_IMPROVEMENTS} candidates, submit only the top {MAX_IMPROVEMENTS} by
value_score. If nothing is worth applying, return an empty array: []
"""


def _safe_repo_slug(url: str) -> str:
    """`https://github.com/foo/bar` → `foo__bar`. Used in branch + filename."""
    m = _GH_REPO_RE.search(url)
    if not m:
        return re.sub(r"[^a-zA-Z0-9_-]+", "_", url)
    return f"{m.group(1)}__{m.group(2)}"


def _write_findings_md(repo_url: str, title: str, items: list[dict]) -> str:
    lines = [
        f"## research-scout audit — improvements from [{title}]({repo_url})",
        "",
        "_Re-analysed during the 2026-06 backfill. Original nightly run produced no improvements_"
        " _(model: free qwen at the time; this audit used the current scout model)._",
        "",
        f"**{len(items)}** actionable improvement(s) for the orchestrator.",
        "",
    ]
    for i, imp in enumerate(items, 1):
        score = imp.get("value_score")
        score_str = f" — value `{score:.1f}/10`" if isinstance(score, (int, float)) else ""
        lines.append(f"### {i}. {imp['title']}{score_str}")
        lines.append("")
        lines.append(f"**Component:** `{imp['component']}`")
        if imp.get("file"):
            lines.append(f"**File:** `{imp['file']}`")
        scoring = []
        for key in ("impact", "effort", "risk"):
            v = imp.get(key)
            if isinstance(v, (int, float)):
                scoring.append(f"{key} `{v:.0f}`")
        if scoring:
            lines.append(f"**Scoring:** {' · '.join(scoring)}")
        lines.append("")
        lines.append(imp["description"])
        if imp.get("code"):
            lines.append("")
            lines.append("```python")
            lines.append(imp["code"])
            lines.append("```")
        if imp.get("benefit"):
            lines.append("")
            lines.append(f"**Benefit:** {imp['benefit']}")
        lines.append("")
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--min-value-score",
        type=float,
        default=6.0,
        help="Only write a findings file when at least ONE improvement has "
        "value_score >= this. Default 6 — low enough to surface useful "
        "audits, high enough to skip mediocre ones.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N candidates (debugging).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    state = json.loads(STATE.read_text())
    candidates = sorted(
        (e.get("processed_at", "")[:10], u)
        for u, e in state["processed"].items()
        if _is_audit_candidate(e)
    )
    if args.limit:
        candidates = candidates[: args.limit]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Wipe stale per-repo files so we don't accidentally re-open PRs
    # from a previous failed audit.
    for old in OUTPUT_DIR.glob("*.md"):
        old.unlink()

    summary: dict = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": os.environ.get("SCOUT_MODEL", _scout.OPENROUTER_MODEL),
        "min_value_score": args.min_value_score,
        "total_candidates": len(candidates),
        "results": [],
    }

    for i, (date, url) in enumerate(candidates, 1):
        print(f"\n[{i}/{len(candidates)}] {url} (last seen {date})", flush=True)
        t0 = time.time()
        content = _fetch_url(url)
        if "error" in content:
            print(f"  fetch-error: {content['error']}")
            summary["results"].append(
                {
                    "url": url,
                    "outcome": "fetch-error",
                    "error": str(content["error"]),
                    "items": 0,
                }
            )
            continue
        title = content.get("title", url)
        text = content.get("text", "")
        prompt = _build_prompt(text)
        llm = _call_llm(prompt)
        if "error" in llm:
            print(f"  llm-error: {llm['error']}")
            summary["results"].append(
                {"url": url, "outcome": "llm-error", "error": llm["error"], "items": 0}
            )
            continue
        items, reason = _parse_improvements(llm["content"])
        elapsed = time.time() - t0
        print(f"  parsed {len(items)} item(s) — {reason} ({elapsed:.0f}s)")
        for imp in items[:3]:
            print(f"    [{imp['component']}] {imp['title']} (value={imp.get('value_score', '?')})")
        top_score = max((imp.get("value_score", 0) for imp in items), default=0)
        should_write = bool(items) and top_score >= args.min_value_score
        result = {
            "url": url,
            "title": title,
            "outcome": "improvements-found" if items else "no-improvements",
            "items": len(items),
            "top_value_score": top_score,
            "elapsed_s": round(elapsed, 1),
        }
        if should_write:
            slug = _safe_repo_slug(url)
            findings_path = OUTPUT_DIR / f"{slug}.md"
            findings_path.write_text(_write_findings_md(url, title, items), encoding="utf-8")
            result["findings_file"] = str(findings_path.relative_to(PROJECT))
            print(f"  → findings written to {findings_path.relative_to(PROJECT)}")
        else:
            why = "no items" if not items else f"top score {top_score} < {args.min_value_score}"
            result["skipped_pr"] = why
            print(f"  → skipping PR ({why})")
        summary["results"].append(result)
        # Persist after every iteration so a job killed mid-run leaves
        # usable partial output.
        SUMMARY_FILE.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    summary["repos_with_findings_file"] = sum(
        1 for r in summary["results"] if r.get("findings_file")
    )
    summary["total_improvements"] = sum(r["items"] for r in summary["results"])
    SUMMARY_FILE.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n=== AUDIT DONE ===")
    print(f"Candidates analysed: {len(summary['results'])}")
    print(
        f"Repos with findings file (≥1 item value≥{args.min_value_score}): "
        f"{summary['repos_with_findings_file']}"
    )
    print(f"Total improvements: {summary['total_improvements']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
