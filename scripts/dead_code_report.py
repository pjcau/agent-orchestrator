#!/usr/bin/env python3
"""Dead code report — find unused definitions under src/.

Two-view comparison:
  A) Definitions in src/ NOT used anywhere in src/ (truly dead production code)
  B) Definitions in src/ NOT used in src/ BUT used in tests/ (test-only — scaffolding never wired up)

Uses vulture for static analysis, then cross-references with grep.
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
    kind: str  # function, class, method, variable, attribute, import, unreachable
    name: str
    confidence: int
    raw: str

    @property
    def category(self) -> str:
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
    return [f for f in findings if not FALSE_POSITIVE_RE.search(f.raw)]


def _count_usages(name: str, search_dir: str, exclude_file: str, exclude_line: int) -> int:
    """Count how many times `name` appears in `search_dir`, excluding the definition."""
    if not Path(search_dir).exists():
        return 0
    result = subprocess.run(
        ["grep", "-rn", "--include=*.py", f"\\b{re.escape(name)}\\b", search_dir],
        capture_output=True,
        text=True,
    )
    count = 0
    for line in result.stdout.strip().splitlines():
        if f"{exclude_file}:{exclude_line}:" in line:
            continue
        count += 1
    return count


def classify_findings(
    findings: list[Finding],
) -> tuple[list[Finding], list[Finding]]:
    """Split findings into two groups:

    Returns:
        (dead_in_src, test_only)
        - dead_in_src: not used anywhere in src/ (truly dead)
        - test_only: not used in src/ but IS used in tests/ (scaffolding)
    """
    dead_in_src = []
    test_only = []

    extra_dirs = ["tests/", "examples/", "scripts/"]

    for f in findings:
        if f.kind == "unreachable":
            dead_in_src.append(f)
            continue

        name = f.name
        if not name:
            dead_in_src.append(f)
            continue

        # Check usage in src/ (excluding definition)
        src_usages = _count_usages(name, "src/", f.file, f.line)

        if src_usages > 1:
            # Used in src/ — not dead (vulture false positive)
            continue

        # Check usage in tests/, examples/, scripts/
        other_usages = 0
        for d in extra_dirs:
            other_usages += _count_usages(name, d, f.file, f.line)

        if other_usages > 0:
            test_only.append(f)
        else:
            dead_in_src.append(f)

    return dead_in_src, test_only


def _render_section(title: str, description: str, findings: list[Finding]) -> list[str]:
    """Render a findings section as markdown lines."""
    lines = [f"# {title}", "", description, ""]

    if not findings:
        lines.append("*No findings.*")
        lines.append("")
        return lines

    by_category: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        by_category[f.category].append(f)

    # Summary table
    lines.append(f"**Total: {len(findings)}**")
    lines.append("")
    lines.append("| Category | Count | Breakdown |")
    lines.append("|----------|-------|-----------|")
    for cat in sorted(by_category.keys()):
        items = by_category[cat]
        kinds: dict[str, int] = defaultdict(int)
        for f in items:
            kinds[f.kind] += 1
        breakdown = ", ".join(f"{v} {k}{'s' if v > 1 else ''}" for k, v in sorted(kinds.items()))
        lines.append(f"| {cat} | {len(items)} | {breakdown} |")
    lines.append("")

    # Detailed findings grouped by category → file
    for cat in sorted(by_category.keys()):
        items = by_category[cat]
        lines.append(f"## {cat}")
        lines.append("")

        by_file: dict[str, list[Finding]] = defaultdict(list)
        for f in items:
            by_file[f.file].append(f)

        for filepath in sorted(by_file.keys()):
            file_items = by_file[filepath]
            lines.append(f"### `{filepath}`")
            lines.append("")
            for f in sorted(file_items, key=lambda x: x.line):
                badge = "🔴" if f.confidence >= 90 else "🟡" if f.confidence >= 70 else "⚪"
                lines.append(
                    f"- {badge} **L{f.line}** — unused {f.kind} `{f.name}` ({f.confidence}%)"
                )
            lines.append("")

    return lines


def generate_report(dead_in_src: list[Finding], test_only: list[Finding]) -> str:
    """Generate dual-view markdown report."""
    lines = [
        "# Dead Code Report — Dual View",
        "",
        "Two comparisons of definitions in `src/`:",
        "",
        f"- **Section A — Dead in production** ({len(dead_in_src)}): "
        "not used anywhere in `src/`. Candidates for removal.",
        f"- **Section B — Test-only** ({len(test_only)}): "
        "not used in `src/` but used in `tests/`. "
        "Scaffolding written but never wired into production code.",
        "",
        "---",
        "",
    ]

    lines.extend(
        _render_section(
            "A — Dead in Production (not used in src/)",
            "These definitions exist in `src/` but are **never referenced by any other "
            "production code**. Safe candidates for removal or implementation.",
            dead_in_src,
        )
    )

    lines.append("---")
    lines.append("")

    lines.extend(
        _render_section(
            "B — Test-Only (used in tests/ but not in src/)",
            "These definitions exist in `src/` and have **test coverage**, but are "
            "**never called from production code**. They are scaffolding — features "
            "written and tested but never integrated. Wire them up or remove them.",
            test_only,
        )
    )

    lines.append("---")
    lines.append("")
    lines.append("**Legend:** 🔴 ≥90% confidence · 🟡 70-89% · ⚪ 60-69%")
    lines.append("")
    lines.append("*Generated by `scripts/dead_code_report.py` using vulture*")

    return "\n".join(lines)


def main() -> int:
    print("Running vulture analysis on src/...", file=sys.stderr)
    raw = run_vulture("src/", min_confidence=60)
    print(f"  Raw findings: {len(raw)}", file=sys.stderr)

    filtered = filter_false_positives(raw)
    print(f"  After false-positive filter: {len(filtered)}", file=sys.stderr)

    print("  Classifying: src/-only vs test-only...", file=sys.stderr)
    dead_in_src, test_only = classify_findings(filtered)
    print(f"  Dead in production (not in src/): {len(dead_in_src)}", file=sys.stderr)
    print(f"  Test-only (in tests/ but not src/): {len(test_only)}", file=sys.stderr)

    report = generate_report(dead_in_src, test_only)

    report_path = Path("docs/dead-code-report.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"\nReport written to {report_path}", file=sys.stderr)

    print(report)

    return 1 if (dead_in_src or test_only) else 0


if __name__ == "__main__":
    sys.exit(main())
