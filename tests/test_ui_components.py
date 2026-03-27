"""Tests for the DeepFlow UI components added to the dashboard.

These tests validate that:
- The HTML file contains the required CDN links for Mermaid and KaTeX
- The JavaScript file contains the expected function names and logic
- Thinking/reasoning tags are detected correctly
- Mermaid code-block detection works as expected
- KaTeX delimiter detection works as expected
- Task plan event types are correctly defined
- HITL event types are correctly handled
"""

import re
from pathlib import Path

STATIC_DIR = Path(__file__).parent.parent / "src" / "agent_orchestrator" / "dashboard" / "static"
HTML_FILE = STATIC_DIR / "index.html"
JS_FILE = STATIC_DIR / "app.js"
CSS_FILE = STATIC_DIR / "style.css"


def read_html() -> str:
    return HTML_FILE.read_text(encoding="utf-8")


def read_js() -> str:
    return JS_FILE.read_text(encoding="utf-8")


def read_css() -> str:
    return CSS_FILE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# HTML CDN link checks
# ---------------------------------------------------------------------------


class TestHtmlCdnLinks:
    def test_mermaid_cdn_script(self):
        """index.html must include the Mermaid CDN script tag."""
        html = read_html()
        assert "cdn.jsdelivr.net/npm/mermaid" in html, "Mermaid CDN script not found in index.html"

    def test_katex_css_cdn(self):
        """index.html must include the KaTeX CSS CDN link."""
        html = read_html()
        assert "cdn.jsdelivr.net/npm/katex" in html and "katex.min.css" in html, (
            "KaTeX CSS CDN not found in index.html"
        )

    def test_katex_js_cdn(self):
        """index.html must include the KaTeX JS CDN script tag."""
        html = read_html()
        assert "katex.min.js" in html, "KaTeX JS CDN not found in index.html"

    def test_katex_auto_render_cdn(self):
        """index.html must include the KaTeX auto-render contrib script."""
        html = read_html()
        assert "auto-render.min.js" in html, "KaTeX auto-render script not found in index.html"

    def test_task_plan_section_exists(self):
        """index.html must contain the Task Plan sidebar section."""
        html = read_html()
        assert 'id="task-plan-list"' in html, (
            "Task Plan panel (task-plan-list) not found in index.html"
        )

    def test_sse_toggle_button_exists(self):
        """index.html must contain the SSE toggle button."""
        html = read_html()
        assert 'id="btn-sse-toggle"' in html, "SSE toggle button not found in index.html"

    def test_sse_indicator_exists(self):
        """index.html must contain the SSE indicator dot."""
        html = read_html()
        assert 'id="sse-indicator"' in html, "SSE indicator not found in index.html"


# ---------------------------------------------------------------------------
# JavaScript function checks
# ---------------------------------------------------------------------------


