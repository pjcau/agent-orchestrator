"""Workspace verification gate — checks a produced workdir before declaring success.

Distinct from `core.skill.verification_middleware` which validates a single
SkillResult. This module operates one level up: after a multi-agent team run
has dumped files into a workdir, the gate runs a chain of pluggable verifiers
(syntax, dependency resolution, encoding sanity, build, smoke test) and
returns a `VerificationReport`. The RepairLoop (sibling module) uses the
report to drive automatic retries.

Design lives in `docs/architecture-repair-loop.md`. Pure harness — no
dashboard or integrations imports.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

EmitEvent = Callable[[str, dict[str, Any]], None] | None


@dataclass(frozen=True)
class VerifierFailure:
    """One verifier-reported problem in the workspace."""

    verifier: str
    severity: str  # "error" | "warning"
    category: str  # "py_syntax" | "pypi_resolve" | "json_escape" | …
    message: str
    detail: str = ""
    file: str | None = None
    exit_code: int | None = None

    @property
    def signature(self) -> str:
        """Stable hash used for cross-attempt dedup."""
        normalized = _normalize_message(self.message)
        raw = f"{self.category}|{normalized}|{self.file or ''}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class VerificationReport:
    passed: bool
    failures: tuple[VerifierFailure, ...]
    duration_ms: int
    verifier_timings: tuple[tuple[str, int], ...] = field(default_factory=tuple)

    def signature_set(self) -> frozenset[str]:
        return frozenset(f.signature for f in self.failures)

    def top(self, n: int = 3) -> tuple[VerifierFailure, ...]:
        return self.failures[:n]


class WorkspaceVerifier(Protocol):
    """Runs one category of check against `workdir` and returns failures."""

    name: str
    cost_estimate_s: float

    async def verify(self, workdir: Path) -> Sequence[VerifierFailure]: ...


class VerificationGate:
    """Runs a chain of verifiers in cost order, optionally fail-fast.

    Usage::

        gate = VerificationGate([SyntaxVerifier(), DependencyVerifier(), ...])
        report = await gate.verify(workdir)
        if not report.passed:
            ...

    `fail_fast=True` (default) stops at the first verifier that produces
    error-severity failures so a 1s syntax error doesn't trigger a 30s
    docker build. Set `fail_fast=False` to collect everything (useful when
    surfacing a full report to the user).
    """

    def __init__(
        self,
        verifiers: Sequence[WorkspaceVerifier],
        *,
        fail_fast: bool = True,
        emit_event: EmitEvent = None,
    ) -> None:
        self._verifiers = sorted(verifiers, key=lambda v: v.cost_estimate_s)
        self._fail_fast = fail_fast
        self._emit = emit_event

    @property
    def verifiers(self) -> tuple[WorkspaceVerifier, ...]:
        return tuple(self._verifiers)

    async def verify(self, workdir: Path) -> VerificationReport:
        t0 = time.perf_counter()
        self._emit_event(
            "verification.started",
            {"workdir": str(workdir), "verifier_count": len(self._verifiers)},
        )

        all_failures: list[VerifierFailure] = []
        timings: list[tuple[str, int]] = []
        for v in self._verifiers:
            t_v = time.perf_counter()
            self._emit_event("verifier.started", {"name": v.name})
            try:
                failures = list(await v.verify(workdir))
            except Exception as exc:  # noqa: BLE001 — verifier crash is itself a failure
                failures = [
                    VerifierFailure(
                        verifier=v.name,
                        severity="error",
                        category="verifier_crash",
                        message=f"verifier {v.name} crashed: {type(exc).__name__}",
                        detail=str(exc)[:4096],
                    )
                ]
            dur = int((time.perf_counter() - t_v) * 1000)
            timings.append((v.name, dur))
            error_count = sum(1 for f in failures if f.severity == "error")
            self._emit_event(
                "verifier.finished",
                {
                    "name": v.name,
                    "passed": error_count == 0,
                    "duration_ms": dur,
                    "failure_count": len(failures),
                },
            )
            all_failures.extend(failures)
            if self._fail_fast and error_count > 0:
                break

        total_dur = int((time.perf_counter() - t0) * 1000)
        passed = not any(f.severity == "error" for f in all_failures)
        report = VerificationReport(
            passed=passed,
            failures=tuple(all_failures),
            duration_ms=total_dur,
            verifier_timings=tuple(timings),
        )
        self._emit_event(
            "verification.finished",
            {
                "passed": passed,
                "total_duration_ms": total_dur,
                "signatures": sorted(report.signature_set()),
            },
        )
        return report

    def _emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        if self._emit is None:
            return
        try:
            self._emit(event_type, data)
        except Exception:  # noqa: BLE001 — never let event emission break verification
            pass


_WHITESPACE_RUN = re.compile(r"\s+")
_NUMBER = re.compile(r"\d+")


def _normalize_message(msg: str) -> str:
    """Collapse whitespace and replace numbers with a placeholder for dedup."""
    msg = _WHITESPACE_RUN.sub(" ", msg.strip())
    msg = _NUMBER.sub("N", msg)
    return msg[:200]
