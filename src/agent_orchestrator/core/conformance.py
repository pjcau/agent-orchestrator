"""Conformance test suites for Provider and Checkpointer interfaces.

Any new implementation runs against these tests to verify correctness.

Usage:
    from agent_orchestrator.core.conformance import (
        run_provider_conformance,
        run_checkpointer_conformance,
    )

    results = await run_provider_conformance(MyProvider(...))
    results = await run_checkpointer_conformance(MyCheckpointer(...))
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable

from .provider import Provider, Message, Role, Completion
from .checkpoint import Checkpointer, Checkpoint


class TestStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TestResult:
    name: str
    status: TestStatus
    error: str | None = None
    duration_ms: float = 0.0


@dataclass
class ConformanceReport:
    suite: str
    implementation: str
    results: list[TestResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.PASSED)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.FAILED)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.SKIPPED)

    @property
    def all_passed(self) -> bool:
        return self.failed == 0

    def summary(self) -> str:
        total = len(self.results)
        return (
            f"{self.suite} [{self.implementation}]: "
            f"{self.passed}/{total} passed, {self.failed} failed, {self.skipped} skipped"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "implementation": self.implementation,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "all_passed": self.all_passed,
            "results": [
                {"name": r.name, "status": r.status.value, "error": r.error}
                for r in self.results
            ],
        }


async def _run_test(name: str, test_fn: Callable[[], Awaitable[None]]) -> TestResult:
    """Run a single test and capture result."""
    start = time.monotonic()
    try:
        await test_fn()
        duration = (time.monotonic() - start) * 1000
        return TestResult(name=name, status=TestStatus.PASSED, duration_ms=duration)
    except Exception as e:
        duration = (time.monotonic() - start) * 1000
        return TestResult(
            name=name, status=TestStatus.FAILED,
            error=f"{type(e).__name__}: {e}", duration_ms=duration,
        )


# ─── Provider Conformance ──────────────────────────────────────────────

async def run_provider_conformance(provider: Provider) -> ConformanceReport:
    """Run all conformance tests against a Provider implementation."""
    report = ConformanceReport(
        suite="Provider",
        implementation=f"{type(provider).__name__}({provider.model_id})",
    )

    async def test_model_id():
        mid = provider.model_id
        assert isinstance(mid, str), f"model_id must be str, got {type(mid)}"
        assert len(mid) > 0, "model_id must not be empty"

    async def test_capabilities():
        caps = provider.capabilities
        assert caps is not None, "capabilities must not be None"
        assert isinstance(caps.max_context, int), "max_context must be int"
        assert caps.max_context > 0, "max_context must be positive"
        assert isinstance(caps.supports_tools, bool)
        assert isinstance(caps.supports_streaming, bool)

    async def test_cost_properties():
        inp = provider.input_cost_per_million
        out = provider.output_cost_per_million
        assert isinstance(inp, (int, float)), "input_cost must be numeric"
        assert isinstance(out, (int, float)), "output_cost must be numeric"
        assert inp >= 0, "input_cost must be non-negative"
        assert out >= 0, "output_cost must be non-negative"

    async def test_estimate_cost():
        cost = provider.estimate_cost(1000, 500)
        assert isinstance(cost, float), "estimate_cost must return float"
        assert cost >= 0, "cost must be non-negative"

    async def test_complete_simple():
        messages = [Message(role=Role.USER, content="Say 'hello' and nothing else.")]
        result = await provider.complete(messages=messages, max_tokens=50)
        assert isinstance(result, Completion), f"Expected Completion, got {type(result)}"
        assert result.content is not None, "content must not be None"
        assert len(result.content) > 0, "content must not be empty"

    async def test_complete_with_system():
        messages = [Message(role=Role.USER, content="What are you?")]
        result = await provider.complete(
            messages=messages, system="You are a test bot. Reply with 'test'.",
            max_tokens=50,
        )
        assert isinstance(result, Completion)
        assert result.content is not None

    async def test_complete_returns_usage():
        messages = [Message(role=Role.USER, content="Say 'hi'.")]
        result = await provider.complete(messages=messages, max_tokens=50)
        if result.usage:
            assert result.usage.input_tokens >= 0
            assert result.usage.output_tokens >= 0

    async def test_complete_multi_turn():
        messages = [
            Message(role=Role.USER, content="My name is TestBot."),
            Message(role=Role.ASSISTANT, content="Nice to meet you, TestBot!"),
            Message(role=Role.USER, content="What is my name?"),
        ]
        result = await provider.complete(messages=messages, max_tokens=100)
        assert isinstance(result, Completion)
        assert result.content is not None

    async def test_stream_basic():
        if not provider.capabilities.supports_streaming:
            raise Exception("SKIP: streaming not supported")
        messages = [Message(role=Role.USER, content="Count from 1 to 3.")]
        chunks = []
        async for chunk in provider.stream(messages=messages, max_tokens=100):
            chunks.append(chunk)
        assert len(chunks) > 0, "stream must yield at least one chunk"
        # Last chunk should be final
        assert chunks[-1].is_final, "last chunk must be final"

    # Run all tests
    tests = [
        ("model_id", test_model_id),
        ("capabilities", test_capabilities),
        ("cost_properties", test_cost_properties),
        ("estimate_cost", test_estimate_cost),
        ("complete_simple", test_complete_simple),
        ("complete_with_system", test_complete_with_system),
        ("complete_returns_usage", test_complete_returns_usage),
        ("complete_multi_turn", test_complete_multi_turn),
        ("stream_basic", test_stream_basic),
    ]

    for name, fn in tests:
        result = await _run_test(name, fn)
        if result.error and result.error.startswith("Exception: SKIP:"):
            result.status = TestStatus.SKIPPED
            result.error = result.error.replace("Exception: SKIP: ", "")
        report.results.append(result)

    return report


# ─── Checkpointer Conformance ──────────────────────────────────────────

async def run_checkpointer_conformance(
    checkpointer: Checkpointer,
) -> ConformanceReport:
    """Run all conformance tests against a Checkpointer implementation."""
    report = ConformanceReport(
        suite="Checkpointer",
        implementation=type(checkpointer).__name__,
    )

    async def test_save_and_get():
        cp = Checkpoint(
            checkpoint_id="test-1",
            thread_id="thread-a",
            state={"x": 1, "y": "hello"},
            next_nodes=["node_b"],
            step_index=0,
        )
        await checkpointer.save(cp)
        loaded = await checkpointer.get("test-1")
        assert loaded is not None, "saved checkpoint must be retrievable"
        assert loaded.checkpoint_id == "test-1"
        assert loaded.thread_id == "thread-a"
        assert loaded.state == {"x": 1, "y": "hello"}
        assert loaded.next_nodes == ["node_b"]
        assert loaded.step_index == 0

    async def test_get_nonexistent():
        loaded = await checkpointer.get("nonexistent-id-xyz")
        assert loaded is None, "nonexistent checkpoint must return None"

    async def test_get_latest():
        for i in range(3):
            await checkpointer.save(Checkpoint(
                checkpoint_id=f"latest-{i}",
                thread_id="thread-latest",
                state={"step": i},
                next_nodes=[],
                step_index=i,
            ))
        latest = await checkpointer.get_latest("thread-latest")
        assert latest is not None
        assert latest.step_index == 2, f"expected step 2, got {latest.step_index}"
        assert latest.state == {"step": 2}

    async def test_get_latest_nonexistent():
        latest = await checkpointer.get_latest("no-such-thread")
        assert latest is None

    async def test_list_thread():
        thread_id = "thread-list-test"
        for i in range(4):
            await checkpointer.save(Checkpoint(
                checkpoint_id=f"list-{i}",
                thread_id=thread_id,
                state={"i": i},
                next_nodes=[],
                step_index=i,
            ))
        items = await checkpointer.list_thread(thread_id)
        assert len(items) == 4, f"expected 4, got {len(items)}"
        # Should be ordered by step_index
        for idx, item in enumerate(items):
            assert item.step_index == idx

    async def test_list_empty_thread():
        items = await checkpointer.list_thread("empty-thread-xyz")
        assert items == []

    async def test_overwrite():
        cp = Checkpoint(
            checkpoint_id="overwrite-1",
            thread_id="thread-overwrite",
            state={"v": "original"},
            next_nodes=["a"],
            step_index=0,
        )
        await checkpointer.save(cp)
        cp2 = Checkpoint(
            checkpoint_id="overwrite-1",
            thread_id="thread-overwrite",
            state={"v": "updated"},
            next_nodes=["b"],
            step_index=0,
        )
        await checkpointer.save(cp2)
        loaded = await checkpointer.get("overwrite-1")
        assert loaded is not None
        assert loaded.state == {"v": "updated"}

    async def test_metadata():
        cp = Checkpoint(
            checkpoint_id="meta-1",
            thread_id="thread-meta",
            state={"x": 1},
            next_nodes=[],
            step_index=0,
            metadata={"source": "test", "custom_key": 42},
        )
        await checkpointer.save(cp)
        loaded = await checkpointer.get("meta-1")
        assert loaded is not None
        assert loaded.metadata.get("source") == "test"
        assert loaded.metadata.get("custom_key") == 42

    async def test_thread_isolation():
        await checkpointer.save(Checkpoint(
            checkpoint_id="iso-a",
            thread_id="thread-iso-a",
            state={"from": "a"},
            next_nodes=[],
            step_index=0,
        ))
        await checkpointer.save(Checkpoint(
            checkpoint_id="iso-b",
            thread_id="thread-iso-b",
            state={"from": "b"},
            next_nodes=[],
            step_index=0,
        ))
        items_a = await checkpointer.list_thread("thread-iso-a")
        items_b = await checkpointer.list_thread("thread-iso-b")
        assert len(items_a) == 1
        assert len(items_b) == 1
        assert items_a[0].state["from"] == "a"
        assert items_b[0].state["from"] == "b"

    async def test_complex_state():
        state = {
            "messages": [{"role": "user", "content": "hello"}],
            "counter": 42,
            "nested": {"a": [1, 2, 3], "b": {"c": True}},
            "empty_list": [],
            "none_value": None,
        }
        await checkpointer.save(Checkpoint(
            checkpoint_id="complex-1",
            thread_id="thread-complex",
            state=state,
            next_nodes=["next"],
            step_index=5,
        ))
        loaded = await checkpointer.get("complex-1")
        assert loaded is not None
        assert loaded.state == state

    # Run all tests
    tests = [
        ("save_and_get", test_save_and_get),
        ("get_nonexistent", test_get_nonexistent),
        ("get_latest", test_get_latest),
        ("get_latest_nonexistent", test_get_latest_nonexistent),
        ("list_thread", test_list_thread),
        ("list_empty_thread", test_list_empty_thread),
        ("overwrite", test_overwrite),
        ("metadata", test_metadata),
        ("thread_isolation", test_thread_isolation),
        ("complex_state", test_complex_state),
    ]

    for name, fn in tests:
        report.results.append(await _run_test(name, fn))

    return report
