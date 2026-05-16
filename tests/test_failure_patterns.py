"""Unit tests for `core.failure_patterns.FailurePatternRegistry` + bundled YAML."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.core.failure_patterns import (
    FailurePattern,
    FailurePatternRegistry,
    default_yaml_path,
    load_default_registry,
)
from agent_orchestrator.core.verification_gate import VerifierFailure


def _failure(category: str, message: str, file: str | None = None) -> VerifierFailure:
    return VerifierFailure(
        verifier="x",
        severity="error",
        category=category,
        message=message,
        file=file,
    )


# ---------------------------- Bundled registry ----------------------------


def test_default_registry_loads():
    registry = load_default_registry()
    assert len(registry.patterns) >= 3
    names = {p.name for p in registry.patterns}
    assert "psycopg_v2_pin_against_v3_only_package" in names
    assert "literal_backslash_n_in_json" in names


def test_default_registry_yaml_exists():
    assert default_yaml_path().exists()


def test_match_psycopg_failure():
    registry = load_default_registry()
    f = _failure(
        category="pypi_resolve",
        message="requirements.txt:3: pin 'psycopg>=2.9,<3' rejects every published release of 'psycopg' (smallest available major is 3)",
        file="requirements.txt",
    )
    p = registry.match(f)
    assert p is not None
    assert p.name == "psycopg_v2_pin_against_v3_only_package"


def test_match_json_escape_failure():
    registry = load_default_registry()
    f = _failure(
        category="json_escape",
        message="package.json: looks like a single-line file with 12 literal '\\n' substrings",
        file="package.json",
    )
    p = registry.match(f)
    assert p is not None
    assert p.name == "literal_backslash_n_in_json"


def test_match_returns_none_for_unknown():
    registry = load_default_registry()
    f = _failure(category="something_else", message="who knows")
    assert registry.match(f) is None


def test_category_mismatch_skips_pattern():
    # The pattern regex matches the message, but the category doesn't — must skip.
    registry = load_default_registry()
    f = _failure(
        category="literal_newline",  # wrong category for the json pattern
        message="package.json: looks like a single-line file with 12 literal '\\n' substrings",
    )
    p = registry.match(f)
    # Should match the source pattern (correct category), not the json one.
    assert p is not None
    assert p.name == "literal_backslash_n_in_source"


# ---------------------------- pip_pin_repair action ----------------------------


@pytest.mark.asyncio
async def test_apply_pip_pin_repair_rewrites_requirements(tmp_path: Path):
    req = tmp_path / "requirements.txt"
    req.write_text("fastapi>=0.109\npsycopg>=2.9,<3\nuvicorn\n")
    registry = load_default_registry()
    f = _failure(
        category="pypi_resolve",
        message="requirements.txt:2: pin 'psycopg>=2.9,<3' rejects every published release of 'psycopg' (smallest available major is 3)",
        file="requirements.txt",
    )
    action = await registry.apply(f, tmp_path)
    assert action is not None
    assert action.kind == "file_rewrite"
    assert action.file == "requirements.txt"
    after = req.read_text()
    assert "psycopg2-binary>=2.9" in after
    assert "fastapi>=0.109" in after  # untouched
    assert "uvicorn" in after  # untouched


@pytest.mark.asyncio
async def test_apply_pip_pin_repair_idempotent(tmp_path: Path):
    req = tmp_path / "requirements.txt"
    req.write_text("psycopg2-binary>=2.9\n")  # already fixed
    registry = load_default_registry()
    f = _failure(
        category="pypi_resolve",
        message="requirements.txt:1: pin 'psycopg>=2.9,<3' rejects every published release of 'psycopg' (smallest available major is 3)",
        file="requirements.txt",
    )
    # Note: failure references psycopg but file doesn't contain it.
    action = await registry.apply(f, tmp_path)
    # No change → action is None (handler returns None when nothing changed).
    assert action is None


@pytest.mark.asyncio
async def test_apply_pip_pin_repair_missing_file(tmp_path: Path):
    registry = load_default_registry()
    f = _failure(
        category="pypi_resolve",
        message="requirements.txt:1: pin 'psycopg>=2.9,<3' rejects every published release of 'psycopg' (smallest available major is 3)",
        file="requirements.txt",  # does not exist
    )
    action = await registry.apply(f, tmp_path)
    assert action is None  # graceful


# ---------------------------- unicode_unescape action ----------------------------


@pytest.mark.asyncio
async def test_apply_unicode_unescape_fixes_package_json(tmp_path: Path):
    bad = (
        r'{\n  "name": "task-tracker-frontend",\n  "version": "1.0.0",\n'
        r'  "scripts": {\n    "dev": "vite",\n    "build": "vite build"\n  },\n'
        r'  "dependencies": {\n    "react": "^19"\n  }\n}'
    )
    pkg = tmp_path / "package.json"
    pkg.write_text(bad)
    registry = load_default_registry()
    f = _failure(
        category="json_escape",
        message=r"package.json: looks like a single-line file with 12 literal '\n' substrings",
        file="package.json",
    )
    action = await registry.apply(f, tmp_path)
    assert action is not None
    assert action.kind == "file_rewrite"
    after = pkg.read_text()
    assert "\n" in after
    import json as _json
    parsed = _json.loads(after)
    assert parsed["name"] == "task-tracker-frontend"


@pytest.mark.asyncio
async def test_apply_unicode_unescape_refuses_when_heuristic_not_met(tmp_path: Path):
    # Real multi-line file with one stray `\n` literal — must NOT be rewritten.
    src = 'def f():\n    s = "hello\\n"\n    return s\n'
    target = tmp_path / "ok.py"
    target.write_text(src)
    before = target.read_text()
    registry = load_default_registry()
    f = _failure(
        category="literal_newline",
        message=r"ok.py: looks like a single-line file with 4 literal '\n' substrings",
        file="ok.py",
    )
    action = await registry.apply(f, tmp_path)
    assert action is None
    assert target.read_text() == before


# ---------------------------- noop action ----------------------------


@pytest.mark.asyncio
async def test_noop_action_returns_action_object(tmp_path: Path):
    yaml_content = """
