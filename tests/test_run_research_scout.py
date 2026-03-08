"""Tests for run_research_scout — content analysis, findings generation, GitHub fetching."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

# Add scripts to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from run_research_scout import _fetch_url, _write_findings, analyze_content, _GH_REPO_RE


class TestGitHubRepoRegex:
    def test_matches_standard_url(self):
        match = _GH_REPO_RE.search("https://github.com/owner/repo")
        assert match
        assert match.group(1) == "owner"
        assert match.group(2) == "repo"

    def test_matches_url_with_trailing_slash(self):
        match = _GH_REPO_RE.search("https://github.com/owner/repo/")
        assert match
        assert match.group(2) == "repo"

    def test_no_match_for_non_github(self):
        match = _GH_REPO_RE.search("https://gitlab.com/owner/repo")
        assert match is None


class TestAnalyzeContent:
    def test_finds_agent_keywords(self):
        text = "This agent uses multi-agent coordination and orchestration patterns."
        result = analyze_content(text, "https://example.com")
        components = [imp["component"] for imp in result["improvements"]]
        assert "agents" in components

    def test_finds_tools_keywords(self):
        text = "The API integration with the SDK provides a CLI plugin for MCP extensions."
        result = analyze_content(text, "https://example.com")
        components = [imp["component"] for imp in result["improvements"]]
        assert "tools" in components

    def test_no_improvements_for_unrelated_content(self):
        text = "This is a recipe for chocolate cake with eggs and flour."
        result = analyze_content(text, "https://example.com")
        assert len(result["improvements"]) == 0

    def test_max_five_improvements(self):
        text = (
            "agent multi-agent coordination orchestration delegation role "
            "memory state persistence context session cache store "
            "routing router dispatch load balance strategy cost optimize "
            "skill tool capability workflow pipeline chain "
            "api integration sdk cli plugin extension mcp"
        )
        result = analyze_content(text, "https://example.com")
        assert len(result["improvements"]) <= 5

    def test_relevance_sorting(self):
        text = (
            "api integration sdk cli plugin extension mcp "
            "routing cost"
        )
        result = analyze_content(text, "https://example.com")
        if len(result["improvements"]) >= 2:
            assert result["improvements"][0]["relevance"] >= result["improvements"][1]["relevance"]


class TestWriteFindings:
    def test_creates_file_when_findings_exist(self, tmp_path: Path):
        findings_file = tmp_path / "findings.md"
        findings = [
            {
                "url": "https://github.com/owner/repo",
                "title": "owner/repo",
                "improvements": [
                    {"component": "tools", "keywords_found": ["api", "sdk"]},
                ],
            }
        ]
        with patch("run_research_scout.FINDINGS_FILE", findings_file):
            _write_findings(findings)
        assert findings_file.exists()
        content = findings_file.read_text()
        assert "owner/repo" in content
        assert "tools" in content

    def test_removes_file_when_no_findings(self, tmp_path: Path):
        findings_file = tmp_path / "findings.md"
        findings_file.write_text("old findings")
        with patch("run_research_scout.FINDINGS_FILE", findings_file):
            _write_findings([])
        assert not findings_file.exists()

    def test_no_error_when_file_doesnt_exist(self, tmp_path: Path):
        findings_file = tmp_path / "nonexistent.md"
        with patch("run_research_scout.FINDINGS_FILE", findings_file):
            _write_findings([])  # Should not raise


class TestFetchUrl:
    def test_github_url_uses_api(self):
        mock_repo = json.dumps({
            "description": "Test repo",
            "topics": ["ai"],
            "language": "Python",
            "stargazers_count": 50,
        }).encode()
        mock_readme = b"# README\nThis is a test project."

        from unittest.mock import MagicMock
        from contextlib import contextmanager

        call_count = 0

        @contextmanager
        def mock_urlopen(req, timeout=None):
            nonlocal call_count
            m = MagicMock()
            if call_count == 0:
                m.read.return_value = mock_repo
            else:
                m.read.return_value = mock_readme
            call_count += 1
            yield m

        with patch("run_research_scout.urlopen", side_effect=mock_urlopen):
            result = _fetch_url("https://github.com/owner/testrepo")

        assert "error" not in result
        assert result["title"] == "owner/testrepo"
        assert "README" in result["text"]

    def test_non_github_url_without_aiohttp(self):
        with patch.dict("sys.modules", {"aiohttp": None}):
            result = _fetch_url("https://example.com/article")
        # Should gracefully handle missing aiohttp
        assert "error" in result or "text" in result
