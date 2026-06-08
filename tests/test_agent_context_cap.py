"""Tests for the P1 context-side tool-result cap in ``core.agent``.

See ``docs/ago-cli-improvements.md`` (P1 — "Tool outputs are not capped
before re-entering context"). The cap is applied where a tool result is
folded into the conversation, independent of the agent-host transport cap.
"""

from agent_orchestrator.core.agent import cap_tool_result_content


def test_short_text_passes_through_unchanged():
    text = "small output"
    assert cap_tool_result_content(text, 8000) == text


def test_exactly_at_limit_is_unchanged():
    text = "x" * 100
    assert cap_tool_result_content(text, 100) == text


def test_zero_or_negative_limit_disables_cap():
    text = "y" * 50_000
    assert cap_tool_result_content(text, 0) == text
    assert cap_tool_result_content(text, -1) == text


def test_long_text_is_truncated_with_marker():
    text = "A" * 9000 + "B" * 9000  # 18 KB, distinct head/tail
    out = cap_tool_result_content(text, 8000)
    assert len(out) < len(text)
    assert "[truncated" in out
    # Head and tail are both preserved.
    assert out.startswith("A")
    assert out.rstrip().endswith("B")


def test_marker_reports_dropped_count_accurately():
    text = "Z" * 20_000
    limit = 5000
    out = cap_tool_result_content(text, limit)
    # The visible characters + the dropped count must reconstruct the total.
    visible = out.count("Z")
    # Marker format: "…[truncated {n} chars]…"
    import re

    m = re.search(r"truncated (\d+) chars", out)
    assert m is not None
    dropped = int(m.group(1))
    assert visible + dropped == len(text)


def test_capped_output_stays_near_limit():
    text = "Q" * 100_000
    limit = 4000
    out = cap_tool_result_content(text, limit)
    # Never exceeds the requested budget (marker reserve is subtracted up front).
    assert len(out) <= limit
