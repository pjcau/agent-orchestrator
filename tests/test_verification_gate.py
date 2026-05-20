"""Unit tests for `core.verification_gate.VerificationGate`."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_orchestrator.core.verification_gate import (
    VerificationGate,
    VerificationReport,
    VerifierFailure,
)


@dataclass
class _FakeVerifier:
    name: str
    cost_estimate_s: float
    result: list[VerifierFailure]
    crashes: bool = False

    async def verify(self, workdir: Path):
        if self.crashes:
            raise RuntimeError("boom")
        return self.result


@pytest.mark.asyncio
async def test_gate_passes_when_all_verifiers_pass(tmp_path):
    gate = VerificationGate(
        [
            _FakeVerifier(name="syntax", cost_estimate_s=1.0, result=[]),
            _FakeVerifier(name="deps", cost_estimate_s=2.0, result=[]),
        ]
    )
    report = await gate.verify(tmp_path)
    assert report.passed
    assert report.failures == ()
    assert {name for name, _ in report.verifier_timings} == {"syntax", "deps"}


@pytest.mark.asyncio
async def test_gate_fail_fast_stops_after_first_error(tmp_path):
    deps = _FakeVerifier(name="deps", cost_estimate_s=2.0, result=[])
    gate = VerificationGate(
        [
            _FakeVerifier(
                name="syntax",
                cost_estimate_s=1.0,
                result=[
                    VerifierFailure(
                        verifier="syntax",
                        severity="error",
                        category="py_syntax",
                        message="bad",
                    )
                ],
            ),
            deps,
        ],
        fail_fast=True,
    )
    report = await gate.verify(tmp_path)
    assert not report.passed
    # Only syntax should have run.
    assert [name for name, _ in report.verifier_timings] == ["syntax"]
    assert len(report.failures) == 1


@pytest.mark.asyncio
async def test_gate_no_fail_fast_collects_all(tmp_path):
    gate = VerificationGate(
        [
            _FakeVerifier(
                name="syntax",
                cost_estimate_s=1.0,
                result=[
                    VerifierFailure(
                        verifier="syntax",
                        severity="error",
                        category="py_syntax",
                        message="bad",
                    )
                ],
            ),
            _FakeVerifier(
                name="deps",
                cost_estimate_s=2.0,
                result=[
                    VerifierFailure(
                        verifier="deps",
                        severity="error",
                        category="pypi_resolve",
                        message="missing",
                    )
                ],
            ),
        ],
        fail_fast=False,
    )
    report = await gate.verify(tmp_path)
    assert not report.passed
    assert len(report.failures) == 2
    assert [name for name, _ in report.verifier_timings] == ["syntax", "deps"]


@pytest.mark.asyncio
async def test_warning_does_not_trigger_fail_fast(tmp_path):
    gate = VerificationGate(
        [
            _FakeVerifier(
                name="syntax",
                cost_estimate_s=1.0,
                result=[
                    VerifierFailure(
                        verifier="syntax",
                        severity="warning",
                        category="unreadable",
                        message="meh",
                    )
                ],
            ),
            _FakeVerifier(name="deps", cost_estimate_s=2.0, result=[]),
        ],
        fail_fast=True,
    )
    report = await gate.verify(tmp_path)
    assert report.passed  # warnings don't fail
    assert {name for name, _ in report.verifier_timings} == {"syntax", "deps"}


@pytest.mark.asyncio
async def test_verifier_crash_becomes_a_failure_not_a_throw(tmp_path):
    gate = VerificationGate(
        [_FakeVerifier(name="crashy", cost_estimate_s=1.0, result=[], crashes=True)]
    )
    report = await gate.verify(tmp_path)
    assert not report.passed
    assert len(report.failures) == 1
    f = report.failures[0]
    assert f.category == "verifier_crash"
    assert f.verifier == "crashy"


@pytest.mark.asyncio
async def test_verifiers_run_in_cost_order(tmp_path):
    seen: list[str] = []

    @dataclass
    class _Tracker:
        name: str
        cost_estimate_s: float

        async def verify(self, workdir):
            seen.append(self.name)
            return []

    gate = VerificationGate(
        [
            _Tracker(name="expensive", cost_estimate_s=10.0),
            _Tracker(name="cheap", cost_estimate_s=0.1),
            _Tracker(name="medium", cost_estimate_s=2.0),
        ]
    )
    await gate.verify(tmp_path)
    assert seen == ["cheap", "medium", "expensive"]


@pytest.mark.asyncio
async def test_events_emitted_in_order(tmp_path):
    events: list[tuple[str, dict]] = []

    def emit(t: str, d: dict) -> None:
        events.append((t, d))

    gate = VerificationGate(
        [_FakeVerifier(name="syntax", cost_estimate_s=1.0, result=[])],
        emit_event=emit,
    )
    await gate.verify(tmp_path)
    names = [t for t, _ in events]
    assert names == [
        "verification.started",
        "verifier.started",
        "verifier.finished",
        "verification.finished",
    ]


@pytest.mark.asyncio
async def test_verifier_finished_event_carries_duration_ms(tmp_path):
    """Phase 7.9b: per-verifier timing is observable on the bus so the
    dashboard can flag a slow verifier (e.g. the smoke tier on a cache miss)."""
    events: list[tuple[str, dict]] = []

    def emit(t: str, d: dict) -> None:
        events.append((t, d))

    gate = VerificationGate(
        [_FakeVerifier(name="syntax", cost_estimate_s=1.0, result=[])],
        emit_event=emit,
    )
    await gate.verify(tmp_path)
    finished = [d for t, d in events if t == "verifier.finished"]
    assert len(finished) == 1
    payload = finished[0]
    assert payload["name"] == "syntax"
    assert isinstance(payload["duration_ms"], int)
    assert payload["duration_ms"] >= 0
    assert payload["passed"] is True
    assert payload["failure_count"] == 0


@pytest.mark.asyncio
async def test_event_emitter_exception_does_not_break_verify(tmp_path):
    def emit(t: str, d: dict) -> None:
        raise RuntimeError("bad sink")

    gate = VerificationGate(
        [_FakeVerifier(name="syntax", cost_estimate_s=1.0, result=[])],
        emit_event=emit,
    )
    report = await gate.verify(tmp_path)
    assert report.passed  # no propagation


def test_signature_is_stable_and_deduplicates_numbers():
    a = VerifierFailure(
        verifier="syntax",
        severity="error",
        category="py_syntax",
        message="foo.py: invalid syntax (line 42)",
        file="foo.py",
    )
    b = VerifierFailure(
        verifier="syntax",
        severity="error",
        category="py_syntax",
        message="foo.py: invalid syntax (line 99)",
        file="foo.py",
    )
    assert a.signature == b.signature  # line numbers normalized


def test_signature_set_and_top_helpers():
    failures = tuple(
        VerifierFailure(
            verifier="x",
            severity="error",
            category=f"c{i}",
            message=f"m{i}",
        )
        for i in range(5)
    )
    report = VerificationReport(passed=False, failures=failures, duration_ms=10)
    assert len(report.signature_set()) == 5
    assert len(report.top(3)) == 3


@pytest.mark.asyncio
async def test_empty_verifier_list_passes(tmp_path):
    gate = VerificationGate([])
    report = await gate.verify(tmp_path)
    assert report.passed
    assert report.failures == ()
    assert report.verifier_timings == ()


def test_asyncio_smoke():
    # Sanity check that the gate can be driven without pytest-asyncio in
    # downstream consumers that prefer asyncio.run().
    gate = VerificationGate(
        [_FakeVerifier(name="syntax", cost_estimate_s=1.0, result=[])]
    )
    report = asyncio.run(gate.verify(Path("/")))
    assert report.passed
