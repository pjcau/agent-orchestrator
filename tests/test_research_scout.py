"""Tests for the research scout script's parsing and analysis logic."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# The script uses __file__ at import time, so we import specific functions
# by loading the module with a patched __file__
SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "run_research_scout.py"


def _load_scout_module():
    """Load run_research_scout as a module for testing."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_research_scout", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Ensure the src path is available for imports within the script
    src_path = str(Path(__file__).parent.parent / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def scout():
    """Load the research scout module once for all tests."""
    return _load_scout_module()


class TestParseImprovements:
    """`_parse_improvements` returns a `(items, reason)` tuple so callers
    can record *why* the list is the size it is. Each case below checks both
    halves of the tuple — the reason is what shows up in the state file."""

    def test_valid_json_array(self, scout):
        llm_output = json.dumps(
            [
                {
                    "component": "router",
                    "title": "Adaptive routing",
                    "description": "Use history to adjust weights",
                    "file": "src/agent_orchestrator/core/router.py",
                    "code": "def adaptive(): pass",
                    "benefit": "Better routing",
                }
            ]
        )
        items, reason = scout._parse_improvements(llm_output)
        assert len(items) == 1
        assert items[0]["component"] == "router"
        assert items[0]["title"] == "Adaptive routing"
        assert items[0]["file"] == "src/agent_orchestrator/core/router.py"
        assert "1 actionable" in reason

    def test_markdown_fenced_json(self, scout):
        llm_output = (
            "```json\n"
            '[{"component": "agent", "title": "Better prompts", '
            '"description": "Use chain-of-thought", "file": "agent.py", '
            '"code": "", "benefit": "Better output"}]\n'
            "```"
        )
        items, _ = scout._parse_improvements(llm_output)
        assert len(items) == 1
        assert items[0]["component"] == "agent"

    def test_empty_array_explains_why(self, scout):
        items, reason = scout._parse_improvements("[]")
        assert items == []
        assert "empty array" in reason.lower()

    def test_invalid_json_explains_why(self, scout):
        items, reason = scout._parse_improvements("not json at all")
        assert items == []
        assert "no json array" in reason.lower() or "prose" in reason.lower()

    def test_empty_response_explains_why(self, scout):
        items, reason = scout._parse_improvements("")
        assert items == []
        assert "empty" in reason.lower()

    def test_missing_required_fields_explained(self, scout):
        # JSON parses but every item lacks title/description -> all dropped.
        llm_output = json.dumps([{"component": "router"}])
        items, reason = scout._parse_improvements(llm_output)
        assert items == []
        assert "title" in reason.lower() and "description" in reason.lower()

    def test_max_30_improvements(self, scout):
        items_in = [
            {
                "component": f"comp{i}",
                "title": f"Title {i}",
                "description": f"Desc {i}",
                "file": f"file{i}.py",
                "code": "",
                "benefit": "",
                "value_score": 5,
            }
            for i in range(50)
        ]
        items, _ = scout._parse_improvements(json.dumps(items_in))
        assert len(items) == scout.MAX_IMPROVEMENTS == 30

    def test_ranks_by_value_score_desc(self, scout):
        items_in = [
            {"component": "a", "title": "Low value", "description": "desc", "value_score": 2},
            {"component": "b", "title": "High value", "description": "desc", "value_score": 9},
            {"component": "c", "title": "Mid value", "description": "desc", "value_score": 5},
        ]
        items, _ = scout._parse_improvements(json.dumps(items_in))
        assert [imp["title"] for imp in items] == ["High value", "Mid value", "Low value"]

    def test_missing_value_score_derived_from_components(self, scout):
        # impact 9, effort 2, risk 1 -> derived value_score ≈ 9 - 0.6 - 0.5 = 7.9
        items_in = [
            {
                "component": "a",
                "title": "Only impact fields",
                "description": "desc",
                "impact": 9,
                "effort": 2,
                "risk": 1,
            },
        ]
        items, _ = scout._parse_improvements(json.dumps(items_in))
        assert len(items) == 1
        assert 7.5 < items[0]["value_score"] < 8.5


class TestIsTransientLLMError:
    """HTTP 429 / 5xx / network failures must be retryable (URL not marked
    processed), or the nightly scout drops the repo from the queue forever."""

    @pytest.mark.parametrize(
        "msg",
        [
            "OpenRouter API error: HTTP Error 429: Too Many Requests",
            "HTTP Error 502: Bad Gateway",
            "HTTP Error 503: Service Unavailable",
            "Connection reset by peer",
            "Read timed out",
        ],
    )
    def test_recognises_transient(self, scout, msg):
        assert scout._is_transient_llm_error(msg) is True

    @pytest.mark.parametrize(
        "msg",
        [
            "HTTP Error 400: Bad Request",
            "HTTP Error 401: Unauthorized",
            "Prompt too long for context window",
            "",
        ],
    )
    def test_rejects_persistent(self, scout, msg):
        assert scout._is_transient_llm_error(msg) is False


class TestParseImprovementsAdditional:
    """Edge cases for `_parse_improvements` value-score handling and
    surrounding-text tolerance. Kept separate from the main class so the
    transient-error class above stays focused on its own concern."""

    def test_non_numeric_score_falls_back_to_default(self, scout):
        items_in = [
            {
                "component": "a",
                "title": "Bad score",
                "description": "desc",
                "value_score": "not a number",
            },
        ]
        items, _ = scout._parse_improvements(json.dumps(items_in))
        assert len(items) == 1
        # Default value for malformed score is the mid-range default (5.0)
        assert items[0]["value_score"] == 5.0

    def test_score_clamped_to_0_10_range(self, scout):
        items_in = [
            {"component": "a", "title": "Over", "description": "d", "value_score": 999},
            {"component": "b", "title": "Under", "description": "d", "value_score": -5},
        ]
        items, _ = scout._parse_improvements(json.dumps(items_in))
        scores = {imp["title"]: imp["value_score"] for imp in items}
        assert scores["Over"] == 10.0
        assert scores["Under"] == 0.0

    def test_surrounding_text_ignored(self, scout):
        llm_output = (
            "Here are my suggestions:\n"
            '[{"component": "skill", "title": "New tool", '
            '"description": "Add X integration", "file": "skills.py", '
            '"code": "", "benefit": "More tools"}]\n'
            "Hope this helps!"
        )
        items, _ = scout._parse_improvements(llm_output)
        assert len(items) == 1
        assert items[0]["component"] == "skill"


class TestWriteFindings:
    def test_writes_markdown_file(self, scout, tmp_path):
        original = scout.FINDINGS_FILE
        scout.FINDINGS_FILE = tmp_path / "findings.md"
        try:
            improvements = [
                {
                    "component": "router",
                    "title": "Adaptive routing",
                    "description": "Use history to adjust weights",
                    "file": "src/agent_orchestrator/core/router.py",
                    "code": "def adaptive(): pass",
                    "benefit": "Better routing",
                }
            ]
            scout._write_findings("test/repo", "https://github.com/test/repo", improvements)
            content = scout.FINDINGS_FILE.read_text()
            assert "Adaptive routing" in content
            assert "router" in content
            assert "test/repo" in content
            assert "def adaptive(): pass" in content
        finally:
            scout.FINDINGS_FILE = original

    def test_empty_improvements_still_writes(self, scout, tmp_path):
        original = scout.FINDINGS_FILE
        scout.FINDINGS_FILE = tmp_path / "findings.md"
        try:
            scout._write_findings("test/repo", "https://github.com/test/repo", [])
            assert scout.FINDINGS_FILE.exists()
            content = scout.FINDINGS_FILE.read_text()
            assert "0" in content
        finally:
            scout.FINDINGS_FILE = original


class TestCallClaude:
    def test_success(self, scout):
        mock_result = type(
            "Result", (), {"returncode": 0, "stdout": '[{"title": "test"}]', "stderr": ""}
        )()
        with patch.object(scout.subprocess, "run", return_value=mock_result):
            result = scout._call_claude("test prompt")
        assert "content" in result
        assert result["content"] == '[{"title": "test"}]'

    def test_cli_failure(self, scout):
        mock_result = type("Result", (), {"returncode": 1, "stdout": "", "stderr": "error"})()
        with patch.object(scout.subprocess, "run", return_value=mock_result):
            result = scout._call_claude("test prompt")
        assert "error" in result

    def test_empty_output(self, scout):
        mock_result = type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        with patch.object(scout.subprocess, "run", return_value=mock_result):
            result = scout._call_claude("test prompt")
        assert "error" in result
        assert "empty" in result["error"]

    def test_timeout(self, scout):
        import subprocess as sp

        with patch.object(scout.subprocess, "run", side_effect=sp.TimeoutExpired("claude", 120)):
            result = scout._call_claude("test prompt")
        assert "error" in result
        assert "timed out" in result["error"]

    def test_not_found(self, scout):
        with patch.object(scout.subprocess, "run", side_effect=FileNotFoundError()):
            result = scout._call_claude("test prompt")
        assert "error" in result
        assert "not found" in result["error"]


class TestCallLlm:
    def test_local_uses_claude(self, scout):
        """Without CI env var, _call_llm should use claude CLI."""
        mock_result = type("Result", (), {"returncode": 0, "stdout": "[]", "stderr": ""})()
        with (
            patch.object(scout.subprocess, "run", return_value=mock_result),
            patch.dict(scout.os.environ, {"CI": "", "OPENROUTER_API_KEY": ""}, clear=False),
        ):
            result = scout._call_llm("test")
        assert "content" in result

    def test_ci_uses_openrouter(self, scout):
        """With CI + OPENROUTER_API_KEY, _call_llm should use OpenRouter."""
        with (
            patch.dict(
                scout.os.environ, {"CI": "true", "OPENROUTER_API_KEY": "sk-test"}, clear=False
            ),
            patch.object(scout, "_call_openrouter", return_value={"content": "[]"}) as mock_or,
        ):
            result = scout._call_llm("test")
        assert "content" in result
        mock_or.assert_called_once_with("test")


class TestCreatePr:
    def test_success(self, scout, tmp_path):
        findings = tmp_path / "findings.md"
        findings.write_text("## Test findings\nSome content")

        calls = []

        def mock_run(cmd, **_kwargs):
            calls.append(cmd)
            stdout = ""
            if cmd[0] == "gh":
                stdout = "https://github.com/test/repo/pull/1"
            return type("Result", (), {"returncode": 0, "stdout": stdout, "stderr": ""})()

        with patch.object(scout.subprocess, "run", side_effect=mock_run):
            result = scout._create_pr(findings)

        assert result is True
        # Should have called: checkout -b, add, commit, push, gh pr create, checkout main
        assert len(calls) == 6
        assert calls[0][1] == "checkout"
        assert calls[4][0] == "gh"
        # Verify --body-file is used instead of --body
        assert "--body-file" in calls[4]

    def test_failure_returns_false(self, scout, tmp_path):
        findings = tmp_path / "findings.md"
        findings.write_text("## Test findings")

        import subprocess as sp

        def mock_run(cmd, **_kwargs):
            if cmd[0] == "gh":
                raise sp.CalledProcessError(1, "gh", stderr="gh not found")
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with patch.object(scout.subprocess, "run", side_effect=mock_run):
            result = scout._create_pr(findings)

        assert result is False


class TestConstants:
    def test_lookback_is_30_days(self, scout):
        assert scout.LOOKBACK_DAYS == 30

    def test_only_github_urls_supported(self, scout):
        result = scout._fetch_url("https://example.com/not-github")
        assert "error" in result

    def test_openrouter_model(self, scout):
        assert "qwen" in scout.OPENROUTER_MODEL
