"""Unit tests for `core.repair_loop.RepairLoop`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_orchestrator.core.repair_loop import (
    RepairLoop,
    augment_task,
)
from agent_orchestrator.core.verification_gate import (
    VerificationGate,
    VerifierFailure,
)


@dataclass
class _FakeTeamResult:
    workdir: Path
    cost_usd: float = 0.0


@dataclass
class _FakeVerifier:
    name: str
    cost_estimate_s: float
    results: list[list[VerifierFailure]]
    _call_idx: int = 0

    async def verify(self, workdir: Path):
        idx = min(self._call_idx, len(self.results) - 1)
        out = self.results[idx]
        self._call_idx += 1
        return out


def _failure(category: str = "py_syntax", file: str = "x.py", msg: str = "bad") -> VerifierFailure:
    return VerifierFailure(
        verifier="syntax",
        severity="error",
        category=category,
        message=msg,
        file=file,
    )


# ---------------------------- RepairLoop ----------------------------


@pytest.mark.asyncio
async def test_first_attempt_passes_no_retries(tmp_path: Path):
    calls: list[str] = []

    async def team(task: str, **kw):
        calls.append(task)
        return _FakeTeamResult(workdir=tmp_path, cost_usd=0.01)

    gate = VerificationGate([_FakeVerifier(name="v", cost_estimate_s=1.0, results=[[]])])
    loop = RepairLoop(team_runner=team, gate=gate, max_attempts=5)
    result = await loop.run("build it")

    assert result.status == "passed"
    assert result.attempt_count == 1
    assert calls == ["build it"]  # original task verbatim
    assert pytest.approx(result.cumulative_cost_usd, 0.0001) == 0.01


@pytest.mark.asyncio
async def test_retries_until_pass(tmp_path: Path):
    # attempt 1: fails on syntax; attempt 2: clean
    gate = VerificationGate(
        [
            _FakeVerifier(
                name="v",
                cost_estimate_s=1.0,
                results=[[_failure()], []],
            )
        ]
    )
    n_calls = {"n": 0}

    async def team(task: str, **kw):
        n_calls["n"] += 1
        return _FakeTeamResult(workdir=tmp_path, cost_usd=0.01)

    loop = RepairLoop(team_runner=team, gate=gate, max_attempts=5)
    result = await loop.run("build it")

    assert result.status == "passed"
    assert result.attempt_count == 2
    assert n_calls["n"] == 2
    # Second attempt should have received an augmented prompt.
    assert "REPAIR ATTEMPT 2" in result.attempts[1].task
    assert "bad" in result.attempts[1].task


@pytest.mark.asyncio
async def test_stops_at_max_attempts_with_partial(tmp_path: Path):
    gate = VerificationGate(
        [
            _FakeVerifier(
                name="v",
                cost_estimate_s=1.0,
                results=[[_failure(msg=f"err{i}") for _ in range(1)] for i in range(5)],
            )
        ]
    )

    async def team(task: str, **kw):
        return _FakeTeamResult(workdir=tmp_path, cost_usd=0.01)

    loop = RepairLoop(team_runner=team, gate=gate, max_attempts=3)
    result = await loop.run("never works")

    assert result.status == "partial"
    assert result.attempt_count == 3


@pytest.mark.asyncio
async def test_aborts_on_cost_cap(tmp_path: Path):
    gate = VerificationGate(
        [
            _FakeVerifier(
                name="v",
                cost_estimate_s=1.0,
                results=[[_failure()]] * 10,
            )
        ]
    )

    async def team(task: str, **kw):
        return _FakeTeamResult(workdir=tmp_path, cost_usd=0.40)

    loop = RepairLoop(team_runner=team, gate=gate, max_attempts=10, max_cost_usd=0.50)
    result = await loop.run("expensive")

    # First attempt: cost=0.40 (under cap). Second: cumulative=0.80 → abort.
    assert result.status == "aborted_cost"
    assert result.attempt_count == 2
    assert result.cumulative_cost_usd == pytest.approx(0.80)


@pytest.mark.asyncio
async def test_signature_repeat_triggers_escalation(tmp_path: Path):
    # Same failure two attempts in a row.
    f = _failure(msg="exact same")
    gate = VerificationGate(
        [
            _FakeVerifier(
                name="v",
                cost_estimate_s=1.0,
                results=[[f], [f], []],
            )
        ]
    )
    events: list[tuple[str, dict]] = []

    def emit(t: str, d: dict) -> None:
        events.append((t, d))

    n_calls = {"n": 0}

    async def team(task: str, **kw):
        n_calls["n"] += 1
        return _FakeTeamResult(workdir=tmp_path, cost_usd=0.01)

    loop = RepairLoop(team_runner=team, gate=gate, max_attempts=5, emit_event=emit)
    result = await loop.run("flaky")

    assert result.status == "passed"
    # Escalation should have happened on attempt 2 (same signature as attempt 1).
    escalated = [e for e in events if e[0] == "repair.escalated"]
    assert len(escalated) == 1
    assert escalated[0][1]["strategy"] == "more_context"


@pytest.mark.asyncio
async def test_event_emission_lifecycle(tmp_path: Path):
    gate = VerificationGate([_FakeVerifier(name="v", cost_estimate_s=1.0, results=[[]])])
    events: list[str] = []

    def emit(t: str, d: dict) -> None:
        events.append(t)

    async def team(task: str, **kw):
        return _FakeTeamResult(workdir=tmp_path, cost_usd=0.01)

    loop = RepairLoop(team_runner=team, gate=gate, emit_event=emit)
    await loop.run("hi")

    # Pull out repair-loop-specific events
    repair_events = [e for e in events if e.startswith("repair.")]
    assert repair_events == [
        "repair.started",
        "repair.attempt_started",
        "repair.attempt_finished",
        "repair.finished",
    ]


@pytest.mark.asyncio
async def test_event_emitter_exception_does_not_break_loop(tmp_path: Path):
    gate = VerificationGate([_FakeVerifier(name="v", cost_estimate_s=1.0, results=[[]])])

    def emit(t: str, d: dict) -> None:
        raise RuntimeError("bad sink")

    async def team(task: str, **kw):
        return _FakeTeamResult(workdir=tmp_path, cost_usd=0.01)

    loop = RepairLoop(team_runner=team, gate=gate, emit_event=emit)
    result = await loop.run("hi")
    assert result.status == "passed"


@pytest.mark.asyncio
async def test_pattern_registry_short_circuits_when_all_resolved(tmp_path: Path):
    # Registry that "fixes" everything by returning a non-None action.
    class _AlwaysFix:
        async def apply(self, failure, workdir):
            return type("Action", (), {"kind": "noop", "file": failure.file})()

    f = _failure()
    gate = VerificationGate(
        [
            _FakeVerifier(
                name="v",
                cost_estimate_s=1.0,
                # Attempt 1 surfaces a failure. After registry "fixes" it,
                # the gate is called a second time within the same attempt
                # and must return empty.
                results=[[f], [], [], []],
            )
        ]
    )
    n_calls = {"n": 0}

    async def team(task: str, **kw):
        n_calls["n"] += 1
        return _FakeTeamResult(workdir=tmp_path, cost_usd=0.05)

    events: list[tuple[str, dict]] = []
    loop = RepairLoop(
        team_runner=team,
        gate=gate,
        pattern_registry=_AlwaysFix(),
        emit_event=lambda t, d: events.append((t, d)),
    )
    result = await loop.run("with registry")

    assert result.status == "passed"
    assert n_calls["n"] == 1  # only one team invocation needed
    fixed_events = [e for e in events if e[0] == "repair.auto_fixed"]
    assert len(fixed_events) == 1


@pytest.mark.asyncio
async def test_pattern_registry_exception_swallowed(tmp_path: Path):
    class _Crashy:
        async def apply(self, failure, workdir):
            raise RuntimeError("oops")

    f = _failure()
    gate = VerificationGate([_FakeVerifier(name="v", cost_estimate_s=1.0, results=[[f], []])])

    async def team(task: str, **kw):
        return _FakeTeamResult(workdir=tmp_path, cost_usd=0.01)

    loop = RepairLoop(team_runner=team, gate=gate, pattern_registry=_Crashy())
    result = await loop.run("survives")
    # Registry crashed → no auto-fix → standard retry → passes on attempt 2.
    assert result.status == "passed"
    assert result.attempt_count == 2


def test_invalid_max_attempts_rejected():
    with pytest.raises(ValueError):
        RepairLoop(
            team_runner=lambda *a, **kw: None,  # type: ignore[arg-type]
            gate=VerificationGate([]),
            max_attempts=0,
        )


def test_invalid_max_cost_rejected():
    with pytest.raises(ValueError):
        RepairLoop(
            team_runner=lambda *a, **kw: None,  # type: ignore[arg-type]
            gate=VerificationGate([]),
            max_cost_usd=0,
        )


# ---------------------------- augment_task ----------------------------


def test_augment_task_top3_only(tmp_path: Path):
    failures = [_failure(msg=f"err{i}") for i in range(10)]
    out = augment_task(
        original="root",
        failures=failures[:3],
        attempt=2,
        past_history=[],
        workdir=tmp_path,
    )
    assert "REPAIR ATTEMPT 2" in out
    assert "err0" in out and "err1" in out and "err2" in out
    assert "err3" not in out


def test_augment_task_includes_history(tmp_path: Path):
    out = augment_task(
        original="root",
        failures=[_failure()],
        attempt=3,
        past_history=["attempt 1: cost=$0.01", "attempt 2: cost=$0.02"],
        workdir=tmp_path,
    )
    assert "Past attempts that did NOT resolve" in out
    assert "attempt 1" in out
    assert "attempt 2" in out


def test_augment_task_file_excerpts(tmp_path: Path):
    src = "\n".join(f"line {i}" for i in range(80))
    (tmp_path / "main.py").write_text(src)
    out = augment_task(
        original="root",
        failures=[_failure(file="main.py", msg="boom")],
        attempt=4,
        past_history=[],
        workdir=tmp_path,
        include_file_excerpts=True,
        max_excerpt_lines=10,
    )
    assert "first 10 lines" in out
    assert "line 0" in out and "line 9" in out
    assert "line 10" not in out


def test_augment_task_excerpt_missing_file_is_silent(tmp_path: Path):
    out = augment_task(
        original="root",
        failures=[_failure(file="ghost.py")],
        attempt=2,
        past_history=[],
        workdir=tmp_path,
        include_file_excerpts=True,
    )
    # No crash, no excerpt section since the file doesn't exist.
    assert "ghost.py (first" not in out


def test_augment_task_no_failures_renders_safe(tmp_path: Path):
    out = augment_task(
        original="root",
        failures=[],
        attempt=2,
        past_history=[],
        workdir=tmp_path,
    )
    assert "(no failures listed)" in out


# ---------------------- Post-condition guard (Phase 7.7) ----------------------


@pytest.mark.asyncio
async def test_post_condition_guard_reverts_when_failure_count_increases(tmp_path: Path):
    """If an auto-fix makes the report STRICTLY worse, the loop must revert
    the touched file(s) and proceed as if no fix had run."""
    from agent_orchestrator.core.failure_patterns import (
        FailurePattern,
        FailurePatternRegistry,
        RepairAction,
    )

    # 1) Set up a workdir with a known initial state.
    target = tmp_path / "requirements.txt"
    target.write_text("fastapi>=0.109\n")

    # 2) Build a fake registry whose action makes things worse.
    import re as _re

    BAD_PATTERN = FailurePattern(
        name="evil",
        category="missing_dep",
        pattern=_re.compile(r"."),
        action_type="pip_pin_repair",  # any string, we replace the handler below
        action_params={},
    )

    async def evil_apply(self, failure, workdir):
        # Snapshot + write a destructive change.
        original_bytes = target.read_bytes()
        target.write_text("not-a-real-package-that-pip-cannot-resolve\n")
        return RepairAction(
            kind="file_rewrite",
            file="requirements.txt",
            new_content="not-a-real-package",
            explanation="evil",
            changed_path="requirements.txt",
            original_bytes=original_bytes,
        )

    registry = FailurePatternRegistry([BAD_PATTERN])
    registry.apply = evil_apply.__get__(registry, FailurePatternRegistry)

    # 3) A gate that returns MORE failures after the fix.
    initial = [_failure("missing_dep", file="x.py", msg="No module named 'X'")]
    after = [
        _failure("missing_dep", file="x.py", msg="No module named 'X'"),
        _failure("missing_dep", file="y.py", msg="No module named 'Y'"),
    ]

    @dataclass
    class _PoliceGate:
        verifiers: list = None
        call: int = 0

        async def verify(self, workdir):
            self.call += 1
            from agent_orchestrator.core.verification_gate import VerificationReport

            failures = initial if self.call == 1 else (after if self.call == 2 else initial)
            return VerificationReport(
                passed=False,
                failures=tuple(failures),
                duration_ms=1,
            )

    gate = _PoliceGate()

    # 4) Drive _try_auto_fix directly.
    loop = RepairLoop(
        team_runner=lambda *a, **kw: None,  # never called
        gate=gate,
        pattern_registry=registry,
        max_attempts=1,
    )
    report_before = await gate.verify(tmp_path)
    fixed, new_report = await loop._try_auto_fix(report_before, tmp_path)

    # Guard fired: signatures returned empty, original file content restored.
    assert fixed == ()
    assert target.read_text() == "fastapi>=0.109\n"


@pytest.mark.asyncio
async def test_post_condition_guard_does_not_revert_when_fix_helps(tmp_path: Path):
    """Sanity: when the fix REDUCES failures, the action stands."""
    from agent_orchestrator.core.failure_patterns import (
        FailurePattern,
        FailurePatternRegistry,
        RepairAction,
    )

    target = tmp_path / "requirements.txt"
    target.write_text("fastapi>=0.109\n")
    target.read_text()

    import re as _re

    PATTERN = FailurePattern(
        name="helpful",
        category="missing_dep",
        pattern=_re.compile(r"."),
        action_type="pip_pin_repair",
        action_params={},
    )

    async def helpful_apply(self, failure, workdir):
        original_bytes = target.read_bytes()
        target.write_text("fastapi>=0.109\npasslib\n")
        return RepairAction(
            kind="file_rewrite",
            file="x.py",
            new_content="fastapi>=0.109\npasslib\n",
            explanation="helpful",
            changed_path="requirements.txt",
            original_bytes=original_bytes,
        )

    registry = FailurePatternRegistry([PATTERN])
    registry.apply = helpful_apply.__get__(registry, FailurePatternRegistry)

    @dataclass
    class _GoodGate:
        verifiers: list = None
        call: int = 0

        async def verify(self, workdir):
            self.call += 1
            from agent_orchestrator.core.verification_gate import VerificationReport

            if self.call == 1:
                return VerificationReport(
                    passed=False,
                    failures=(
                        _failure("missing_dep", file="x.py", msg="No module named 'passlib'"),
                    ),
                    duration_ms=1,
                )
            return VerificationReport(passed=True, failures=(), duration_ms=1)

    gate = _GoodGate()
    loop = RepairLoop(
        team_runner=lambda *a, **kw: None, gate=gate, pattern_registry=registry, max_attempts=1
    )
    report_before = await gate.verify(tmp_path)
    fixed, new_report = await loop._try_auto_fix(report_before, tmp_path)

    assert len(fixed) == 1
    assert new_report.passed is True
    # File kept its fixed content (not reverted).
    assert target.read_text() == "fastapi>=0.109\npasslib\n"


# ---------------------- max_wall_s (Phase 7.9a) ----------------------


@pytest.mark.asyncio
async def test_repair_loop_aborts_on_wall_clock_cap(tmp_path: Path):
    """A slow team_runner should trigger status='aborted_time' before
    exhausting max_attempts."""
    import asyncio as _asyncio

    async def slow_team(task: str, **kw):
        await _asyncio.sleep(0.3)  # well above max_wall_s
        return _FakeTeamResult(workdir=tmp_path, cost_usd=0.001)

    failing_v = _FakeVerifier(name="x", cost_estimate_s=0.0, results=[[_failure()]])
    gate = VerificationGate([failing_v])
    loop = RepairLoop(
        team_runner=slow_team,
        gate=gate,
        max_attempts=5,
        max_cost_usd=10.0,
        max_wall_s=0.2,
    )
    result = await loop.run("task")
    assert result.status == "aborted_time"
    # Should have tried at least once but well under max_attempts.
    assert 1 <= result.attempt_count <= 3


@pytest.mark.asyncio
async def test_repair_loop_max_wall_s_validates_positive():
    failing_v = _FakeVerifier(name="x", cost_estimate_s=0.0, results=[[_failure()]])
    gate = VerificationGate([failing_v])
    with pytest.raises(ValueError, match="max_wall_s"):
        RepairLoop(team_runner=lambda *a, **kw: None, gate=gate, max_wall_s=0)
