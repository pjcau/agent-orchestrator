"""Tests for conformance test suites (v1.1 Sprint 1)."""

import asyncio

from agent_orchestrator.core.conformance import (
    run_checkpointer_conformance,
    ConformanceReport,
    TestResult,
    TestStatus,
)
from agent_orchestrator.core.checkpoint import InMemoryCheckpointer


class TestConformanceReport:
    def test_counts(self):
        report = ConformanceReport(
            suite="Test",
            implementation="Mock",
            results=[
                TestResult("a", TestStatus.PASSED),
                TestResult("b", TestStatus.PASSED),
                TestResult("c", TestStatus.FAILED, error="oops"),
                TestResult("d", TestStatus.SKIPPED),
            ],
        )
        assert report.passed == 2
        assert report.failed == 1
        assert report.skipped == 1
        assert not report.all_passed

    def test_all_passed(self):
        report = ConformanceReport(
            suite="Test",
            implementation="Mock",
            results=[TestResult("a", TestStatus.PASSED)],
        )
        assert report.all_passed

    def test_summary(self):
        report = ConformanceReport(
            suite="Provider",
            implementation="TestProvider",
            results=[TestResult("a", TestStatus.PASSED)],
        )
        s = report.summary()
        assert "Provider" in s
        assert "TestProvider" in s
        assert "1/1 passed" in s

    def test_to_dict(self):
        report = ConformanceReport(
            suite="Test",
            implementation="Impl",
            results=[TestResult("t1", TestStatus.PASSED)],
        )
        d = report.to_dict()
        assert d["suite"] == "Test"
        assert d["all_passed"] is True
        assert len(d["results"]) == 1


class TestCheckpointerConformance:
    def test_inmemory_passes_all(self):
        checkpointer = InMemoryCheckpointer()
        report = asyncio.run(run_checkpointer_conformance(checkpointer))
        assert report.all_passed, (
            f"InMemoryCheckpointer failed conformance: "
            f"{[r.name + ': ' + (r.error or '') for r in report.results if r.status == TestStatus.FAILED]}"
        )
        assert report.passed == 10
        assert report.failed == 0
