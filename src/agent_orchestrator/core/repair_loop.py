"""Workspace repair loop — wraps a team runner with verify-and-retry semantics.

Design lives in `docs/architecture-repair-loop.md`. This module sits one level
above `VerificationGate`: it invokes a user-supplied `team_runner`, asks the
gate to verify the result, and on failure re-invokes the team with a
structured retry prompt up to `max_attempts` (default 5).

Three safety nets:

1. **Hard attempt cap** (`max_attempts`, default 5) — never loops forever.
2. **Hard cost cap** (`max_cost_usd`, default $0.50) — aborts as soon as the
   cumulative cost across attempts exceeds the budget.
3. **Signature memory** — if the same failure signature persists across two
   attempts, the loop escalates (more file context in the next prompt) and,
   if that also fails to break the loop, aborts with status="partial" rather
   than burning the rest of the budget on identical retries.

The repair loop is provider-agnostic — it only knows how to call
`team_runner(task, **kwargs) -> TeamRunResult` and how to ask the gate to
verify. Everything else (event bus, cost tracking) is injected.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from agent_orchestrator.core.verification_gate import (
    VerificationGate,
    VerificationReport,
    VerifierFailure,
)

EmitEvent = Callable[[str, dict[str, Any]], None] | None


class TeamRunResult(Protocol):
    """Minimal protocol the repair loop needs from a team runner result."""

    workdir: Path
    cost_usd: float


@dataclass
class RepairAttempt:
    attempt: int
    task: str
    workdir: Path
    report: VerificationReport
    cost_usd: float
    duration_s: float
    escalated: bool = False
    auto_fixed_signatures: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class RepairResult:
    final_workdir: Path
    final_report: VerificationReport
    attempts: list[RepairAttempt]
    status: Literal["passed", "partial", "aborted_budget", "aborted_cost", "aborted_time"]
    cumulative_cost_usd: float
    cumulative_duration_s: float

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)


TeamRunner = Callable[..., Awaitable[TeamRunResult]]


@dataclass
class _ActionRevertInfo:
    """Pre-action snapshot so RepairLoop can undo a regression."""

    path: str | None
    original_bytes: bytes | None

    def revert(self, workdir: Path) -> None:
        if not self.path:
            return
        target = workdir / self.path
        try:
            if self.original_bytes is None:
                # File didn't exist pre-action → revert == delete.
                if target.exists():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(self.original_bytes)
        except OSError:
            # Best-effort revert; the next gate.verify() will still re-read
            # whatever is on disk now, so a partial revert is still safer
            # than no revert.
            pass


class RepairLoop:
    """Verify-and-retry harness around a team runner.

    Args:
        team_runner: async callable. Must accept a ``task: str`` argument
            and any ``**team_kwargs`` forwarded by the caller. Must return
            an object with ``workdir: Path`` and ``cost_usd: float``.
        gate: a configured `VerificationGate`.
        max_attempts: hard cap on team-run invocations (default 5).
        max_cost_usd: hard cap on cumulative cost (default $0.50).
        max_wall_s: hard cap on cumulative wall-clock seconds across all
            attempts (default 1800 = 30 min). Surfaced as
            ``status="aborted_time"`` + ``repair.aborted{reason: "time"}``.
        emit_event: optional `(event_name, data) -> None` sink.
        pattern_registry: optional Phase-4 registry; if set, the loop calls
            ``registry.apply(failure, workdir)`` before invoking the team and
            skips an attempt when every failure auto-resolves.
    """

    def __init__(
        self,
        *,
        team_runner: TeamRunner,
        gate: VerificationGate,
        max_attempts: int = 5,
        max_cost_usd: float = 0.50,
        max_wall_s: float = 1800.0,
        emit_event: EmitEvent = None,
        pattern_registry: Any | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if max_cost_usd <= 0:
            raise ValueError("max_cost_usd must be > 0")
        if max_wall_s <= 0:
            raise ValueError("max_wall_s must be > 0")
        self._team_runner = team_runner
        self._gate = gate
        self._max_attempts = max_attempts
        self._max_cost = max_cost_usd
        self._max_wall_s = max_wall_s
        self._emit = emit_event
        self._registry = pattern_registry

    async def run(self, task: str, **team_kwargs: Any) -> RepairResult:
        t0 = time.perf_counter()
        attempts: list[RepairAttempt] = []
        signatures_seen: set[str] = set()
        history: list[str] = []
        cumulative_cost = 0.0
        current_task = task

        self._emit_event(
            "repair.started",
            {
                "task": _trunc(task),
                "max_attempts": self._max_attempts,
                "max_cost_usd": self._max_cost,
                "max_wall_s": self._max_wall_s,
            },
        )

        last_report: VerificationReport | None = None
        last_workdir: Path | None = None
        status: Literal["passed", "partial", "aborted_budget", "aborted_cost", "aborted_time"] = (
            "partial"
        )

        for attempt_idx in range(1, self._max_attempts + 1):
            self._emit_event(
                "repair.attempt_started",
                {"attempt": attempt_idx, "signatures_seen": sorted(signatures_seen)},
            )
            t_attempt = time.perf_counter()
            result = await self._team_runner(current_task, **team_kwargs)
            workdir = result.workdir
            cost = float(getattr(result, "cost_usd", 0.0) or 0.0)
            cumulative_cost += cost

            report = await self._gate.verify(workdir)
            last_report = report
            last_workdir = workdir

            # Auto-fix via registry BEFORE escalating to a new LLM attempt.
            # `_try_auto_fix` handles its own re-verify + post-condition guard:
            # if the failure count strictly increases after the fix, all touched
            # files are reverted and the returned signatures list is empty.
            auto_fixed: tuple[str, ...] = ()
            if not report.passed and self._registry is not None:
                auto_fixed, report = await self._try_auto_fix(report, workdir)
                last_report = report

            duration = time.perf_counter() - t_attempt
            escalated = bool(report.signature_set() & signatures_seen)
            attempt = RepairAttempt(
                attempt=attempt_idx,
                task=current_task,
                workdir=workdir,
                report=report,
                cost_usd=cost,
                duration_s=duration,
                escalated=escalated,
                auto_fixed_signatures=auto_fixed,
            )
            attempts.append(attempt)
            self._emit_event(
                "repair.attempt_finished",
                {
                    "attempt": attempt_idx,
                    "passed": report.passed,
                    "cost_delta_usd": cost,
                    "signature_set": sorted(report.signature_set()),
                    "auto_fixed": list(auto_fixed),
                },
            )

            if report.passed:
                status = "passed"
                break

            # Cost guard — abort BEFORE prompting another attempt.
            if cumulative_cost > self._max_cost:
                status = "aborted_cost"
                self._emit_event(
                    "repair.aborted",
                    {"reason": "cost", "cumulative_cost_usd": cumulative_cost},
                )
                break

            # Time guard — same purpose as cost guard but for wall-clock.
            # Surfaces the 2026-05-16(e) failure mode where one iter hung
            # >37 min because the smoke verifier cache kept missing.
            wall_so_far = time.perf_counter() - t0
            if wall_so_far > self._max_wall_s:
                status = "aborted_time"
                self._emit_event(
                    "repair.aborted",
                    {"reason": "time", "wall_s": wall_so_far, "max_wall_s": self._max_wall_s},
                )
                break

            # Escalation: when the same signatures recur, give the next attempt
            # more context. When this is already the last attempt, just exit.
            if attempt_idx >= self._max_attempts:
                status = "partial"
                break

            history.append(_summarise_attempt(attempt))
            signatures_seen |= report.signature_set()
            if escalated:
                self._emit_event(
                    "repair.escalated",
                    {"attempt": attempt_idx, "strategy": "more_context"},
                )
                current_task = augment_task(
                    original=task,
                    failures=list(report.top(3)),
                    attempt=attempt_idx + 1,
                    past_history=history,
                    workdir=workdir,
                    include_file_excerpts=True,
                )
            else:
                current_task = augment_task(
                    original=task,
                    failures=list(report.top(3)),
                    attempt=attempt_idx + 1,
                    past_history=history,
                    workdir=workdir,
                    include_file_excerpts=False,
                )

        cumulative_duration = time.perf_counter() - t0
        assert last_report is not None and last_workdir is not None
        repair_result = RepairResult(
            final_workdir=last_workdir,
            final_report=last_report,
            attempts=attempts,
            status=status,
            cumulative_cost_usd=cumulative_cost,
            cumulative_duration_s=cumulative_duration,
        )
        self._emit_event(
            "repair.finished",
            {
                "status": status,
                "attempts_used": len(attempts),
                "cumulative_cost_usd": cumulative_cost,
            },
        )
        return repair_result

    async def _try_auto_fix(
        self, report: VerificationReport, workdir: Path
    ) -> tuple[tuple[str, ...], VerificationReport]:
        """Apply registry actions, re-verify, revert on regression.

        Returns ``(signatures_fixed, latest_report)``. The contract:

        - Each action is allowed to write file(s) and is expected to expose
          ``RepairAction.changed_path`` + ``RepairAction.original_bytes`` so
          this method can revert if needed.
        - After ALL applied actions, the gate is re-verified ONCE.
        - **Post-condition guard**: if the post-fix report has strictly more
          failures than the pre-fix one, every touched file is restored from
          its snapshot, the signatures list is reset to ``()`` and the
          ORIGINAL report is returned. The repair loop then proceeds as if
          no auto-fix had run — preventing a buggy pattern from compounding.
        - Any exception inside the registry is swallowed (defence in depth).

        Surfaces a ``repair.auto_fixed`` event per applied action and a
        ``repair.auto_fix_reverted`` event when the guard triggers.
        """
        applied: list[tuple[str, _ActionRevertInfo]] = []
        for failure in report.top(3):
            try:
                action = await self._registry.apply(failure, workdir)
            except Exception:  # noqa: BLE001
                action = None
            if action is None:
                continue
            self._emit_event(
                "repair.auto_fixed",
                {
                    "signature": failure.signature,
                    "category": failure.category,
                    "kind": getattr(action, "kind", "unknown"),
                    "file": getattr(action, "file", None),
                    "changed_path": getattr(action, "changed_path", None),
                },
            )
            applied.append(
                (
                    failure.signature,
                    _ActionRevertInfo(
                        path=getattr(action, "changed_path", None),
                        original_bytes=getattr(action, "original_bytes", None),
                    ),
                )
            )

        if not applied:
            return (), report

        new_report = await self._gate.verify(workdir)
        # Post-condition: failure count must not strictly increase.
        before = len(report.failures)
        after = len(new_report.failures)
        if after > before:
            for sig, info in applied:
                info.revert(workdir)
            self._emit_event(
                "repair.auto_fix_reverted",
                {
                    "reason": "failure_count_increased",
                    "before": before,
                    "after": after,
                    "reverted_signatures": [sig for sig, _ in applied],
                },
            )
            # Revert was destructive — re-verify so the reported state matches disk.
            new_report = await self._gate.verify(workdir)
            return (), new_report
        return tuple(sig for sig, _ in applied), new_report

    def _emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        if self._emit is None:
            return
        try:
            self._emit(event_type, data)
        except Exception:  # noqa: BLE001 — never let event emission break the loop
            pass


# ---------------------------------------------------------------------------
# Prompt-augmentation helpers — pure functions, easy to unit-test.
# ---------------------------------------------------------------------------


def augment_task(
    *,
    original: str,
    failures: list[VerifierFailure],
    attempt: int,
    past_history: list[str],
    workdir: Path,
    include_file_excerpts: bool = False,
    max_excerpt_lines: int = 50,
) -> str:
    """Build the retry prompt for the next attempt.

    The prompt is bounded: only the top-3 failures are forwarded, past
    history is summarized, and when `include_file_excerpts` is true we add
    the first 50 lines of each offending file (so the agent can see the
    actual code that triggered the failure).
    """
    parts = [
        original,
        "",
        "─" * 60,
        f"REPAIR ATTEMPT {attempt} — previous output failed verification:",
    ]
    parts.append(_format_failures(failures))
    if past_history:
        parts.append("")
        parts.append("Past attempts that did NOT resolve the issue:")
        for h in past_history:
            parts.append(f"  - {h}")
    if include_file_excerpts:
        parts.append("")
        parts.append("RELEVANT FILE EXCERPTS (the prior fix did not work — examine carefully):")
        for f in failures:
            if not f.file:
                continue
            excerpt = _read_excerpt(workdir / f.file, max_excerpt_lines)
            if excerpt:
                parts.append("")
                parts.append(f"--- {f.file} (first {max_excerpt_lines} lines) ---")
                parts.append(excerpt)
    parts.append("")
    parts.append(
        "Do NOT make any change unrelated to the failures above. Address them "
        "in the order listed. If a failure persists after this turn, the loop "
        "will escalate."
    )
    return "\n".join(parts)


def _format_failures(failures: list[VerifierFailure]) -> str:
    if not failures:
        return "(no failures listed)"
    lines: list[str] = []
    for i, f in enumerate(failures, 1):
        loc = f" [{f.file}]" if f.file else ""
        lines.append(f"{i}. [{f.category}]{loc} {f.message}")
        if f.detail:
            head = f.detail.strip().splitlines()[0][:160]
            lines.append(f"     hint: {head}")
    return "\n".join(lines)


def _summarise_attempt(attempt: RepairAttempt) -> str:
    cats = sorted({f.category for f in attempt.report.failures})
    return (
        f"attempt {attempt.attempt}: cost=${attempt.cost_usd:.4f}, "
        f"duration={attempt.duration_s:.1f}s, failures={len(attempt.report.failures)} "
        f"({', '.join(cats) or 'none'})"
    )


def _read_excerpt(path: Path, max_lines: int) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    lines = text.splitlines()[:max_lines]
    return "\n".join(lines)


def _trunc(s: str, n: int = 200) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 1] + "…"
