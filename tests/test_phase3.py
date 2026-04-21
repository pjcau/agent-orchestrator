"""Tests for Phase 3: modality detection (PR #88) and hybrid graph
execution with store preload (PR #84)."""

import pytest

from agent_orchestrator.core.graph import StateGraph, START, END
from agent_orchestrator.core.metrics import MetricsRegistry
from agent_orchestrator.core.modality import (
    Modality,
    detect_modality,
    record_detection,
)
from agent_orchestrator.core.store import InMemoryStore


# ═══════════════════════════════════════════════════════════════════════
# PR #88 — Modality detection
# ═══════════════════════════════════════════════════════════════════════


class TestDetectModality:
    def test_plain_text(self):
        assert detect_modality("What is the capital of France?") == Modality.TEXT

    def test_short_utterance_is_text(self):
        assert detect_modality("hello world") == Modality.TEXT

    def test_fenced_code_block_detected(self):
        src = "```python\ndef f(x):\n    return x * 2\n```"
        assert detect_modality(src) == Modality.CODE

    def test_function_definition_detected(self):
        src = "def greet(name):\n    return f'hi {name}'"
        assert detect_modality(src) == Modality.CODE

    def test_java_snippet_detected(self):
        src = "public class Main { public static void main() {} }"
        assert detect_modality(src) == Modality.CODE

    def test_single_code_keyword_not_enough(self):
        # Natural sentence that mentions code keywords but isn't code
        assert detect_modality("I want to import a module.") == Modality.TEXT

    def test_latex_equation_detected(self):
        assert detect_modality("Solve $x^2 + 2x = 0$ for x.") == Modality.EQUATION

    def test_latex_macro_detected(self):
        assert (
            detect_modality("The integral is \\int_0^1 f(x) dx")
            == Modality.EQUATION
        )

    def test_png_bytes_detected_as_image(self):
        png_magic = b"\x89PNG\r\n\x1a\n" + b"rest..."
        assert detect_modality(png_magic) == Modality.IMAGE

    def test_jpeg_bytes_detected_as_image(self):
        jpeg_magic = b"\xff\xd8\xff" + b"rest..."
        assert detect_modality(jpeg_magic) == Modality.IMAGE

    def test_dict_with_image_is_image(self):
        assert detect_modality({"image": "base64..."}) == Modality.IMAGE

    def test_dict_with_image_and_text_is_mixed(self):
        assert detect_modality({"image": "...", "text": "describe"}) == Modality.MIXED

    def test_structured_list_of_dicts(self):
        data = [{"id": 1, "v": 2}, {"id": 2, "v": 3}]
        assert detect_modality(data) == Modality.STRUCTURED

    def test_chat_style_dict_not_structured(self):
        assert detect_modality({"role": "user", "content": "hi"}) == Modality.TEXT

    def test_dict_with_image_url_field(self):
        assert detect_modality({"image_url": "http://..."}) == Modality.IMAGE


class TestModalityMetrics:
    def test_record_detection_updates_counter(self):
        reg = MetricsRegistry()
        record_detection(Modality.CODE, metrics=reg)
        record_detection(Modality.CODE, metrics=reg)
        record_detection(Modality.IMAGE, metrics=reg)

        code_counter = reg.counter(
            "modality_detected_total", "", labels={"modality": "code"}
        )
        image_counter = reg.counter(
            "modality_detected_total", "", labels={"modality": "image"}
        )
        assert code_counter.get() == 2
        assert image_counter.get() == 1

    def test_record_detection_no_metrics_is_noop(self):
        record_detection(Modality.TEXT, metrics=None)  # must not raise


# ═══════════════════════════════════════════════════════════════════════
# PR #84 — Hybrid graph execution via preload
# ═══════════════════════════════════════════════════════════════════════


async def _identity_node(state: dict) -> dict:
    # Echo the state so we can assert what entered the traversal.
    return {"echo": state}


class TestHybridGraphPreload:
    async def test_preload_fetches_into_initial_state(self):
        store = InMemoryStore()
        await store.aput(("glossary",), "db", {"def": "Database"})

        graph = StateGraph()
        graph.add_node("pass", _identity_node)
        graph.add_edge(START, "pass")
        graph.add_edge("pass", END)
        compiled = graph.compile()

        result = await compiled.invoke(
            {"query": "What is db?"},
            preload=[(("glossary",), "db", "glossary")],
            store=store,
        )

        assert result.success
        # The node echoed the state after preload merged glossary.
        echoed = result.state["echo"]
        assert echoed["glossary"] == {"def": "Database"}
        assert echoed["query"] == "What is db?"

    async def test_preload_silently_skips_missing_keys(self):
        store = InMemoryStore()

        graph = StateGraph()
        graph.add_node("pass", _identity_node)
        graph.add_edge(START, "pass")
        graph.add_edge("pass", END)
        compiled = graph.compile()

        result = await compiled.invoke(
            {"query": "hi"},
            preload=[(("glossary",), "missing", "glossary")],
            store=store,
        )

        assert result.success
        echoed = result.state["echo"]
        # Missing key => no state entry added
        assert "glossary" not in echoed

    async def test_preload_requires_store(self):
        graph = StateGraph()
        graph.add_node("pass", _identity_node)
        graph.add_edge(START, "pass")
        graph.add_edge("pass", END)
        compiled = graph.compile()

        with pytest.raises(ValueError, match="preload requires a store"):
            await compiled.invoke(
                {"x": 1},
                preload=[(("a",), "b", "c")],
            )

    async def test_without_preload_behaviour_unchanged(self):
        # Regression: callers that don't pass preload keep working.
        graph = StateGraph()
        graph.add_node("pass", _identity_node)
        graph.add_edge(START, "pass")
        graph.add_edge("pass", END)
        compiled = graph.compile()

        result = await compiled.invoke({"x": 1})
        assert result.success
        assert result.state["echo"]["x"] == 1