class TestJavaScriptFunctions:
    def test_mermaid_initialize(self):
        """app.js must call mermaid.initialize with startOnLoad: false."""
        js = read_js()
        assert "mermaid.initialize" in js, "mermaid.initialize not found in app.js"
        assert "startOnLoad" in js, "startOnLoad not found in mermaid.initialize call"

    def test_run_mermaid_on_function(self):
        """app.js must define runMermaidOn function."""
        js = read_js()
        assert "function runMermaidOn" in js, "runMermaidOn function not found in app.js"

    def test_run_katex_on_function(self):
        """app.js must define runKatexOn function."""
        js = read_js()
        assert "function runKatexOn" in js, "runKatexOn function not found in app.js"

    def test_render_math_in_element_call(self):
        """app.js must call renderMathInElement for KaTeX rendering."""
        js = read_js()
        assert "renderMathInElement" in js, "renderMathInElement not found in app.js"

    def test_katex_delimiters(self):
        """app.js must configure KaTeX with both display and inline delimiters."""
        js = read_js()
        assert "$$" in js and "display: true" in js, (
            "KaTeX display delimiter ($$) not configured in app.js"
        )
        assert "display: false" in js, "KaTeX inline delimiter ($) not configured in app.js"

    def test_mermaid_div_class_in_markdown(self):
        """app.js renderMarkdown must produce a div with class 'mermaid'."""
        js = read_js()
        assert (
            'class="mermaid"' in js or 'class=\\"mermaid\\"' in js or 'class=\\"mermaid\\"' in js
        ), "Mermaid div not emitted by renderMarkdown in app.js"

    def test_extract_thinking_blocks_function(self):
        """app.js must define extractThinkingBlocks function."""
        js = read_js()
        assert "function extractThinkingBlocks" in js, (
            "extractThinkingBlocks function not found in app.js"
        )

    def test_thinking_tag_regex(self):
        """app.js must have a regex that matches thinking/reasoning tags."""
        js = read_js()
        assert "thinking" in js and "reasoning" in js, (
            "thinking/reasoning tag handling not found in app.js"
        )

    def test_thinking_accordion_function(self):
        """app.js must define renderThinkingAccordion function."""
        js = read_js()
        assert "function renderThinkingAccordion" in js, (
            "renderThinkingAccordion function not found in app.js"
        )

    def test_thinking_details_element(self):
        """app.js must render thinking blocks as <details> elements."""
        js = read_js()
        assert "thinking-accordion" in js, "thinking-accordion class not found in app.js"
        assert "<details" in js or "details" in js, (
            "<details> element not found in thinking accordion"
        )

    def test_stream_buffer_variable(self):
        """app.js must use a streamBuffer for progressive streaming."""
        js = read_js()
        assert "streamBuffer" in js, "streamBuffer variable not found in app.js"

    def test_stream_buffer_accumulates(self):
        """app.js appendToStream must accumulate chunks in streamBuffer."""
        js = read_js()
        # streamBuffer += text indicates buffering
        assert "streamBuffer +=" in js, (
            "streamBuffer not being accumulated (streamBuffer +=) in appendToStream"
        )

    def test_task_plan_functions(self):
        """app.js must define task plan management functions."""
        js = read_js()
        assert "function clearTaskPlan" in js, "clearTaskPlan not found in app.js"
        assert "function upsertTaskPlanItem" in js, "upsertTaskPlanItem not found in app.js"
        assert "function renderTaskPlan" in js, "renderTaskPlan not found in app.js"

    def test_task_plan_status_icons(self):
        """app.js must define status icons for task plan items."""
        js = read_js()
        assert "TASK_STATUS_ICONS" in js, "TASK_STATUS_ICONS not found in app.js"
        assert "in_progress" in js, "in_progress status not found in TASK_STATUS_ICONS"
        assert "completed" in js, "completed status not found in TASK_STATUS_ICONS"
        assert "failed" in js, "failed status not found in TASK_STATUS_ICONS"

    def test_graph_start_clears_plan(self):
        """app.js graph.start event handler must call clearTaskPlan."""
        js = read_js()
        # Find the graph.start block
        idx = js.find('"graph.start"')
        assert idx != -1, "graph.start handler not found in app.js"
        # clearTaskPlan should appear near the graph.start handler
        context = js[idx : idx + 500]
        assert "clearTaskPlan" in context, "clearTaskPlan not called in graph.start handler"

    def test_graph_node_enter_updates_plan(self):
        """app.js graph.node.enter event must call upsertTaskPlanItem with in_progress."""
        js = read_js()
        idx = js.find('"graph.node.enter"')
        assert idx != -1, "graph.node.enter handler not found"
        context = js[idx : idx + 400]
        assert "upsertTaskPlanItem" in context, (
            "upsertTaskPlanItem not called in graph.node.enter handler"
        )

    def test_hitl_functions(self):
        """app.js must define HITL handling functions."""
        js = read_js()
        assert "function renderHitlButtons" in js, "renderHitlButtons not found in app.js"
        assert "function sendHitlResponse" in js, "sendHitlResponse not found in app.js"

    def test_hitl_clarification_handled(self):
        """app.js must handle clarification.request events."""
        js = read_js()
        assert "clarification.request" in js, "clarification.request event type not found in app.js"

    def test_hitl_interrupt_handled(self):
        """app.js must handle interrupt events."""
        js = read_js()
        assert '"interrupt"' in js, "interrupt event type not found in app.js"

    def test_hitl_approve_reject_buttons(self):
        """app.js must render Approve/Reject buttons for interrupt events."""
        js = read_js()
        assert "Approve" in js, "Approve button text not found in app.js"
        assert "Reject" in js, "Reject button text not found in app.js"

    def test_sse_functions(self):
        """app.js must define SSE connection functions."""
        js = read_js()
        assert "function connectSSE" in js, "connectSSE not found in app.js"
        assert "function disconnectSSE" in js, "disconnectSSE not found in app.js"
        assert "function toggleSseMode" in js, "toggleSseMode not found in app.js"

    def test_sse_uses_event_source(self):
        """app.js connectSSE must use EventSource."""
        js = read_js()
        assert "new EventSource" in js, "EventSource not used in app.js"

    def test_sse_feeds_handle_event(self):
        """app.js SSE handler must call handleEvent for event messages."""
        js = read_js()
        # The SSE handler calls handleEvent
        idx = js.find("sseSource.onmessage")
        assert idx != -1, "sseSource.onmessage handler not found"
        context = js[idx : idx + 400]
        assert "handleEvent" in context, "handleEvent not called in SSE message handler"

    def test_post_render_bubble(self):
        """app.js must call postRenderBubble after adding messages."""
        js = read_js()
        assert "function postRenderBubble" in js, "postRenderBubble not found in app.js"
        assert "postRenderBubble(bubble)" in js, "postRenderBubble not called on bubble in app.js"

    def test_open_explorer_for_session_function(self):
        """app.js must define openExplorerForSession for history→explorer navigation."""
        js = read_js()
        assert "function openExplorerForSession" in js, (
            "openExplorerForSession function not found in app.js"
        )

    def test_history_view_files_button(self):
        """app.js history detail must include a View Files button."""
        js = read_js()
        assert "btn-history-files" in js, "btn-history-files class not found in app.js"
        assert "View Files" in js, "View Files button text not found in app.js"


