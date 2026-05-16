"""Failure-pattern registry — deterministic short-circuits for known failures.

Each pattern owns a regex over a `VerifierFailure.message` and a built-in
auto-fix action. When a pattern matches, the `RepairLoop` applies the fix
without calling an LLM — saving cost, time, and the risk of the agent
re-introducing the same bug.

Three actions ship in this module:

- `pip_pin_repair`: rewrite a `requirements*.txt` line to a known-good pin
  (e.g. `psycopg<3` → `psycopg2-binary>=2.9`).
- `unicode_unescape`: rewrite a file whose newlines / quotes were escaped
  one layer too many (the literal-`\\n` corruption mode from 2026-05-16).
- `noop`: emit an action that records the match but does nothing — useful
  for surfacing known-but-unfixable failures in the dashboard so an
  operator sees them without the loop wasting an LLM call.

Patterns are loaded from YAML (`core/failure_patterns.yaml`). New patterns
can be added there without touching this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from agent_orchestrator.core.verification_gate import VerifierFailure


@dataclass(frozen=True)
class RepairAction:
    kind: Literal["file_rewrite", "noop"]
    file: str | None
    new_content: str | None
    explanation: str


@dataclass(frozen=True)
class FailurePattern:
    name: str
    category: str
    pattern: re.Pattern[str]
    action_type: Literal["pip_pin_repair", "unicode_unescape", "noop"]
    action_params: dict[str, Any]
    llm_required: bool = False


class FailurePatternRegistry:
    """Holds compiled patterns and dispatches the matching auto-fix.

    The registry is **stateless across files** — every `apply()` call reads
    the current workspace state to compute a fix. Patterns are matched in
    insertion order; the first match wins.
    """

    def __init__(self, patterns: list[FailurePattern]) -> None:
        self._patterns = patterns

    @classmethod
    def from_yaml(cls, path: Path) -> FailurePatternRegistry:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError(f"{path}: expected a YAML list, got {type(data).__name__}")
        patterns: list[FailurePattern] = []
        for i, entry in enumerate(data):
            try:
                patterns.append(_pattern_from_dict(entry))
            except Exception as exc:  # noqa: BLE001 — surface index of bad entry
                raise ValueError(f"{path}: entry #{i} ({entry.get('name', '?')}) invalid: {exc}") from exc
        return cls(patterns)

    @property
    def patterns(self) -> tuple[FailurePattern, ...]:
        return tuple(self._patterns)

    def match(self, failure: VerifierFailure) -> FailurePattern | None:
        for p in self._patterns:
            if p.category and p.category != failure.category:
                continue
            if p.pattern.search(failure.message):
                return p
        return None

    async def apply(self, failure: VerifierFailure, workdir: Path) -> RepairAction | None:
        p = self.match(failure)
        if p is None:
            return None
        action = _ACTION_HANDLERS.get(p.action_type)
        if action is None:
            return None
        return action(failure, workdir, p.action_params)


# ---------------------------------------------------------------------------
# YAML loading helpers
# ---------------------------------------------------------------------------


def _pattern_from_dict(d: dict[str, Any]) -> FailurePattern:
    name = d["name"]
    category = d.get("category", "")
    pattern = re.compile(d["pattern"])
    af = d.get("auto_fix") or {}
    action_type = af.get("type")
    if action_type not in {"pip_pin_repair", "unicode_unescape", "noop"}:
        raise ValueError(f"unknown auto_fix.type: {action_type!r}")
    return FailurePattern(
        name=name,
        category=category,
        pattern=pattern,
        action_type=action_type,
        action_params={k: v for k, v in af.items() if k != "type"},
        llm_required=bool(d.get("llm_required", False)),
    )


# ---------------------------------------------------------------------------
# Action implementations
# ---------------------------------------------------------------------------


def _action_pip_pin_repair(
    failure: VerifierFailure,
    workdir: Path,
    params: dict[str, Any],
) -> RepairAction | None:
    """Rewrite a requirements-file line to a known-good pin.

    `params` schema::

        replacements:
          psycopg: "psycopg2-binary>=2.9"
          old-pkg: "new-pkg>=1.0"
    """
    if failure.file is None:
        return None
    target = workdir / failure.file
    if not target.exists():
        return None
    replacements: dict[str, str] = params.get("replacements", {})
    if not replacements:
        return None

    try:
        original = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    new_lines: list[str] = []
    changed = False
    for raw in original.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            new_lines.append(raw)
            continue
        # Extract the package name (first identifier on the line, lowercased).
        m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._\-]*)", raw)
        if not m:
            new_lines.append(raw)
            continue
        pkg = m.group(1).lower()
        if pkg in replacements:
            new_lines.append(replacements[pkg])
            changed = True
        else:
            new_lines.append(raw)

    if not changed:
        return None

    new_content = "\n".join(new_lines)
    if not new_content.endswith("\n"):
        new_content += "\n"
    target.write_text(new_content, encoding="utf-8")
    return RepairAction(
        kind="file_rewrite",
        file=failure.file,
        new_content=new_content,
        explanation=f"replaced unresolvable pin(s) in {failure.file}",
    )


def _action_unicode_unescape(
    failure: VerifierFailure,
    workdir: Path,
    params: dict[str, Any],
) -> RepairAction | None:
    """Decode `\\n` / `\\t` / `\\"` escapes back to real characters.

    Heuristic: only run if the file has fewer than 3 real newlines AND at
    least 4 literal `\\n` substrings (same conditions as the EncodingVerifier).
    This prevents the fix from mangling a legitimate one-line shell script
    that just happens to embed a regex with `\\n`.
    """
    if failure.file is None:
        return None
    target = workdir / failure.file
    if not target.exists():
        return None
    try:
        original = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if original.count("\n") >= 3 or original.count("\\n") < 4:
        return None
    try:
        decoded = original.encode("utf-8").decode("unicode_escape")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return None
    if decoded == original:
        return None
    target.write_text(decoded, encoding="utf-8")
    return RepairAction(
        kind="file_rewrite",
        file=failure.file,
        new_content=decoded,
        explanation=f"decoded over-escaped newlines in {failure.file}",
    )


def _action_noop(
    failure: VerifierFailure,
    workdir: Path,
    params: dict[str, Any],
) -> RepairAction | None:
    return RepairAction(
        kind="noop",
        file=failure.file,
        new_content=None,
        explanation=params.get("note", "noted; no auto-fix available"),
    )


_ACTION_HANDLERS = {
    "pip_pin_repair": _action_pip_pin_repair,
    "unicode_unescape": _action_unicode_unescape,
    "noop": _action_noop,
}


def default_yaml_path() -> Path:
    """Location of the bundled failure-patterns YAML."""
    return Path(__file__).with_name("failure_patterns.yaml")


def load_default_registry() -> FailurePatternRegistry:
    """Convenience: load the bundled registry."""
    return FailurePatternRegistry.from_yaml(default_yaml_path())
