#!/usr/bin/env python3
"""Dead code & usage report — dual-view analysis of src/ definitions.

Three views:
  A) Definitions in src/ used within src/ but NOT in tests/ (needs test coverage)
  B) Definitions in src/ used in BOTH src/ AND tests/ (production + tested)
  C) Definitions in src/ NOT used in src/ at all (dead / scaffolding)

Uses vulture for dead code detection and grep for cross-referencing.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

# FastAPI route handlers and other known false positives for "unused" detection
FALSE_POSITIVE_NAMES = {
    "login_page",
    "login_github",
    "callback_github",
    "auth_me",
    "auth_logout",
    "auth_debug",
    "health",
    "websocket_endpoint",
    "stream_endpoint",
    "prometheus_metrics",
    "snapshot",
    "usage_stats",
    "agent_errors",
    "cache_clear",
    "agent_config",
    "agent_run",
    "team_run",
    "skill_invoke",
    "cost_preview",
    "openrouter_pricing",
    "ollama_pull",
    "ollama_delete",
    "list_files",
    "read_file",
    "new_conversation",
    "clear_conversation",
    "fork_conversation",
    "list_conversations",
    "presets",
    "graph_reset",
    "graph_replay",
    "graph_last_run",
    "download_zip",
    "dispatch",
    "list_jobs",
    "job_detail",
    "session_history",
    "events_endpoint",
    "agents_endpoint",
    "models_endpoint",
    "_startup",
    "jobs_list",
    "jobs_detail",
    "jobs_switch",
    "jobs_restore_conversation",
    "jobs_delete",
    "jobs_files",
    "jobs_file_content",
    "jobs_download_zip",
}

# Skip private/dunder methods and short names that cause false grep matches
SKIP_PATTERN = re.compile(r"^(__.*__|_[a-z]|[a-z]$)")


@dataclass
class Definition:
    file: str
    line: int
    kind: str  # "class", "function", "method"
    name: str
    src_usages: int = 0  # references in src/ (excluding definition)
    test_usages: int = 0  # references in tests/

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

    @property
    def short_file(self) -> str:
        return self.file.replace("src/agent_orchestrator/", "")


def extract_definitions(src_dir: str = "src/") -> list[Definition]:
    """Extract all class and function/method definitions from Python files."""
    defs = []
    src_path = Path(src_dir)

    # Match: class Foo, def bar, async def baz
    pattern = re.compile(r"^(\s*)(async\s+)?def\s+(\w+)|^(\s*)class\s+(\w+)")

    for py_file in sorted(src_path.rglob("*.py")):
        rel = str(py_file.relative_to("."))
        try:
            lines = py_file.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue

        for i, line in enumerate(lines, 1):
            m = pattern.match(line)
            if not m:
                continue

            indent = m.group(1) or m.group(4) or ""
            if m.group(5):  # class
                name = m.group(5)
                kind = "class"
            else:  # def/async def
                name = m.group(3)
                indent_level = len(indent)
                kind = "method" if indent_level >= 4 else "function"

            # Skip private, dunder, too-short names, and known false positives
            if SKIP_PATTERN.match(name):
                continue
            if name in FALSE_POSITIVE_NAMES:
                continue
            if len(name) <= 2:
                continue

            defs.append(Definition(file=rel, line=i, kind=kind, name=name))

    return defs


def count_usages(name: str, search_dir: str, exclude_file: str, exclude_line: int) -> int:
    """Count references to `name` in `search_dir`, excluding the definition itself."""
    if not Path(search_dir).exists():
        return 0
    result = subprocess.run(
        ["grep", "-rn", "--include=*.py", f"\\b{re.escape(name)}\\b", search_dir],
        capture_output=True,
        text=True,
    )
    count = 0
    for line in result.stdout.strip().splitlines():
        # Skip the definition line itself
        if f"{exclude_file}:{exclude_line}:" in line:
            continue
        count += 1
    return count


def classify_definitions(defs: list[Definition]) -> None:
    """Count src/ and tests/ usages for each definition (in-place)."""
    total = len(defs)
    for i, d in enumerate(defs):
        if (i + 1) % 50 == 0:
            print(f"  Scanning {i + 1}/{total}...", file=sys.stderr)
        d.src_usages = count_usages(d.name, "src/", d.file, d.line)
        d.test_usages = count_usages(d.name, "tests/", d.file, d.line)


def _render_section(
    title: str, description: str, items: list[Definition], show_usages: bool = False
) -> list[str]:
    """Render a list of definitions as markdown."""
    lines = [f"# {title}", "", description, ""]

    if not items:
        lines.append("*No findings.*")
        lines.append("")
        return lines

    by_category: dict[str, list[Definition]] = defaultdict(list)
    for d in items:
        by_category[d.category].append(d)

    # Summary
    lines.append(f"**Total: {len(items)}**")
    lines.append("")
    lines.append("| Category | Count | Breakdown |")
    lines.append("|----------|-------|-----------|")
    for cat in sorted(by_category.keys()):
        cat_items = by_category[cat]
        kinds: dict[str, int] = defaultdict(int)
        for d in cat_items:
            kinds[d.kind] += 1
        breakdown = ", ".join(
            f"{v} {k}{'es' if k == 'class' else 's'}" for k, v in sorted(kinds.items())
        )
        lines.append(f"| {cat} | {len(cat_items)} | {breakdown} |")
    lines.append("")

    # Detail by category → file
    for cat in sorted(by_category.keys()):
        cat_items = by_category[cat]
        lines.append(f"## {cat}")
        lines.append("")

        by_file: dict[str, list[Definition]] = defaultdict(list)
        for d in cat_items:
            by_file[d.file].append(d)

        for filepath in sorted(by_file.keys()):
            file_items = by_file[filepath]
            lines.append(f"### `{filepath}`")
            lines.append("")
            for d in sorted(file_items, key=lambda x: x.line):
                usage_info = ""
                if show_usages:
                    usage_info = f" (src: {d.src_usages}, tests: {d.test_usages})"
                lines.append(f"- **L{d.line}** — {d.kind} `{d.name}`{usage_info}")
            lines.append("")

    return lines


def generate_report(
    src_only: list[Definition],
    src_and_tests: list[Definition],
    dead: list[Definition],
) -> str:
    """Generate the dual-view report."""
    lines = [
        "# Code Usage Report — Dual View",
        "",
        f"Analysis of all public definitions in `src/` ({len(src_only) + len(src_and_tests) + len(dead)} total):",
        "",
        f"- **Section A — Used in src/ only** ({len(src_only)}): "
        "production code WITHOUT test coverage — needs tests.",
        f"- **Section B — Used in src/ AND tests/** ({len(src_and_tests)}): "
        "production code WITH test coverage — healthy.",
        f"- **Section C — Not used in src/** ({len(dead)}): "
        "dead code or scaffolding (may be used only in tests).",
        "",
        "---",
        "",
    ]

    lines.extend(
        _render_section(
            "A — Used in src/ only (no test coverage)",
            "These definitions are used in production code but have **no references in tests/**. "
            "Priority candidates for adding test coverage.",
            src_only,
            show_usages=True,
        )
    )

    lines.append("---")
    lines.append("")

    lines.extend(
        _render_section(
            "B — Used in src/ AND tests/ (covered)",
            "These definitions are used in production AND have test coverage. "
            "This is the healthy, well-connected code.",
            src_and_tests,
            show_usages=True,
        )
    )

    lines.append("---")
    lines.append("")

    lines.extend(
        _render_section(
            "C — Not used in src/ (dead / scaffolding)",
            "These definitions exist in `src/` but are **never referenced by production code**. "
            "Some may be used only in tests (scaffolding). Candidates for wiring up or removal.",
            dead,
            show_usages=True,
        )
    )

    lines.append("---")
    lines.append(
        f"*Generated by `scripts/dead_code_report.py` — "
        f"{len(src_and_tests)} covered, "
        f"{len(src_only)} need tests, "
        f"{len(dead)} dead*"
    )

    return "\n".join(lines)


def main() -> int:
    print("Extracting definitions from src/...", file=sys.stderr)
    defs = extract_definitions("src/")
    print(f"  Found {len(defs)} public definitions", file=sys.stderr)

    print("Counting usages in src/ and tests/...", file=sys.stderr)
    classify_definitions(defs)

    # Classify into three buckets
    src_only = []  # used in src/, not in tests/
    src_and_tests = []  # used in both
    dead = []  # not used in src/

    for d in defs:
        if d.src_usages > 1:  # >1 because definition itself may match
            if d.test_usages > 0:
                src_and_tests.append(d)
            else:
                src_only.append(d)
        else:
            dead.append(d)

    print(f"\n  A — Used in src/ only (no tests): {len(src_only)}", file=sys.stderr)
    print(f"  B — Used in src/ AND tests/:      {len(src_and_tests)}", file=sys.stderr)
    print(f"  C — Not used in src/ (dead):       {len(dead)}", file=sys.stderr)

    report = generate_report(src_only, src_and_tests, dead)

    report_path = Path("docs/dead-code-report.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"\nReport written to {report_path}", file=sys.stderr)

    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