# ---------------------------------------------------------------------------
# CSS class checks
# ---------------------------------------------------------------------------


class TestCssClasses:
    def test_thinking_accordion_styles(self):
        """style.css must define .thinking-accordion styles."""
        css = read_css()
        assert ".thinking-accordion" in css, ".thinking-accordion not found in style.css"

    def test_thinking_content_styles(self):
        """style.css must define .thinking-content styles."""
        css = read_css()
        assert ".thinking-content" in css, ".thinking-content not found in style.css"

    def test_mermaid_styles(self):
        """style.css must define .mermaid styles."""
        css = read_css()
        assert ".mermaid" in css, ".mermaid not found in style.css"

    def test_task_plan_styles(self):
        """style.css must define task-plan-* styles."""
        css = read_css()
        assert ".task-plan-list" in css, ".task-plan-list not found in style.css"
        assert ".task-plan-item" in css, ".task-plan-item not found in style.css"

    def test_hitl_styles(self):
        """style.css must define hitl-* styles."""
        css = read_css()
        assert ".hitl-option-btn" in css, ".hitl-option-btn not found in style.css"
        assert ".hitl-interrupt" in css, ".hitl-interrupt not found in style.css"
        assert ".hitl-approve-btn" in css, ".hitl-approve-btn not found in style.css"

    def test_sse_styles(self):
        """style.css must define sse-* styles."""
        css = read_css()
        assert ".sse-dot" in css, ".sse-dot not found in style.css"

    def test_tool_call_result_readable_height(self):
        """style.css tool-call-result must have min-height for readability."""
        css = read_css()
        # Find the .tool-call-result block
        idx = css.find(".tool-call-result {")
        assert idx != -1, ".tool-call-result not found in style.css"
        block = css[idx : idx + 300]
        assert "min-height" in block, ".tool-call-result must have min-height for readability"
        assert "overflow-y" in block, (
            ".tool-call-result must have overflow-y for scrollable content"
        )

    def test_tool_call_header_wraps(self):
        """style.css tool-call-header must wrap long content."""
        css = read_css()
        idx = css.find(".tool-call-header {")
        assert idx != -1, ".tool-call-header not found in style.css"
        block = css[idx : idx + 300]
        assert "flex-wrap" in block, (
            ".tool-call-header must have flex-wrap for long agent/tool names"
        )
        assert "word-break" in block, ".tool-call-header must have word-break"

    def test_history_modal_overflow_contained(self):
        """style.css history layout must constrain overflow."""
        css = read_css()
        idx = css.find(".modal-wide {")
        assert idx != -1, ".modal-wide not found in style.css"
        block = css[idx : idx + 200]
        assert "overflow" in block, ".modal-wide must have overflow control"

    def test_btn_history_files_style(self):
        """style.css must define .btn-history-files for View Files button."""
        css = read_css()
        assert ".btn-history-files" in css, ".btn-history-files not found in style.css"


# ---------------------------------------------------------------------------
# Logic unit tests (Python-side simulations of the JS logic)
# ---------------------------------------------------------------------------


class TestThinkingTagExtraction:
    """Simulate the extractThinkingBlocks logic in Python for unit testing."""

    THINKING_PATTERN = re.compile(r"<(thinking|reasoning)>(.*?)</\1>", re.IGNORECASE | re.DOTALL)

    def extract(self, text):
        blocks = []
        clean = self.THINKING_PATTERN.sub(lambda m: blocks.append(m.group(2).strip()) or "", text)
        return clean.strip(), blocks

    def test_single_thinking_block(self):
        text = "Hello <thinking>This is my thought</thinking> world"
        clean, blocks = self.extract(text)
        assert "This is my thought" in blocks
        assert "<thinking>" not in clean

    def test_reasoning_tag(self):
        text = "<reasoning>Step 1: analyze</reasoning>\nFinal answer"
        clean, blocks = self.extract(text)
        assert "Step 1: analyze" in blocks
        assert "Final answer" in clean

    def test_no_thinking_block(self):
        text = "Regular text without any tags"
        clean, blocks = self.extract(text)
        assert blocks == []
        assert clean == text

    def test_multiple_blocks(self):
        text = "<thinking>First thought</thinking> middle <thinking>Second thought</thinking>"
        clean, blocks = self.extract(text)
        assert len(blocks) == 2
        assert "First thought" in blocks
        assert "Second thought" in blocks

    def test_multiline_thinking(self):
        text = "<thinking>\nLine 1\nLine 2\n</thinking>\nAnswer"
        clean, blocks = self.extract(text)
        assert "Line 1" in blocks[0]
        assert "Line 2" in blocks[0]
        assert "Answer" in clean


