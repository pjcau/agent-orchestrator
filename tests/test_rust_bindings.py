"""Integration tests for the Rust-backed `_agent_orchestrator_rust` extension module.

These tests are skipped automatically when the native extension has not been
built yet (i.e., before `maturin develop` or `maturin build` has been run
inside `rust/`).  They verify that every Rust type produces output identical
to its Python counterpart.

Run after building the extension:
    cd rust && maturin develop --release
    pytest tests/test_rust_bindings.py -v
"""

from __future__ import annotations


import pytest

# ---------------------------------------------------------------------------
# Optional import guard — skip the entire module if the extension is absent.
# ---------------------------------------------------------------------------

rust = pytest.importorskip(
    "_agent_orchestrator_rust",
    reason="Rust extension not built; run 'maturin develop' inside rust/",
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _labels(**kw: str) -> dict[str, str]:
    return dict(kw)


# ---------------------------------------------------------------------------
# GraphTopology
# ---------------------------------------------------------------------------


class TestGraphTopology:
    START = rust.START
    END = rust.END

    def _simple_graph(self) -> rust.GraphTopology:
        g = rust.GraphTopology()
        g.add_node("a")
        g.add_node("b")
        g.add_edge(self.START, "a")
        g.add_edge("a", "b")
        g.add_edge("b", self.END)
        return g

    # --- sentinels ---

    def test_start_constant(self) -> None:
        assert self.START == "__start__"

    def test_end_constant(self) -> None:
        assert self.END == "__end__"

    # --- add_node ---

    def test_add_node_basic(self) -> None:
        g = rust.GraphTopology()
        g.add_node("foo")  # must not raise

    def test_add_node_rejects_start(self) -> None:
        g = rust.GraphTopology()
        with pytest.raises(ValueError, match="reserved"):
            g.add_node(self.START)

    def test_add_node_rejects_end(self) -> None:
        g = rust.GraphTopology()
        with pytest.raises(ValueError, match="reserved"):
            g.add_node(self.END)

    def test_add_node_rejects_duplicate(self) -> None:
        g = rust.GraphTopology()
        g.add_node("dup")
        with pytest.raises(ValueError, match="already exists"):
            g.add_node("dup")

    # --- validate ---

    def test_validate_valid_graph(self) -> None:
        self._simple_graph().validate()  # must not raise

    def test_validate_no_start_edge(self) -> None:
        g = rust.GraphTopology()
        g.add_node("x")
        with pytest.raises(ValueError, match="START"):
            g.validate()

    def test_validate_unreachable_node(self) -> None:
        g = rust.GraphTopology()
        g.add_node("reachable")
        g.add_node("orphan")
        g.add_edge(self.START, "reachable")
        g.add_edge("reachable", self.END)
        with pytest.raises(ValueError, match="[Uu]nreachable"):
            g.validate()

    def test_validate_invalid_fixed_target(self) -> None:
        g = rust.GraphTopology()
        g.add_node("a")
        g.add_edge(self.START, "a")
        g.add_edge("a", "nonexistent")
        with pytest.raises(ValueError, match="[Tt]arget"):
            g.validate()

    def test_validate_invalid_conditional_route_map_target(self) -> None:
        g = rust.GraphTopology()
        g.add_node("a")
        g.add_edge(self.START, "a")
        g.add_conditional_edge("a", {"go": "does_not_exist"})
        with pytest.raises(ValueError, match="[Tt]arget"):
            g.validate()

    # --- find_reachable ---

    def test_find_reachable_simple(self) -> None:
        g = self._simple_graph()
        reachable = g.find_reachable(self.START)
        assert "a" in reachable
        assert "b" in reachable
        assert self.START not in reachable
        assert self.END not in reachable

    # --- get_next_nodes_fixed ---

    def test_get_next_nodes_fixed(self) -> None:
        g = self._simple_graph()
        assert g.get_next_nodes_fixed(self.START) == ["a"]

    # --- resolve_conditional ---

    def test_resolve_conditional_via_route_map(self) -> None:
        g = rust.GraphTopology()
        g.add_node("yes")
        g.add_node("no")
        g.add_edge(self.START, "yes")
        g.add_edge("yes", self.END)
        g.add_conditional_edge("yes", {"go": "yes", "stop": self.END})
        assert g.resolve_conditional("yes", ["go"]) == ["yes"]
        assert g.resolve_conditional("yes", ["stop"]) == [self.END]

    # --- get_graph_info ---

    def test_get_graph_info_returns_dict(self) -> None:
        info = self._simple_graph().get_graph_info()
        assert isinstance(info, dict)
        assert "nodes" in info
        assert "edges" in info


# ---------------------------------------------------------------------------
# RustClassifier
# ---------------------------------------------------------------------------


class TestRustClassifier:
    clf = rust.RustClassifier()

    # --- token estimation ---

    def test_empty_string_tokens(self) -> None:
        r = self.clf.classify("")
        # max(500, int(0 * 1.3) + 1500) = 1500
        assert r.estimated_tokens == 1500

    def test_single_word_tokens(self) -> None:
        r = self.clf.classify("hello")
        # max(500, int(1 * 1.3) + 1500) = max(500, 1501) = 1501
        assert r.estimated_tokens == 1501

    # --- level = low ---

    def test_low_keyword_summarize(self) -> None:
        assert self.clf.classify("summarize this document").level == "low"

    def test_low_short_no_signals(self) -> None:
        assert self.clf.classify("echo hello world").level == "low"

    def test_low_git_commit_pattern(self) -> None:
        assert self.clf.classify("git commit -m 'fix'").level == "low"

    def test_low_lint_pattern(self) -> None:
        assert self.clf.classify("lint the code").level == "low"

    # --- level = high ---

    def test_high_architecture_keyword(self) -> None:
        assert self.clf.classify("design the architecture").level == "high"

    def test_high_machine_learning(self) -> None:
        assert self.clf.classify("machine learning pipeline setup").level == "high"

    def test_high_over_300_words(self) -> None:
        task = " ".join(["word"] * 305)
        assert self.clf.classify(task).level == "high"

    def test_high_security_audit(self) -> None:
        assert self.clf.classify("do a security audit of the auth module").level == "high"

    # --- level = medium ---

    def test_medium_50_neutral_words(self) -> None:
        task = " ".join(["the"] * 50)
        assert self.clf.classify(task).level == "medium"

    # --- requires_tools ---

    def test_requires_tools_code(self) -> None:
        assert self.clf.classify("write some code").requires_tools is True

    def test_requires_tools_false(self) -> None:
        assert self.clf.classify("analyze the market strategy").requires_tools is False

    # --- requires_reasoning ---

    def test_requires_reasoning_high_keyword(self) -> None:
        assert self.clf.classify("analyze the security implications").requires_reasoning is True

    def test_requires_reasoning_false_short(self) -> None:
        assert self.clf.classify("ping").requires_reasoning is False

    # --- case insensitivity ---

    def test_case_insensitive(self) -> None:
        upper = self.clf.classify("ARCHITECTURE review")
        lower = self.clf.classify("architecture review")
        assert upper.level == lower.level


# ---------------------------------------------------------------------------
# RustQueuedTask / RustTaskQueue
# ---------------------------------------------------------------------------


def _make_task(task_id: str, priority: int) -> rust.RustQueuedTask:
    return rust.RustQueuedTask(task_id=task_id, description="test", priority=priority)


class TestRustTaskQueue:
    def test_enqueue_returns_id(self) -> None:
        q = rust.RustTaskQueue()
        assert q.enqueue(_make_task("t1", 1)) == "t1"

    def test_enqueue_forces_pending(self) -> None:
        q = rust.RustTaskQueue()
        t = rust.RustQueuedTask(task_id="t1", description="d", priority=1, status="running")
        q.enqueue(t)
        assert q.get_task("t1").status == "pending"

    def test_dequeue_highest_priority(self) -> None:
        q = rust.RustTaskQueue()
        q.enqueue(_make_task("low", 1))
        q.enqueue(_make_task("high", 10))
        assert q.dequeue().task_id == "high"

    def test_dequeue_fifo_same_priority(self) -> None:
        q = rust.RustTaskQueue()
        t1 = rust.RustQueuedTask(task_id="first", description="d", priority=5, created_at=1000.0)
        t2 = rust.RustQueuedTask(task_id="second", description="d", priority=5, created_at=2000.0)
        q.enqueue(t1)
        q.enqueue(t2)
        assert q.dequeue().task_id == "first"

    def test_dequeue_empty_returns_none(self) -> None:
        q = rust.RustTaskQueue()
        assert q.dequeue() is None

    def test_complete_sets_status(self) -> None:
        q = rust.RustTaskQueue()
        q.enqueue(_make_task("t1", 1))
        q.dequeue()
        q.complete("t1", "done")
        assert q.get_task("t1").status == "completed"
        assert q.get_task("t1").result == "done"

    def test_fail_retries_when_under_max(self) -> None:
        q = rust.RustTaskQueue()
        q.enqueue(_make_task("t1", 1))
        q.dequeue()
        q.fail("t1", "err")
        t = q.get_task("t1")
        assert t.status == "pending"
        assert t.retries == 1

    def test_fail_permanently_when_max_retries_reached(self) -> None:
        q = rust.RustTaskQueue()
        t = rust.RustQueuedTask(task_id="t1", description="d", priority=1, max_retries=1)
        q.enqueue(t)
        q.dequeue()
        q.fail("t1", "err")
        assert q.get_task("t1").status == "failed"

    def test_retry_failed_task(self) -> None:
        q = rust.RustTaskQueue()
        t = rust.RustQueuedTask(task_id="t1", description="d", priority=1, max_retries=1)
        q.enqueue(t)
        q.dequeue()
        q.fail("t1", "err")
        assert q.retry("t1") is True
        assert q.get_task("t1").status == "pending"

    def test_retry_non_failed_returns_false(self) -> None:
        q = rust.RustTaskQueue()
        q.enqueue(_make_task("t1", 1))
        assert q.retry("t1") is False

    def test_get_stats_keys(self) -> None:
        q = rust.RustTaskQueue()
        stats = q.get_stats()
        assert set(stats.keys()) == {"pending", "running", "completed", "failed", "total"}

    def test_agent_name_filtering(self) -> None:
        q = rust.RustTaskQueue()
        t = rust.RustQueuedTask(task_id="t1", description="d", priority=1, agent_name="backend")
        q.enqueue(t)
        assert q.dequeue(agent_name="frontend") is None
        assert q.dequeue(agent_name="backend") is not None


# ---------------------------------------------------------------------------
# RustRateLimiter
# ---------------------------------------------------------------------------


class TestRustRateLimiter:
    def _limiter(self, rpm: int = 60, tpm: int = 100_000) -> rust.RustRateLimiter:
        return rust.RustRateLimiter([("test", rpm, tpm)])

    def test_allows_under_limit(self) -> None:
        assert self._limiter().acquire("test", 100) is True

    def test_allows_unknown_provider(self) -> None:
        rl = rust.RustRateLimiter([])
        assert rl.acquire("unknown", 999_999) is True

    def test_denies_when_rpm_exhausted(self) -> None:
        rl = self._limiter(rpm=2, tpm=1_000_000)
        rl.record_usage("test", 0)
        rl.record_usage("test", 0)
        assert rl.acquire("test", 0) is False

    def test_denies_when_tpm_would_exceed(self) -> None:
        rl = self._limiter(rpm=1000, tpm=500)
        rl.record_usage("test", 400)
        assert rl.acquire("test", 200) is False

    def test_allows_zero_estimated_tokens_ignores_tpm(self) -> None:
        rl = self._limiter(rpm=1000, tpm=10)
        rl.record_usage("test", 10)
        # tpm exhausted, but estimated_tokens==0 bypasses the check
        assert rl.acquire("test", 0) is True

    def test_reset_clears_usage(self) -> None:
        rl = self._limiter(rpm=1, tpm=1_000_000)
        rl.record_usage("test", 0)
        assert rl.acquire("test", 0) is False
        rl.reset("test")
        assert rl.acquire("test", 0) is True

    def test_get_status_keys(self) -> None:
        rl = self._limiter()
        status = rl.get_status("test")
        assert set(status.keys()) == {
            "provider_key",
            "requests_remaining",
            "tokens_remaining",
            "resets_at",
            "is_limited",
        }

    def test_get_status_not_limited_when_under_limit(self) -> None:
        rl = self._limiter()
        assert rl.get_status("test")["is_limited"] is False


# ---------------------------------------------------------------------------
# RustMetricsRegistry
# ---------------------------------------------------------------------------


class TestRustMetricsRegistry:
    # --- counter ---

    def test_counter_starts_at_zero(self) -> None:
        reg = rust.RustMetricsRegistry()
        assert reg.counter_get("hits", None) == 0.0

    def test_counter_increment(self) -> None:
        reg = rust.RustMetricsRegistry()
        reg.counter_inc("hits", None, 3.0)
        assert reg.counter_get("hits", None) == 3.0

    def test_counter_negative_raises(self) -> None:
        reg = rust.RustMetricsRegistry()
        with pytest.raises((ValueError, Exception)):
            reg.counter_inc("hits", None, -1.0)

    def test_counter_with_labels(self) -> None:
        reg = rust.RustMetricsRegistry()
        lbl = {"agent": "backend"}
        reg.counter_inc("tasks", lbl, 5.0)
        assert reg.counter_get("tasks", lbl) == 5.0

    # --- gauge ---

    def test_gauge_set_get(self) -> None:
        reg = rust.RustMetricsRegistry()
        reg.gauge_set("mem", None, 42.0)
        assert reg.gauge_get("mem", None) == 42.0

    def test_gauge_inc(self) -> None:
        reg = rust.RustMetricsRegistry()
        reg.gauge_set("mem", None, 10.0)
        reg.gauge_inc("mem", None, 5.0)
        assert reg.gauge_get("mem", None) == 15.0

    def test_gauge_dec(self) -> None:
        reg = rust.RustMetricsRegistry()
        reg.gauge_set("mem", None, 10.0)
        reg.gauge_dec("mem", None, 3.0)
        assert reg.gauge_get("mem", None) == 7.0

    # --- histogram ---

    def test_histogram_empty_percentile_zero(self) -> None:
        reg = rust.RustMetricsRegistry()
        assert reg.histogram_get_percentile("lat", None, 50.0) == 0.0

    def test_histogram_p50_exact(self) -> None:
        reg = rust.RustMetricsRegistry()
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            reg.histogram_observe("lat", None, v)
        p50 = reg.histogram_get_percentile("lat", None, 50.0)
        assert abs(p50 - 3.0) < 1e-9

    def test_histogram_p95_interpolation(self) -> None:
        reg = rust.RustMetricsRegistry()
        for v in [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]:
            reg.histogram_observe("lat", None, v)
        p95 = reg.histogram_get_percentile("lat", None, 95.0)
        assert abs(p95 - 9.55) < 1e-9

    def test_histogram_p0_is_min(self) -> None:
        reg = rust.RustMetricsRegistry()
        for v in [5.0, 1.0, 3.0]:
            reg.histogram_observe("lat", None, v)
        assert reg.histogram_get_percentile("lat", None, 0.0) == 1.0

    def test_histogram_p100_is_max(self) -> None:
        reg = rust.RustMetricsRegistry()
        for v in [5.0, 1.0, 3.0]:
            reg.histogram_observe("lat", None, v)
        assert reg.histogram_get_percentile("lat", None, 100.0) == 5.0

    # --- prometheus export ---

    def test_export_counter_type_line(self) -> None:
        reg = rust.RustMetricsRegistry()
        reg.counter_inc("reqs", None, 1.0)
        output = reg.export_prometheus()
        assert "# TYPE reqs counter" in output
        assert output.endswith("\n")

    def test_export_gauge_type_line(self) -> None:
        reg = rust.RustMetricsRegistry()
        reg.gauge_set("mem_bytes", None, 1024.0)
        output = reg.export_prometheus()
        assert "# TYPE mem_bytes gauge" in output

    def test_export_histogram_count_sum(self) -> None:
        reg = rust.RustMetricsRegistry()
        reg.histogram_observe("latency", None, 0.1)
        output = reg.export_prometheus()
        assert "latency_count" in output
        assert "latency_sum" in output

    def test_export_label_format(self) -> None:
        reg = rust.RustMetricsRegistry()
        reg.counter_inc("tasks", {"agent": "backend", "status": "ok"}, 1.0)
        output = reg.export_prometheus()
        assert 'agent="backend"' in output
        assert 'status="ok"' in output

    def test_export_empty_registry_empty_string(self) -> None:
        reg = rust.RustMetricsRegistry()
        assert reg.export_prometheus() == ""

    def test_export_metrics_sorted_by_name(self) -> None:
        reg = rust.RustMetricsRegistry()
        reg.counter_inc("z_metric", None, 1.0)
        reg.counter_inc("a_metric", None, 1.0)
        output = reg.export_prometheus()
        assert output.index("a_metric") < output.index("z_metric")

    # --- get_all ---

    def test_get_all_contains_counter(self) -> None:
        reg = rust.RustMetricsRegistry()
        reg.counter_inc("hits", None, 7.0)
        all_metrics = reg.get_all()
        assert any(v.get("type") == "counter" for v in all_metrics.values())

    def test_get_all_histogram_has_percentile_keys(self) -> None:
        reg = rust.RustMetricsRegistry()
        reg.histogram_observe("lat", None, 1.0)
        all_metrics = reg.get_all()
        hist = next(v for v in all_metrics.values() if v.get("type") == "histogram")
        assert "p50" in hist
        assert "p95" in hist
        assert "p99" in hist


# ---------------------------------------------------------------------------
# Parity tests: Rust vs Python for identical inputs
# ---------------------------------------------------------------------------


class TestParityRustVsPython:
    """Verify Rust produces the same results as the Python implementations."""

    def test_classifier_parity_low(self) -> None:
        from agent_orchestrator.core.router import TaskComplexityClassifier

        py_clf = TaskComplexityClassifier()
        rust_clf = rust.RustClassifier()

        tasks = [
            "summarize this document",
            "git commit -m 'fix'",
            "echo hello",
            "list all files",
            "rename the variable",
        ]
        for task in tasks:
            py = py_clf.classify(task)
            rs = rust_clf.classify(task)
            assert py.level == rs.level, (
                f"Level mismatch for {task!r}: py={py.level} rust={rs.level}"
            )
            assert py.estimated_tokens == rs.estimated_tokens, (
                f"Token mismatch for {task!r}: py={py.estimated_tokens} rust={rs.estimated_tokens}"
            )
            assert py.requires_tools == rs.requires_tools, f"requires_tools mismatch for {task!r}"
            assert py.requires_reasoning == rs.requires_reasoning, (
                f"requires_reasoning mismatch for {task!r}"
            )

    def test_classifier_parity_high(self) -> None:
        from agent_orchestrator.core.router import TaskComplexityClassifier

        py_clf = TaskComplexityClassifier()
        rust_clf = rust.RustClassifier()

        tasks = [
            "design the architecture of the distributed system",
            "machine learning model training pipeline with neural networks",
            "refactor the entire codebase for scalability",
            "deep dive into performance optimization",
            "security audit of authentication module",
        ]
        for task in tasks:
            py = py_clf.classify(task)
            rs = rust_clf.classify(task)
            assert py.level == rs.level, (
                f"Level mismatch for {task!r}: py={py.level} rust={rs.level}"
            )

    def test_classifier_parity_token_estimation(self) -> None:
        from agent_orchestrator.core.router import TaskComplexityClassifier

        py_clf = TaskComplexityClassifier()
        rust_clf = rust.RustClassifier()

        for word_count in [0, 1, 5, 30, 100, 200, 500]:
            task = " ".join(["word"] * word_count)
            py = py_clf.classify(task)
            rs = rust_clf.classify(task)
            assert py.estimated_tokens == rs.estimated_tokens, (
                f"Token mismatch for word_count={word_count}: "
                f"py={py.estimated_tokens} rust={rs.estimated_tokens}"
            )
