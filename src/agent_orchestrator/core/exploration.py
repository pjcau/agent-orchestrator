"""Parallel branch exploration (scout finding #60).

Fork a checkpointed thread into N branches, run each branch concurrently —
optionally each inside its own pooled sandbox — and pick a winner. Builds
directly on :meth:`Checkpointer.fork` (findings #31/#40/#43) and
:class:`SandboxPool` (finding #14).

Usage::

    async def try_strategy(ctx: BranchContext) -> str:
        # ctx.thread_id is a private fork of the base thread's history;
        # ctx.sandbox (when a pool is given) is a private warm sandbox.
        ...

    outcome = await explore(
        checkpointer,
        base_thread_id="main",
        branches={"greedy": try_strategy, "cautious": try_strategy},
        scorer=lambda output: len(output),
    )
    outcome.winner  # ExplorationResult of the best branch
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .checkpoint import Checkpoint, Checkpointer
from .sandbox import Sandbox, SandboxPool


@dataclass
class BranchContext:
    """What a branch callable gets to work with."""

    branch_id: str
    thread_id: str
    head: Checkpoint
    sandbox: Sandbox | None = None


@dataclass
class ExplorationResult:
    """Outcome of one branch."""

    branch_id: str
    thread_id: str
    output: Any = None
    score: float = 0.0
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None


@dataclass
class ExplorationOutcome:
    """Outcome of the whole exploration."""

    winner: ExplorationResult | None
    results: list[ExplorationResult] = field(default_factory=list)


async def explore(
    checkpointer: Checkpointer,
    base_thread_id: str,
    branches: dict[str, Callable[[BranchContext], Awaitable[Any]]],
    scorer: Callable[[Any], float] | None = None,
    at_step: int | None = None,
    sandbox_pool: SandboxPool | None = None,
    max_parallel: int = 4,
) -> ExplorationOutcome:
    """Run every branch on its own fork of *base_thread_id*, return all results.

    Each branch gets a forked thread named ``{base}:{branch_id}`` (never the
    original — the base thread's history is untouched) and, when a pool is
    provided, a pooled sandbox that is always released back. Branch failures
    are isolated into their :class:`ExplorationResult`.

    The winner is the successful branch with the highest ``scorer(output)``
    (ties: declaration order); without a scorer, the first successful branch
    in declaration order wins. ``winner`` is None when every branch failed —
    including when the base thread has no checkpoint to fork.
    """
    if not branches:
        raise ValueError("explore() needs at least one branch")
    if max_parallel < 1:
        raise ValueError("max_parallel must be >= 1")

    semaphore = asyncio.Semaphore(max_parallel)

    async def _run(branch_id: str, fn) -> ExplorationResult:
        thread_id = f"{base_thread_id}:{branch_id}"
        async with semaphore:
            try:
                head = await checkpointer.fork(base_thread_id, thread_id, at_step=at_step)
            except ValueError as exc:
                return ExplorationResult(branch_id, thread_id, error=str(exc))
            if head is None:
                return ExplorationResult(
                    branch_id, thread_id, error="Base thread has no checkpoint to fork"
                )
            sandbox: Sandbox | None = None
            try:
                if sandbox_pool is not None:
                    sandbox = await sandbox_pool.acquire()
                context = BranchContext(
                    branch_id=branch_id, thread_id=thread_id, head=head, sandbox=sandbox
                )
                output = await fn(context)
                score = scorer(output) if scorer else 0.0
                return ExplorationResult(branch_id, thread_id, output=output, score=score)
            except Exception as exc:
                return ExplorationResult(branch_id, thread_id, error=str(exc))
            finally:
                if sandbox is not None and sandbox_pool is not None:
                    await sandbox_pool.release(sandbox)

    results = await asyncio.gather(*(_run(bid, fn) for bid, fn in branches.items()))
    results = list(results)

    successes = [r for r in results if r.success]
    winner: ExplorationResult | None = None
    if successes:
        winner = max(successes, key=lambda r: r.score) if scorer else successes[0]
    return ExplorationOutcome(winner=winner, results=results)
