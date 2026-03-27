"""Tests for the React frontend CSS and component structure.

Validates that:
- Tool call styles have readable sizing (min-height, overflow)
- History detail overflow is properly constrained
- Session explorer styles exist
- SessionExplorer component exists with required functionality
- HistorySidebar includes View Files integration
"""

from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "src"


def read_css() -> str:
    return (FRONTEND / "index.css").read_text()


def read_component(name: str) -> str:
    return (FRONTEND / "components" / "layout" / name).read_text()


class TestToolCallReadability:
    """Tool call result and header must be readable (not clipped)."""

    def test_tool_call_header_wraps(self):
        css = read_css()
        idx = css.find(".chat-tool-call__header {")
        assert idx != -1
        block = css[idx : idx + 300]
        assert "flex-wrap" in block, "header must wrap long agent/tool names"
        assert "word-break" in block, "header must break long words"
        assert "min-height" in block, "header must have min-height"

    def test_tool_call_result_readable_height(self):
        css = read_css()
        idx = css.find(".chat-tool-result {")
        assert idx != -1
        block = css[idx : idx + 300]
        assert "min-height" in block, "result must have min-height for readability"
        assert "overflow-y" in block, "result must scroll when content overflows"
        assert "line-height" in block, "result must have line-height for spacing"


class TestHistoryOverflow:
    """History detail must not overflow into chat input area."""

    def test_history_detail_flex(self):
        css = read_css()
        idx = css.find(".history-detail {")
        assert idx != -1
        block = css[idx : idx + 200]
        assert "overflow-y" in block, "detail must scroll"
        assert "flex" in block, "detail must use flex sizing"


class TestSessionExplorer:
    """Session explorer component and styles must exist."""

    def test_explorer_css_exists(self):
        css = read_css()
        assert ".session-explorer" in css
        assert ".session-explorer__file" in css
        assert ".session-explorer__preview" in css

    def test_explorer_component_exists(self):
        src = read_component("SessionExplorer.tsx")
        assert "function SessionExplorer" in src
        assert "sessionId" in src

    def test_explorer_has_download(self):
        src = read_component("SessionExplorer.tsx")
        assert "download" in src.lower(), "explorer must support file download"

    def test_explorer_has_file_list(self):
        src = read_component("SessionExplorer.tsx")
        assert "files" in src, "explorer must render file list"

    def test_explorer_has_preview(self):
        src = read_component("SessionExplorer.tsx")
        assert "preview" in src, "explorer must show file preview"


class TestHistoryExplorerIntegration:
    """HistorySidebar must include View Files button linking to explorer."""

    def test_view_files_button(self):
        src = read_component("HistorySidebar.tsx")
        assert "View Files" in src, "history must have View Files button"

    def test_imports_session_explorer(self):
        src = read_component("HistorySidebar.tsx")
        assert "SessionExplorer" in src, "history must import SessionExplorer"

    def test_explorer_session_state(self):
        src = read_component("HistorySidebar.tsx")
        assert "explorerSessionId" in src, "history must track which session to explore"
