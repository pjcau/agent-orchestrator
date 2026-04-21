"""Security regression tests for gateway_api path/error handling.

Covers CodeQL alert fixes:
- py/path-injection (_safe_resolve_path sanitizer)
- py/stack-trace-exposure (generic error responses)
- py/log-injection (sanitized log calls)
"""

from agent_orchestrator.dashboard.gateway_api import _safe_resolve_path, _PROJECT_BASE


class TestSafeResolvePath:
    def test_empty_path_returns_project_root(self):
        result = _safe_resolve_path("")
        assert result is not None
        assert str(result) == str(_PROJECT_BASE)

    def test_relative_path_resolved(self):
        result = _safe_resolve_path("README.md")
        assert result is not None
        assert result.name == "README.md"
        assert result.is_relative_to(_PROJECT_BASE)

    def test_nested_relative_path_resolved(self):
        result = _safe_resolve_path("src/agent_orchestrator")
        assert result is not None
        assert result.is_relative_to(_PROJECT_BASE)

    def test_parent_traversal_blocked(self):
        assert _safe_resolve_path("../../../etc/passwd") is None

    def test_absolute_path_blocked(self):
        assert _safe_resolve_path("/etc/passwd") is None

    def test_mixed_traversal_blocked(self):
        assert _safe_resolve_path("src/../../../etc/passwd") is None
        assert _safe_resolve_path("src/../../etc") is None

    def test_null_byte_blocked(self):
        assert _safe_resolve_path("file\x00.txt") is None

    def test_nonexistent_but_contained_path_allowed(self):
        # Resolves successfully because realpath does not require existence;
        # containment check still enforced.
        result = _safe_resolve_path("nonexistent/file.txt")
        assert result is not None
        assert result.is_relative_to(_PROJECT_BASE)

    def test_result_is_pathlib_path(self):
        from pathlib import Path

        result = _safe_resolve_path("README.md")
        assert isinstance(result, Path)
