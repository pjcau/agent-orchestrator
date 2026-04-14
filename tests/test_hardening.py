"""Tests for v0.6.0 — Production Hardening."""

import time

import pytest
from agent_orchestrator.core.rate_limiter import (
    RateLimitConfig,
    RateLimiter,
)
from agent_orchestrator.core.audit import (
    AuditEntry,
    AuditLog,
    EVENT_AGENT_START,
    EVENT_AGENT_COMPLETE,
    EVENT_TOOL_CALL,
    EVENT_PROVIDER_CALL,
)
from agent_orchestrator.core.task_queue import (
    QueuedTask,
    TaskQueue,
)
from agent_orchestrator.core.metrics import (
    Counter,
    Gauge,
    Histogram,
    MetricsRegistry,
    default_metrics,
)
from agent_orchestrator.core.alerts import (
    AlertManager,
    AlertRule,
    PERIOD_SESSION,
    PERIOD_TASK,
    PERIOD_DAY,
    ACTION_LOG,
    ACTION_WEBHOOK,
)


# --- RateLimiter ---


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_acquire_within_limits(self):
        limiter = RateLimiter(
            [
                RateLimitConfig(requests_per_minute=10, tokens_per_minute=10000, provider_key="p1"),
            ]
        )
        assert await limiter.acquire("p1") is True

    @pytest.mark.asyncio
    async def test_acquire_exceeds_request_limit(self):
        limiter = RateLimiter(
            [
                RateLimitConfig(requests_per_minute=2, tokens_per_minute=100000, provider_key="p1"),
            ]
        )
        limiter.record_usage("p1", 100)
        limiter.record_usage("p1", 100)
        assert await limiter.acquire("p1") is False

    @pytest.mark.asyncio
    async def test_acquire_exceeds_token_limit(self):
        limiter = RateLimiter(
            [
                RateLimitConfig(requests_per_minute=100, tokens_per_minute=500, provider_key="p1"),
            ]
        )
        limiter.record_usage("p1", 400)
        assert await limiter.acquire("p1", estimated_tokens=200) is False

    @pytest.mark.asyncio
    async def test_unknown_provider_allowed(self):
        limiter = RateLimiter([])
        assert await limiter.acquire("unknown") is True

    def test_get_status(self):
        limiter = RateLimiter(
            [
                RateLimitConfig(requests_per_minute=10, tokens_per_minute=5000, provider_key="p1"),
            ]
        )
        limiter.record_usage("p1", 1000)
        status = limiter.get_status("p1")
        assert status.provider_key == "p1"
        assert status.requests_remaining == 9
        assert status.tokens_remaining == 4000
        assert status.is_limited is False

    def test_get_status_when_limited(self):
        limiter = RateLimiter(
            [
                RateLimitConfig(requests_per_minute=1, tokens_per_minute=5000, provider_key="p1"),
            ]
        )
        limiter.record_usage("p1", 100)
        status = limiter.get_status("p1")
        assert status.is_limited is True
        assert status.requests_remaining == 0

    def test_reset(self):
        limiter = RateLimiter(
            [
                RateLimitConfig(requests_per_minute=1, tokens_per_minute=5000, provider_key="p1"),
            ]
        )
        limiter.record_usage("p1", 100)
        limiter.reset("p1")
        status = limiter.get_status("p1")
        # After reset, state is cleared — full capacity available again
        assert status.requests_remaining == 1
        assert status.is_limited is False

    @pytest.mark.asyncio
    async def test_reset_allows_new_requests(self):
        limiter = RateLimiter(
            [
                RateLimitConfig(requests_per_minute=1, tokens_per_minute=5000, provider_key="p1"),
            ]
        )
        limiter.record_usage("p1", 100)
        assert await limiter.acquire("p1") is False
        limiter.reset("p1")
        assert await limiter.acquire("p1") is True

    def test_get_status_unknown_provider(self):
        limiter = RateLimiter([])
        status = limiter.get_status("unknown")
        assert status.is_limited is False

    def test_thread_safe_record_usage(self):
        """Concurrent record_usage from many threads must not lose records."""
        import threading

        limiter = RateLimiter(
            [
                RateLimitConfig(
                    requests_per_minute=100_000, tokens_per_minute=10_000_000, provider_key="p1"
                )
            ]
        )

        def worker() -> None:
            for _ in range(200):
                limiter.record_usage("p1", 10)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        status = limiter.get_status("p1")
        # 8 * 200 = 1600 requests, each 10 tokens = 16_000 tokens
        assert 100_000 - status.requests_remaining == 1600
        assert 10_000_000 - status.tokens_remaining == 16_000


