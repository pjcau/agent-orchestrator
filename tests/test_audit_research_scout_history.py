"""Tests for `scripts/audit_research_scout_history.py`.

The audit driver reuses the scout module's LLM/parse paths (already
covered by the scout/live tests). What's worth pinning here are the
pure functions that decide which entries get re-analysed and how the
findings markdown is shaped — the workflow turns those files into PRs
verbatim, so a regression in the format would land in production.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "audit_research_scout_history.py"


def _load():
    spec = importlib.util.spec_from_file_location("audit_research_scout_history", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    src = str(Path(__file__).parent.parent / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def audit():
    return _load()


class TestIsAuditCandidate:
    def test_includes_no_improvements_entry(self, audit):
        entry = {"summary": "foo/bar", "improvements": []}
        assert audit._is_audit_candidate(entry) is True

    def test_includes_llm_error_entry(self, audit):
        entry = {"summary": "llm-error: HTTP 429", "improvements": []}
        assert audit._is_audit_candidate(entry) is True

    def test_excludes_low_relevance(self, audit):
        """The pre-filter would still reject these — re-running burns
        budget without changing the outcome."""
        entry = {"summary": "low-relevance: foo/bar", "improvements": []}
        assert audit._is_audit_candidate(entry) is False

    def test_excludes_fetch_error(self, audit):
        entry = {"summary": "fetch-error: 404 Not Found", "improvements": []}
        assert audit._is_audit_candidate(entry) is False

    def test_excludes_already_found(self, audit):
        entry = {"summary": "foo/bar", "improvements": ["something good"]}
        assert audit._is_audit_candidate(entry) is False


class TestSafeRepoSlug:
    def test_github_url(self, audit):
        assert audit._safe_repo_slug("https://github.com/foo/bar") == "foo__bar"

    def test_github_url_with_git_suffix(self, audit):
        assert audit._safe_repo_slug("https://github.com/foo/bar.git") == "foo__bar"

    def test_github_url_with_trailing_slash(self, audit):
        assert audit._safe_repo_slug("https://github.com/foo/bar/") == "foo__bar"

    def test_non_github_falls_back_to_safe_chars(self, audit):
        slug = audit._safe_repo_slug("https://example.com/some/path")
        # Must contain no slashes / spaces / special chars.
        assert "/" not in slug
        assert " " not in slug

    def test_underscores_dont_collide_with_double_underscore_separator(self, audit):
        """Even though "owner_with_underscore" produces a single underscore
        and our separator is "__", the slug uses no transformation so the
        github regex pattern is preserved."""
        assert audit._safe_repo_slug("https://github.com/my_org/repo-name") == "my_org__repo-name"


class TestWriteFindingsMd:
    """The markdown is fed verbatim into `gh pr create --body-file`. It
    must include the repo header, totals, and at least one improvement
    section with the expected fields."""

    def test_includes_repo_link_and_count(self, audit):
        items = [
            {
                "component": "router",
                "title": "Adaptive routing",
                "description": "Use history to adjust weights",
                "file": "src/agent_orchestrator/core/router.py",
                "code": "def adaptive(): pass",
                "benefit": "Better routing",
                "impact": 8,
                "effort": 4,
                "risk": 2,
                "value_score": 7.5,
            }
        ]
        md = audit._write_findings_md("https://github.com/foo/bar", "foo/bar", items)
        assert "[foo/bar](https://github.com/foo/bar)" in md
        assert "**1** actionable improvement(s)" in md
        assert "Adaptive routing" in md
        assert "value `7.5/10`" in md
        assert "`src/agent_orchestrator/core/router.py`" in md
        # Scoring line has all three terms.
        assert "impact `8`" in md
        assert "effort `4`" in md
        assert "risk `2`" in md
        # Code fenced as python.
        assert "```python\ndef adaptive(): pass\n```" in md
        # Benefit line present.
        assert "**Benefit:** Better routing" in md

    def test_skips_empty_optional_fields(self, audit):
        """If a model omits `code` or `benefit`, the section should
        gracefully shrink — no empty `Benefit:` line, no empty code fence."""
        items = [
            {
                "component": "skill",
                "title": "Bare proposal",
                "description": "Description",
                # no file, code, benefit
            }
        ]
        md = audit._write_findings_md("https://github.com/x/y", "x/y", items)
        assert "**Benefit:**" not in md
        assert "```python" not in md

    def test_audit_banner_marks_provenance(self, audit):
        """The PR body must make clear these are AUDIT findings (the user
        looking at the PR list needs to know these are backfill, not the
        nightly cron output)."""
        items = [
            {
                "component": "x",
                "title": "T",
                "description": "D",
                "code": "",
                "benefit": "",
            }
        ]
        md = audit._write_findings_md("https://github.com/a/b", "a/b", items)
        assert "audit" in md.lower() or "backfill" in md.lower()
