"""Tests for `scripts/explain_research_scout_history.py`.

These cover the legacy/new outcome classification merge and the markdown
output shape used by the workflow step summary.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "explain_research_scout_history.py"


def _load() -> object:
    spec = importlib.util.spec_from_file_location("explain_research_scout_history", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    src = str(Path(__file__).parent.parent / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def explainer():
    return _load()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_report_with_modern_outcome_fields(explainer):
    """Entries written by the updated scout carry an `outcome` field — the
    report must pick it up verbatim instead of re-parsing the summary."""
    now = datetime.now(timezone.utc)
    state = {
        "processed": {
            "https://github.com/foo/bar": {
                "processed_at": _iso(now - timedelta(days=1)),
                "summary": "foo/bar",
                "improvements": [],
                "outcome": "no-improvements",
                "reason": "LLM returned an empty array — nothing actionable",
            },
            "https://github.com/baz/qux": {
                "processed_at": _iso(now - timedelta(days=2)),
                "summary": "llm-error: HTTP 429",
                "improvements": [],
                "outcome": "llm-error",
                "reason": "OpenRouter HTTP 429",
            },
        }
    }
    report = explainer.build_report(state, days=7)
    assert "Total runs analysed:** 2" in report
    assert "LLM ran, no actionable items" in report
    assert "LLM call failed" in report
    assert "foo/bar" in report
    assert "baz/qux" in report
    # The reason column must surface the explicit reason text.
    assert "nothing actionable" in report
    assert "OpenRouter HTTP 429" in report


def test_report_with_legacy_entries_falls_back_to_summary_prefix(explainer):
    """Legacy entries (no outcome field) must still be classified — that's
    the whole point of `classify_legacy_outcome` being applied at render
    time. Without this, the historical 2+ weeks of state would all collapse
    into one "unknown" bucket."""
    now = datetime.now(timezone.utc)
    state = {
        "processed": {
            "https://github.com/a/low": {
                "processed_at": _iso(now - timedelta(days=1)),
                "summary": "low-relevance: a/low",
                "improvements": [],
            },
            "https://github.com/a/err": {
                "processed_at": _iso(now - timedelta(days=2)),
                "summary": "llm-error: HTTP 429",
                "improvements": [],
            },
            "https://github.com/a/empty": {
                "processed_at": _iso(now - timedelta(days=3)),
                "summary": "a/empty",
                "improvements": [],
            },
            "https://github.com/a/found": {
                "processed_at": _iso(now - timedelta(days=4)),
                "summary": "a/found",
                "improvements": ["did a thing"],
            },
        }
    }
    report = explainer.build_report(state, days=7)
    assert "Skipped by keyword pre-filter" in report
    assert "LLM call failed" in report
    assert "LLM ran, no actionable items" in report
    assert "Improvements found" in report


def test_days_window_drops_old_entries(explainer):
    now = datetime.now(timezone.utc)
    state = {
        "processed": {
            "https://github.com/recent/x": {
                "processed_at": _iso(now - timedelta(days=2)),
                "summary": "recent/x",
                "improvements": [],
            },
            "https://github.com/old/x": {
                "processed_at": _iso(now - timedelta(days=40)),
                "summary": "old/x",
                "improvements": [],
            },
        }
    }
    report = explainer.build_report(state, days=14)
    assert "recent/x" in report
    assert "old/x" not in report


def test_empty_state_renders_friendly_placeholder(explainer):
    report = explainer.build_report({"processed": {}}, days=14)
    assert "No entries" in report


def test_pipe_in_reason_is_escaped_for_markdown_table(explainer):
    """A pipe in the reason would break the markdown table column count.
    We backslash-escape it so the table still renders."""
    now = datetime.now(timezone.utc)
    state = {
        "processed": {
            "https://github.com/a/x": {
                "processed_at": _iso(now - timedelta(days=1)),
                "summary": "x",
                "improvements": [],
                "outcome": "no-improvements",
                "reason": "weird | reason with | pipes",
            },
        }
    }
    report = explainer.build_report(state, days=14)
    assert "weird \\| reason with \\| pipes" in report


def test_handles_missing_processed_at(explainer):
    """Malformed entries (missing or unparseable date) must be skipped
    silently, not crash the workflow step."""
    state = {
        "processed": {
            "https://x.test": {
                "summary": "broken",
                "improvements": [],
            },
        }
    }
    # Should not raise.
    report = explainer.build_report(state, days=14)
    assert "No entries" in report or "Total runs analysed:** 0" in report


def test_real_state_file_is_renderable(explainer, tmp_path):
    """Smoke test against the actual state file shape on disk."""
    real_state = Path(".claude/research-scout-state.json")
    if not real_state.exists():
        pytest.skip("Real state file not present (CI clean clone).")
    state = json.loads(real_state.read_text())
    report = explainer.build_report(state, days=30)
    assert "Research Scout — outcomes" in report