# --- AuditLog ---


class TestAuditLog:
    def test_log_and_get_entries(self):
        log = AuditLog()
        log.log_action(EVENT_AGENT_START, "backend", "Starting task", task_id="t1")
        log.log_action(EVENT_AGENT_COMPLETE, "backend", "Task done", task_id="t1")
        entries = log.get_entries()
        assert len(entries) == 2

    def test_filter_by_event_type(self):
        log = AuditLog()
        log.log_action(EVENT_AGENT_START, "backend", "start")
        log.log_action(EVENT_TOOL_CALL, "backend", "call tool")
        log.log_action(EVENT_AGENT_COMPLETE, "backend", "done")
        entries = log.get_entries(event_type=EVENT_TOOL_CALL)
        assert len(entries) == 1
        assert entries[0].event_type == EVENT_TOOL_CALL

    def test_filter_by_agent_name(self):
        log = AuditLog()
        log.log_action(EVENT_AGENT_START, "backend", "start")
        log.log_action(EVENT_AGENT_START, "frontend", "start")
        entries = log.get_entries(agent_name="backend")
        assert len(entries) == 1

    def test_filter_by_task_id(self):
        log = AuditLog()
        log.log_action(EVENT_AGENT_START, "backend", "start", task_id="t1")
        log.log_action(EVENT_AGENT_START, "backend", "start", task_id="t2")
        entries = log.get_entries(task_id="t1")
        assert len(entries) == 1

    def test_get_agent_history(self):
        log = AuditLog()
        log.log_action(EVENT_AGENT_START, "backend", "start")
        log.log_action(EVENT_TOOL_CALL, "backend", "tool")
        log.log_action(EVENT_AGENT_START, "frontend", "start")
        history = log.get_agent_history("backend")
        assert len(history) == 2

    def test_get_task_trace(self):
        log = AuditLog()
        log.log_action(EVENT_AGENT_START, "backend", "start", task_id="t1")
        log.log_action(EVENT_TOOL_CALL, "backend", "tool", task_id="t1")
        log.log_action(EVENT_AGENT_COMPLETE, "backend", "done", task_id="t1")
        log.log_action(EVENT_AGENT_START, "frontend", "start", task_id="t2")
        trace = log.get_task_trace("t1")
        assert len(trace) == 3

    def test_export_json(self):
        log = AuditLog()
        log.log_action(EVENT_AGENT_START, "backend", "start", cost_usd=0.01, tokens=100)
        exported = log.export_json()
        assert len(exported) == 1
        assert exported[0]["event_type"] == EVENT_AGENT_START
        assert exported[0]["cost_usd"] == 0.01
        assert exported[0]["tokens"] == 100

    def test_clear(self):
        log = AuditLog()
        log.log_action(EVENT_AGENT_START, "backend", "start")
        log.clear()
        assert len(log.get_entries()) == 0

    def test_limit_parameter(self):
        log = AuditLog()
        for i in range(10):
            log.log_action(EVENT_AGENT_START, "backend", f"action-{i}")
        entries = log.get_entries(limit=3)
        assert len(entries) == 3
        # Should return the 3 most recent
        assert entries[-1].action == "action-9"

    def test_log_action_returns_entry(self):
        log = AuditLog()
        entry = log.log_action(
            EVENT_PROVIDER_CALL, "backend", "call LLM", provider_key="openrouter"
        )
        assert isinstance(entry, AuditEntry)
        assert entry.provider_key == "openrouter"


