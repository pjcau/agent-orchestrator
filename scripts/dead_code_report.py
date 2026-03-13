#!/usr/bin/env python3
"""Dead code report — find unused definitions under src/.

Uses vulture for static analysis, then cross-references with grep to reduce
false positives (FastAPI routes, enum values, dataclass fields, etc.).

Output: Markdown report grouped by category, suitable for PR body or local review.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

# Patterns that indicate false positives — skip these
FALSE_POSITIVE_PATTERNS = [
    # FastAPI/Starlette route handlers (decorated with @app.get, @router.post, etc.)
    r"unused function '(login_page|login_github|callback_github|auth_me|auth_logout|"
    r"auth_debug|admin_\w+|health|websocket_endpoint|stream_endpoint|"
    r"prometheus_metrics|snapshot|usage_stats|agent_errors|cache_clear|"
    r"agent_config|agent_run|team_run|skill_invoke|cost_preview|"
    r"openrouter_pricing|ollama_pull|ollama_delete|list_files|read_file|"
    r"new_conversation|clear_conversation|fork_conversation|list_conversations|"
    r"presets|graph_reset|graph_replay|graph_last_run|download_zip|"
    r"dispatch|list_jobs|job_detail|session_history|events_endpoint|agents_endpoint|"
    r"models_endpoint|_startup|jobs_list|jobs_detail|jobs_switch|"
    r"jobs_restore_conversation|jobs_delete|jobs_files|jobs_file_content|"
    r"jobs_download_zip)'",
    # Starlette middleware dispatch
    r"unused method 'dispatch'",
    # __aexit__ parameters
    r"unused variable '(exc_type|exc_val|exc_tb)'",
    # Enum members (used via string matching or serialisation)
    r"unused variable '(PENDING|RUNNING|ESCALATED|PERIOD_TASK|PERIOD_SESSION|PERIOD_DAY|"
    r"EVENT_\w+|GRAPH_EDGE|ARTIFACT_PUBLISHED|PATCH)'",
    # Dataclass fields (accessed dynamically or serialised)
    r"unused variable '(parent_task_id|rule_name|triggered_at|threshold|permissions|hit_count)'",
    # Attributes set in __init__ (used externally)
    r"unused attribute '(_cache|_cache_policy|_updated|hit_count)'",
]

FALSE_POSITIVE_RE = re.compile("|".join(FALSE_POSITIVE_PATTERNS))


@dataclass
class Finding:
    file: str
    line: int
    kind: str  # function, class, method, variable, attribute, import
    name: str
    confidence: int
    raw: str

    @property
    def module(self) -> str:
        """Extract module path from file path."""
        p = self.file.replace("src/", "").replace("/", ".").replace(".py", "")
        return p

    @property
    def category(self) -> str:
        """Categorise by directory."""
        if "dashboard" in self.file:
            return "Dashboard"
        if "providers" in self.file:
            return "Providers"
        if "skills" in self.file:
            return "Skills"
        if "core" in self.file:
            return "Core"
        return "Other"


def run_vulture(src_dir: str = "src/", min_confidence: int = 60) -> list[Finding]:
    """Run vulture and parse output."""
    result = subprocess.run(
        ["vulture", src_dir, f"--min-confidence={min_confidence}"],
        capture_output=True,
        text=True,
    )
    # vulture exits 3 when it finds dead code, 0 when clean
    output = result.stdout + result.stderr
    findings = []
    pattern = re.compile(
        r"^(.+?):(\d+): (unused \w+ '(.+?)'|unreachable code after '(.+?)') \((\d+)% confidence\)$"
    )
    for line in output.strip().splitlines():
        m = pattern.match(line.strip())
        if not m:
            continue
        file_path = m.group(1)
        line_no = int(m.group(2))
        desc = m.group(3)
        name = m.group(4) or m.group(5) or ""
        confidence = int(m.group(6))

        # Determine kind
        kind = "unknown"
        for k in ("function", "class", "method", "variable", "attribute", "import"):
            if k in desc:
                kind = k
                break
        if "unreachable" in desc:
            kind = "unreachable"

        findings.append(
            Finding(
                file=file_path,
                line=line_no,
                kind=kind,
                name=name,
                confidence=confidence,
                raw=line.strip(),
            )
        )
    return findings


def filter_false_positives(findings: list[Finding]) -> list[Finding]:
    """Remove known false positives."""
    filtered = []
    for f in findings:
        if FALSE_POSITIVE_RE.search(f.raw):
            continue
        filtered.append(f)
    return filtered


def cross_reference_usage(findings: list[Finding], src_dir: str = "src/") -> list[Finding]:
    """Check if a finding is actually used somewhere via grep."""
    confirmed = []
    for f in findings:
        if f.kind == "unreachable":
            confirmed.append(f)
            continue

        name = f.name
        if not name:
            confirmed.append(f)
            continue

        # Search for usage of this name in the entire src tree (excluding the definition line)
        result = subprocess.run(
            ["grep", "-rn", "--include=*.py", f"\\b{re.escape(name)}\\b", src_dir],
            capture_output=True,
            text=True,
        )
        # Count occurrences excluding the definition file:line
        usages = 0
        for line in result.stdout.strip().splitlines():
            # Skip the definition itself
            if f"{f.file}:{f.line}:" in line:
                continue
            # Skip imports of this name (they count as usage only if used after)
            usages += 1

        if usages <= 1:
            # 0 = truly unused, 1 = might be just one import with no call
            confirmed.append(f)
    return confirmed


def generate_report(findings: list[Finding]) -> str:
    """Generate markdown report."""
    by_category: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        by_category[f.category].append(f)

    lines = [
        "# Dead Code Report",
        "",
        f"**Total findings:** {len(findings)}",
        "",
        "Definitions in `src/` that appear unused. Review each before removing —",
        "some may be used dynamically, via reflection, or by external consumers.",
        "",
    ]

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Category | Count | Breakdown |")
    lines.append("|----------|-------|-----------|")
    for cat in sorted(by_category.keys()):
        items = by_category[cat]
        kinds = defaultdict(int)
        for f in items:
            kinds[f.kind] += 1
        breakdown = ", ".join(f"{v} {k}{'s' if v > 1 else ''}" for k, v in sorted(kinds.items()))
        lines.append(f"| {cat} | {len(items)} | {breakdown} |")
    lines.append("")

    # Detailed findings by category
    for cat in sorted(by_category.keys()):
        items = by_category[cat]
        lines.append(f"## {cat}")
        lines.append("")

        # Group by file
        by_file: dict[str, list[Finding]] = defaultdict(list)
        for f in items:
            by_file[f.file].append(f)

        for filepath in sorted(by_file.keys()):
            file_items = by_file[filepath]
            lines.append(f"### `{filepath}`")
            lines.append("")
            for f in sorted(file_items, key=lambda x: x.line):
                confidence_badge = (
                    "🔴" if f.confidence >= 90 else "🟡" if f.confidence >= 70 else "⚪"
                )
                lines.append(
                    f"- {confidence_badge} **L{f.line}** — "
                    f"unused {f.kind} `{f.name}` ({f.confidence}%)"
                )
            lines.append("")

    # Action items
    lines.append("## Recommended Actions")
    lines.append("")
    lines.append("1. **🔴 High confidence (90%+):** Likely safe to remove")
    lines.append("2. **🟡 Medium confidence (70-89%):** Check if used dynamically or in tests")
    lines.append(
        "3. **⚪ Lower confidence (60-69%):** May be used via reflection, config, or external API"
    )
    lines.append("")
    lines.append("---")
    lines.append("*Generated by `scripts/dead_code_report.py` using vulture*")

    return "\n".join(lines)


def main() -> int:
    print("Running vulture analysis on src/...", file=sys.stderr)
    raw = run_vulture("src/", min_confidence=60)
    print(f"  Raw findings: {len(raw)}", file=sys.stderr)

    filtered = filter_false_positives(raw)
    print(f"  After false-positive filter: {len(filtered)}", file=sys.stderr)

    confirmed = cross_reference_usage(filtered, "src/")
    print(f"  After cross-reference: {len(confirmed)}", file=sys.stderr)

    report = generate_report(confirmed)

    # Write report
    report_path = Path("dead-code-report.md")
    report_path.write_text(report, encoding="utf-8")
    print(f"\nReport written to {report_path}", file=sys.stderr)

    # Also print to stdout for CI
    print(report)

    return 1 if confirmed else 0


if __name__ == "__main__":
    sys.exit(main())
