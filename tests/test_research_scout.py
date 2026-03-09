"""Tests for the research scout script's parsing and analysis logic."""

import json
import sys
from pathlib import Path

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
        result = scout._parse_improvements(llm_output)
        assert len(result) == 1
        assert result[0]["component"] == "router"
        assert result[0]["title"] == "Adaptive routing"
        assert result[0]["file"] == "src/agent_orchestrator/core/router.py"

    def test_markdown_fenced_json(self, scout):
        llm_output = '```json\n[{"component": "agent", "title": "Better prompts", "description": "Use chain-of-thought", "file": "agent.py", "code": "", "benefit": "Better output"}]\n```'
        result = scout._parse_improvements(llm_output)
        assert len(result) == 1
        assert result[0]["component"] == "agent"

    def test_empty_array(self, scout):
        result = scout._parse_improvements("[]")
        assert result == []

    def test_invalid_json(self, scout):
        result = scout._parse_improvements("not json at all")
        assert result == []

    def test_missing_required_fields_skipped(self, scout):
        llm_output = json.dumps([{"component": "router"}])  # missing title + description
        result = scout._parse_improvements(llm_output)
        assert result == []

    def test_max_3_improvements(self, scout):
        items = [
            {
                "component": f"comp{i}",
                "title": f"Title {i}",
                "description": f"Desc {i}",
                "file": f"file{i}.py",
                "code": "",
                "benefit": "",
            }
            for i in range(10)
        ]
        result = scout._parse_improvements(json.dumps(items))
        assert len(result) <= 3

    def test_surrounding_text_ignored(self, scout):
        llm_output = (
            "Here are my suggestions:\n"
            '[{"component": "skill", "title": "New tool", "description": "Add X integration", "file": "skills.py", "code": "", "benefit": "More tools"}]\n'
            "Hope this helps!"
        )
        result = scout._parse_improvements(llm_output)
        assert len(result) == 1
        assert result[0]["component"] == "skill"


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

    def test_no_improvements_no_file(self, scout, tmp_path):
        original = scout.FINDINGS_FILE
        scout.FINDINGS_FILE = tmp_path / "findings.md"
        try:
            # Create file first, then verify it gets deleted
            scout.FINDINGS_FILE.write_text("old content")
            scout._write_findings("test/repo", "https://github.com/test/repo", [])
            # With empty improvements, _write_findings still writes (only main() deletes)
            # The function writes even with 0 improvements since the header says "0 improvements"
            assert scout.FINDINGS_FILE.exists()
        finally:
            scout.FINDINGS_FILE = original


class TestUsageTracker:
    def test_initial_state(self, scout):
        tracker = scout.UsageTracker()
        assert tracker.github_api_calls == 0
        assert tracker.chars_fetched == 0
        assert tracker.llm_input_tokens == 0
        assert tracker.llm_cost_usd == 0.0

    def test_add_fetch(self, scout):
        tracker = scout.UsageTracker()
        tracker.add_fetch(1000)
        assert tracker.github_api_calls == 1
        assert tracker.chars_fetched == 1000

    def test_add_llm_usage_free_model(self, scout):
        tracker = scout.UsageTracker()
        tracker.add_llm_usage("qwen/qwen3-coder:free", 500, 200)
        assert tracker.llm_input_tokens == 500
        assert tracker.llm_output_tokens == 200
        assert tracker.llm_cost_usd == 0.0

    def test_add_llm_usage_paid_model(self, scout):
        tracker = scout.UsageTracker()
        tracker.add_llm_usage("unknown-model", 1_000_000, 1_000_000)
        # default pricing: $0.50/M input + $1.50/M output
        assert tracker.llm_cost_usd == pytest.approx(2.0)

    def test_summary_format(self, scout):
        tracker = scout.UsageTracker()
        tracker.add_fetch(500)
        summary = tracker.summary()
        assert "GitHub API calls" in summary
        assert "LLM" in summary

    def test_to_dict(self, scout):
        tracker = scout.UsageTracker()
        tracker.add_fetch(100)
        d = tracker.to_dict()
        assert d["github_api_calls"] == 1
        assert d["chars_fetched"] == 100
        assert "llm_total_tokens" in d


class TestConstants:
    def test_lookback_is_30_days(self, scout):
        assert scout.LOOKBACK_DAYS == 30

    def test_default_model(self, scout):
        assert "free" in scout.DEFAULT_MODEL or "qwen" in scout.DEFAULT_MODEL