# --- TaskQueue ---


class TestTaskQueue:
    def test_enqueue_and_dequeue(self):
        queue = TaskQueue()
        queue.enqueue(QueuedTask(task_id="t1", description="task 1", priority=1))
        task = queue.dequeue()
        assert task is not None
        assert task.task_id == "t1"
        assert task.status == "running"

    def test_priority_ordering(self):
        queue = TaskQueue()
        queue.enqueue(QueuedTask(task_id="low", description="low", priority=1))
        queue.enqueue(QueuedTask(task_id="high", description="high", priority=10))
        queue.enqueue(QueuedTask(task_id="mid", description="mid", priority=5))
        task = queue.dequeue()
        assert task.task_id == "high"

    def test_fifo_within_same_priority(self):
        queue = TaskQueue()
        queue.enqueue(
            QueuedTask(
                task_id="first",
                description="first",
                priority=5,
                created_at=time.time() - 10,
            )
        )
        queue.enqueue(
            QueuedTask(
                task_id="second",
                description="second",
                priority=5,
                created_at=time.time(),
            )
        )
        task = queue.dequeue()
        assert task.task_id == "first"

    def test_dequeue_empty(self):
        queue = TaskQueue()
        assert queue.dequeue() is None

    def test_complete(self):
        queue = TaskQueue()
        queue.enqueue(QueuedTask(task_id="t1", description="task", priority=1))
        queue.dequeue()
        queue.complete("t1", "done!")
        task = queue.get_task("t1")
        assert task.status == "completed"
        assert task.result == "done!"
        assert task.completed_at is not None

    def test_fail_with_auto_retry(self):
        queue = TaskQueue()
        queue.enqueue(QueuedTask(task_id="t1", description="task", priority=1, max_retries=3))
        queue.dequeue()
        queue.fail("t1", "error occurred")
        task = queue.get_task("t1")
        assert task.status == "pending"  # auto re-queued
        assert task.retries == 1

    def test_fail_permanent_after_max_retries(self):
        queue = TaskQueue()
        queue.enqueue(QueuedTask(task_id="t1", description="task", priority=1, max_retries=1))
        queue.dequeue()
        queue.fail("t1", "error 1")  # retries=1 >= max_retries=1
        task = queue.get_task("t1")
        assert task.status == "failed"

    def test_manual_retry(self):
        queue = TaskQueue()
        queue.enqueue(QueuedTask(task_id="t1", description="task", priority=1, max_retries=1))
        queue.dequeue()
        queue.fail("t1", "error")
        assert queue.get_task("t1").status == "failed"
        assert queue.retry("t1") is True
        assert queue.get_task("t1").status == "pending"

    def test_retry_non_failed_returns_false(self):
        queue = TaskQueue()
        queue.enqueue(QueuedTask(task_id="t1", description="task", priority=1))
        assert queue.retry("t1") is False

    def test_retry_nonexistent_returns_false(self):
        queue = TaskQueue()
        assert queue.retry("nope") is False

    def test_get_pending_and_running(self):
        queue = TaskQueue()
        queue.enqueue(QueuedTask(task_id="t1", description="task1", priority=1))
        queue.enqueue(QueuedTask(task_id="t2", description="task2", priority=1))
        queue.dequeue()  # t1 or t2 becomes running
        assert len(queue.get_pending()) == 1
        assert len(queue.get_running()) == 1

    def test_get_stats(self):
        queue = TaskQueue()
        queue.enqueue(QueuedTask(task_id="t1", description="t", priority=1))
        queue.enqueue(QueuedTask(task_id="t2", description="t", priority=1))
        queue.dequeue()
        queue.complete("t1", "ok")
        stats = queue.get_stats()
        assert stats.pending == 1
        assert stats.completed == 1
        assert stats.total == 2

    def test_dequeue_by_agent_name(self):
        queue = TaskQueue()
        queue.enqueue(
            QueuedTask(task_id="t1", description="task", priority=1, agent_name="backend")
        )
        queue.enqueue(
            QueuedTask(task_id="t2", description="task", priority=1, agent_name="frontend")
        )
        task = queue.dequeue(agent_name="frontend")
        assert task is not None
        assert task.task_id == "t2"