class TestMermaidDetection:
    """Simulate mermaid block detection logic."""

    MERMAID_PATTERN = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)

    def has_mermaid(self, text):
        return bool(self.MERMAID_PATTERN.search(text))

    def extract_mermaid(self, text):
        return self.MERMAID_PATTERN.findall(text)

    def test_detects_mermaid_block(self):
        text = "Here is a diagram:\n```mermaid\ngraph TD\nA-->B\n```"
        assert self.has_mermaid(text)

    def test_extracts_mermaid_code(self):
        text = "```mermaid\ngraph LR\nA-->B\nB-->C\n```"
        blocks = self.extract_mermaid(text)
        assert len(blocks) == 1
        assert "graph LR" in blocks[0]

    def test_no_mermaid_in_regular_code(self):
        text = "```python\nprint('hello')\n```"
        assert not self.has_mermaid(text)

    def test_non_mermaid_not_detected(self):
        text = "Some regular text without code blocks"
        assert not self.has_mermaid(text)


class TestKatexDelimiterDetection:
    """Simulate KaTeX delimiter detection."""

    DISPLAY_PATTERN = re.compile(r"\$\$(.*?)\$\$", re.DOTALL)
    INLINE_PATTERN = re.compile(r"(?<!\$)\$(?!\$)(.*?)(?<!\$)\$(?!\$)")

    def has_display_math(self, text):
        return bool(self.DISPLAY_PATTERN.search(text))

    def has_inline_math(self, text):
        return bool(self.INLINE_PATTERN.search(text))

    def test_display_math_detected(self):
        text = "The formula is $$E = mc^2$$ for mass-energy."
        assert self.has_display_math(text)

    def test_inline_math_detected(self):
        text = "When $x = 5$, the result is known."
        assert self.has_inline_math(text)

    def test_no_math(self):
        text = "Just plain text without any math."
        assert not self.has_display_math(text)
        assert not self.has_inline_math(text)

    def test_display_math_content(self):
        text = "$$\\frac{a}{b} = c$$"
        blocks = self.DISPLAY_PATTERN.findall(text)
        assert len(blocks) == 1
        assert "\\frac" in blocks[0]


class TestTaskPlanEvents:
    """Simulate task plan event tracking logic."""

    def setup_method(self):
        self.plan = []

    def upsert(self, node_id, status, started_at=None):
        existing = next((i for i in self.plan if i["nodeId"] == node_id), None)
        if existing:
            existing["status"] = status
        else:
            self.plan.append({"nodeId": node_id, "status": status, "startedAt": started_at})

    def clear(self):
        self.plan = []

    def test_node_enters_as_in_progress(self):
        self.upsert("agent_node", "in_progress")
        item = next(i for i in self.plan if i["nodeId"] == "agent_node")
        assert item["status"] == "in_progress"

    def test_node_exits_as_completed(self):
        self.upsert("agent_node", "in_progress")
        self.upsert("agent_node", "completed")
        assert len(self.plan) == 1
        assert self.plan[0]["status"] == "completed"

    def test_node_error_marks_failed(self):
        self.upsert("agent_node", "in_progress")
        self.upsert("agent_node", "failed")
        assert self.plan[0]["status"] == "failed"

    def test_graph_start_clears_plan(self):
        self.upsert("node_a", "completed")
        self.upsert("node_b", "in_progress")
        self.clear()
        assert self.plan == []

    def test_multiple_nodes_tracked(self):
        self.upsert("node_a", "in_progress")
        self.upsert("node_b", "in_progress")
        self.upsert("node_a", "completed")
        assert len(self.plan) == 2
        assert next(i for i in self.plan if i["nodeId"] == "node_a")["status"] == "completed"
        assert next(i for i in self.plan if i["nodeId"] == "node_b")["status"] == "in_progress"

    def test_status_icons_coverage(self):
        """Verify all expected statuses have icon mappings defined in JS."""
        js = JS_FILE.read_text(encoding="utf-8")
        for status in ("pending", "in_progress", "completed", "failed"):
            assert status in js, f"Status '{status}' not found in TASK_STATUS_ICONS"
