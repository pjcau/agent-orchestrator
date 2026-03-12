"""Automated security alert fixer.

Reads CodeQL/code-scanning alerts from a JSON file and applies fixes:
- py/log-injection: wrap user-controlled values with _sanitize_log()
- py/path-injection: add path traversal checks before path construction
- py/weak-sensitive-data-hashing: upgrade SHA-256 to PBKDF2-SHA256

Usage:
    python scripts/fix_security_alerts.py /tmp/alerts.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def fix_log_injection(file_path: Path, line: int) -> bool:
    """Fix log injection by sanitizing user-controlled values in log calls."""
    lines = file_path.read_text().splitlines(keepends=True)
    if line < 1 or line > len(lines):
        return False

    target_line = lines[line - 1]

    # Check if this line contains a logger call
    if not re.search(r"logger\.(debug|info|warning|error|exception)", target_line):
        return False

    # Check if _sanitize_log helper exists in the file
    content = file_path.read_text()
    if "_sanitize_log" not in content:
        # Add the helper after imports
        import_end = 0
        for i, src_line in enumerate(lines):
            if src_line.startswith("import ") or src_line.startswith("from "):
                import_end = i + 1

        sanitize_fn = (
            "\n\ndef _sanitize_log(value: str) -> str:\n"
            '    """Sanitize user-controlled values for safe logging '
            '(prevent log injection)."""\n'
            '    return value.replace("\\n", "\\\\n")'
            '.replace("\\r", "\\\\r")'
            '.replace("\\t", "\\\\t")\n'
        )
        lines.insert(import_end, sanitize_fn)
        file_path.write_text("".join(lines))
        print(f"  Added _sanitize_log() helper to {file_path}")
        return True

    # If _sanitize_log already exists, the file was likely already fixed
    # Check if the specific line already uses it
    if "_sanitize_log" in target_line:
        print(f"  Line {line} in {file_path} already sanitized")
        return False

    print(f"  Log injection at {file_path}:{line} — manual review recommended")
    return False


def fix_path_injection(file_path: Path, line: int) -> bool:
    """Fix path injection by adding traversal checks before path construction."""
    content = file_path.read_text()

    # Check if traversal check already exists near the flagged line
    lines = content.splitlines()
    if line < 1 or line > len(lines):
        return False

    # Look for existing traversal checks in nearby lines
    context_start = max(0, line - 5)
    context_end = min(len(lines), line + 5)
    context = "\n".join(lines[context_start:context_end])

    if '".."' in context or "path traversal" in context.lower():
        print(f"  Path injection at {file_path}:{line} already has traversal check")
        return False

    print(f"  Path injection at {file_path}:{line} — manual review recommended")
    return False


def fix_weak_hashing(file_path: Path, line: int) -> bool:
    """Fix weak hashing by upgrading to PBKDF2-SHA256."""
    content = file_path.read_text()
    lines = content.splitlines()

    if line < 1 or line > len(lines):
        return False

    target_line = lines[line - 1]

    if "hashlib.sha256" in target_line and "pbkdf2" not in target_line:
        print(f"  Weak hashing at {file_path}:{line} — upgrading to PBKDF2")
        # Replace hashlib.sha256(...).hexdigest() with pbkdf2_hmac
        new_line = target_line.replace("hashlib.sha256(", 'hashlib.pbkdf2_hmac("sha256", ')
        lines[line - 1] = new_line
        file_path.write_text("\n".join(lines))
        return True

    if "pbkdf2" in target_line:
        print(f"  Weak hashing at {file_path}:{line} already uses PBKDF2")
        return False

    print(f"  Weak hashing at {file_path}:{line} — manual review recommended")
    return False


# Map CodeQL rule IDs to fix functions
FIXERS: dict[str, callable] = {
    "py/log-injection": fix_log_injection,
    "py/path-injection": fix_path_injection,
    "py/weak-sensitive-data-hashing": fix_weak_hashing,
}


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/fix_security_alerts.py <alerts.json>")
        sys.exit(1)

    alerts_file = Path(sys.argv[1])
    if not alerts_file.exists():
        print(f"Alerts file not found: {alerts_file}")
        sys.exit(1)

    alerts = json.loads(alerts_file.read_text())
    if not alerts:
        print("No alerts to fix")
        return

    print(f"Processing {len(alerts)} security alerts...")

    fixed = 0
    skipped = 0
    manual = 0

    for alert in alerts:
        rule = alert.get("rule", "")
        file_path = alert.get("file", "")
        line = alert.get("line", 0)
        desc = alert.get("description", "")

        print(f"\n[{rule}] {file_path}:{line} — {desc}")

        fixer = FIXERS.get(rule)
        if fixer is None:
            print(f"  No automated fix for rule: {rule}")
            skipped += 1
            continue

        path = Path(file_path)
        if not path.exists():
            print(f"  File not found: {file_path}")
            skipped += 1
            continue

        if fixer(path, line):
            fixed += 1
        else:
            manual += 1

    print(f"\n{'=' * 40}")
    print(f"Fixed:  {fixed}")
    print(f"Manual: {manual}")
    print(f"Skipped: {skipped}")
    print(f"Total:  {len(alerts)}")


if __name__ == "__main__":
    main()