# --- Metrics ---


class TestMetrics:
    def test_counter_inc_and_get(self):
        c = Counter("test_counter")
        c.inc()
        c.inc(5)
        assert c.get() == 6.0

    def test_counter_negative_raises(self):
        c = Counter("test_counter")
        with pytest.raises(ValueError):
            c.inc(-1)

    def test_counter_reset(self):
        c = Counter("test_counter")
        c.inc(10)
        c.reset()
        assert c.get() == 0.0

    def test_gauge_operations(self):
        g = Gauge("test_gauge")
        g.set(10)
        assert g.get() == 10.0
        g.inc(5)
        assert g.get() == 15.0
        g.dec(3)
        assert g.get() == 12.0

    def test_histogram_observe_and_stats(self):
        h = Histogram("test_hist")
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            h.observe(v)
        assert h.get_count() == 5
        assert h.get_sum() == 15.0
        assert h.get_avg() == 3.0

    def test_histogram_percentile(self):
        h = Histogram("test_hist")
        for v in range(1, 101):
            h.observe(float(v))
        assert h.get_percentile(50) == pytest.approx(50.5, abs=1)
        assert h.get_percentile(0) == 1.0
        assert h.get_percentile(100) == 100.0

    def test_histogram_empty(self):
        h = Histogram("test_hist")
        assert h.get_count() == 0
        assert h.get_avg() == 0.0
        assert h.get_percentile(50) == 0.0

    def test_registry_counter(self):
        reg = MetricsRegistry()
        c1 = reg.counter("requests", labels={"method": "GET"})
        c2 = reg.counter("requests", labels={"method": "GET"})
        assert c1 is c2  # same key returns same instance

    def test_registry_type_mismatch(self):
        reg = MetricsRegistry()
        reg.counter("metric_name")
        with pytest.raises(TypeError):
            reg.gauge("metric_name")

    def test_registry_get_all(self):
        reg = MetricsRegistry()
        reg.counter("c1").inc(10)
        reg.gauge("g1").set(42)
        reg.histogram("h1").observe(1.5)
        all_metrics = reg.get_all()
        assert len(all_metrics) == 3

    def test_export_prometheus(self):
        reg = MetricsRegistry()
        reg.counter("http_requests_total", "Total HTTP requests", labels={"method": "GET"}).inc(5)
        output = reg.export_prometheus()
        assert "# HELP http_requests_total" in output
        assert "# TYPE http_requests_total counter" in output
        assert 'http_requests_total{method="GET"}' in output
        assert "5" in output

    def test_default_metrics_creates_registry(self):
        reg = default_metrics()
        all_metrics = reg.get_all()
        # Should have many pre-defined metrics
        assert len(all_metrics) > 10

    def test_default_metrics_uses_existing_registry(self):
        reg = MetricsRegistry()
        reg.counter("custom_metric").inc(1)
        result = default_metrics(reg)
        assert result is reg
        all_metrics = result.get_all()
        assert "custom_metric" in all_metrics


# --- AlertManager ---