- name: noted_only
  category: misc
  pattern: "noted_test"
  auto_fix:
    type: noop
    note: "documented but not auto-fixable"
"""
    custom = tmp_path / "patterns.yaml"
    custom.write_text(yaml_content)
    registry = FailurePatternRegistry.from_yaml(custom)
    f = _failure(category="misc", message="noted_test something")
    action = await registry.apply(f, tmp_path)
    assert action is not None
    assert action.kind == "noop"
    assert "documented" in action.explanation


# ---------------------------- YAML loader robustness ----------------------------


def test_from_yaml_rejects_non_list(tmp_path: Path):
    bad = tmp_path / "patterns.yaml"
    bad.write_text("key: value\n")
    with pytest.raises(ValueError, match="expected a YAML list"):
        FailurePatternRegistry.from_yaml(bad)


def test_from_yaml_reports_entry_index_on_error(tmp_path: Path):
    bad = tmp_path / "patterns.yaml"
    bad.write_text(
        """
- name: ok
  category: x
  pattern: "."
  auto_fix:
    type: noop
- name: broken
  category: y
  pattern: "."
  auto_fix:
    type: this_action_does_not_exist
"""
    )
    with pytest.raises(ValueError, match="entry #1 \\(broken\\)"):
        FailurePatternRegistry.from_yaml(bad)


def test_from_yaml_loads_explicit_replacements(tmp_path: Path):
    custom = tmp_path / "p.yaml"
    custom.write_text(
        """
- name: madeup_pin
  category: pypi_resolve
  pattern: "madeup"
  auto_fix:
    type: pip_pin_repair
    replacements:
      madeup: "madeup>=5.0"
"""
    )
    registry = FailurePatternRegistry.from_yaml(custom)
    assert len(registry.patterns) == 1
    p = registry.patterns[0]
    assert p.action_type == "pip_pin_repair"
    assert p.action_params["replacements"] == {"madeup": "madeup>=5.0"}