class TestAlertManager:
    def test_no_alert_below_threshold(self):
        manager = AlertManager(
            [
                AlertRule(
                    name="high_spend", threshold_usd=1.0, period=PERIOD_SESSION, action=ACTION_LOG
                ),
            ]
        )
        alerts = manager.check(0.5, PERIOD_SESSION)
        assert len(alerts) == 0

    def test_alert_fires_above_threshold(self):
        manager = AlertManager(
            [
                AlertRule(
                    name="high_spend", threshold_usd=1.0, period=PERIOD_SESSION, action=ACTION_LOG
                ),
            ]
        )
        alerts = manager.check(1.5, PERIOD_SESSION)
        assert len(alerts) == 1
        assert alerts[0].rule_name == "high_spend"
        assert alerts[0].current_spend == 1.5
        assert alerts[0].threshold == 1.0

    def test_dedup_prevents_double_fire(self):
        manager = AlertManager(
            [
                AlertRule(
                    name="high_spend", threshold_usd=1.0, period=PERIOD_SESSION, action=ACTION_LOG
                ),
            ]
        )
        alerts1 = manager.check(1.5, PERIOD_SESSION)
        alerts2 = manager.check(2.0, PERIOD_SESSION)
        assert len(alerts1) == 1
        assert len(alerts2) == 0  # already fired

    def test_different_periods_fire_independently(self):
        manager = AlertManager(
            [
                AlertRule(
                    name="session_alert",
                    threshold_usd=1.0,
                    period=PERIOD_SESSION,
                    action=ACTION_LOG,
                ),
                AlertRule(
                    name="task_alert", threshold_usd=0.5, period=PERIOD_TASK, action=ACTION_LOG
                ),
            ]
        )
        alerts = manager.check(1.5, PERIOD_SESSION)
        assert len(alerts) == 1
        assert alerts[0].rule_name == "session_alert"

        alerts = manager.check(0.6, PERIOD_TASK, task_id="t1")
        assert len(alerts) == 1
        assert alerts[0].rule_name == "task_alert"

    def test_get_triggered_alerts(self):
        manager = AlertManager(
            [
                AlertRule(
                    name="alert1", threshold_usd=0.1, period=PERIOD_SESSION, action=ACTION_LOG
                ),
            ]
        )
        manager.check(0.5, PERIOD_SESSION)
        triggered = manager.get_triggered_alerts()
        assert len(triggered) == 1

    def test_clear_alerts(self):
        manager = AlertManager(
            [
                AlertRule(
                    name="alert1", threshold_usd=0.1, period=PERIOD_SESSION, action=ACTION_LOG
                ),
            ]
        )
        manager.check(0.5, PERIOD_SESSION)
        manager.clear_alerts()
        assert len(manager.get_triggered_alerts()) == 0
        # Should fire again after clear
        alerts = manager.check(0.5, PERIOD_SESSION)
        assert len(alerts) == 1

    def test_add_rule(self):
        manager = AlertManager([])
        manager.add_rule(
            AlertRule(name="new_rule", threshold_usd=0.5, period=PERIOD_DAY, action=ACTION_LOG)
        )
        alerts = manager.check(1.0, PERIOD_DAY)
        assert len(alerts) == 1

    def test_remove_rule(self):
        manager = AlertManager(
            [
                AlertRule(
                    name="removable", threshold_usd=0.1, period=PERIOD_SESSION, action=ACTION_LOG
                ),
            ]
        )
        manager.remove_rule("removable")
        alerts = manager.check(1.0, PERIOD_SESSION)
        assert len(alerts) == 0

    def test_webhook_action_stores_alert(self):
        manager = AlertManager(
            [
                AlertRule(
                    name="webhook_alert",
                    threshold_usd=0.1,
                    period=PERIOD_SESSION,
                    action=ACTION_WEBHOOK,
                    webhook_url="https://hooks.example.com/alert",
                ),
            ]
        )
        alerts = manager.check(0.5, PERIOD_SESSION)
        assert len(alerts) == 1
        assert alerts[0].rule_name == "webhook_alert"

    def test_task_specific_dedup(self):
        manager = AlertManager(
            [
                AlertRule(
                    name="task_alert", threshold_usd=0.1, period=PERIOD_TASK, action=ACTION_LOG
                ),
            ]
        )
        alerts1 = manager.check(0.5, PERIOD_TASK, task_id="t1")
        alerts2 = manager.check(0.5, PERIOD_TASK, task_id="t2")
        assert len(alerts1) == 1
        assert len(alerts2) == 1  # different task_id = separate dedup key
