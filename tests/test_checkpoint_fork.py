"""Tests for Checkpointer.fork() and get_at_step() (scout findings #31/#40/#43)."""

from __future__ import annotations

import pytest

from agent_orchestrator.core.checkpoint import (
    Checkpoint,
    Checkpointer,
    InMemoryCheckpointer,
    SQLiteCheckpointer,
)


def _cp(thread: str, step: int, value: int) -> Checkpoint:
    return Checkpoint(
        checkpoint_id=f"{thread}-{step}",
        thread_id=thread,
        state={"value": value, "nested": {"step": step}},
        next_nodes=[f"node{step + 1}"],
        step_index=step,
    )


async def _seed(checkpointer: Checkpointer, thread: str = "base", steps: int = 3) -> None:
    for i in range(steps):
        await checkpointer.save(_cp(thread, i, value=i * 10))


@pytest.fixture(params=["memory", "sqlite"])
def checkpointer(request, tmp_path):
    if request.param == "memory":
        yield InMemoryCheckpointer()
    else:
        cp = SQLiteCheckpointer(tmp_path / "ckpt.db")
        yield cp
        cp.close()


class TestGetAtStep:
    @pytest.mark.asyncio
    async def test_returns_exact_step(self, checkpointer):
        await _seed(checkpointer)
        found = await checkpointer.get_at_step("base", 1)
        assert found is not None
        assert found.state["value"] == 10

    @pytest.mark.asyncio
    async def test_missing_step_returns_none(self, checkpointer):
        await _seed(checkpointer)
        assert await checkpointer.get_at_step("base", 99) is None


class TestFork:
    @pytest.mark.asyncio
    async def test_fork_copies_full_history(self, checkpointer):
        await _seed(checkpointer, steps=3)
        head = await checkpointer.fork("base", "branch")
        assert head is not None
        assert head.thread_id == "branch"
        assert head.step_index == 2
        branch_history = await checkpointer.list_thread("branch")
        assert [c.step_index for c in branch_history] == [0, 1, 2]
        assert all(c.metadata["forked_from"] == "base" for c in branch_history)

    @pytest.mark.asyncio
    async def test_fork_at_step_truncates(self, checkpointer):
        await _seed(checkpointer, steps=3)
        head = await checkpointer.fork("base", "branch", at_step=1)
        assert head is not None
        assert head.step_index == 1
        assert len(await checkpointer.list_thread("branch")) == 2

    @pytest.mark.asyncio
    async def test_fork_state_is_deep_copied(self, checkpointer):
        await _seed(checkpointer, steps=1)
        head = await checkpointer.fork("base", "branch")
        assert head is not None
        head.state["nested"]["step"] = 999
        original = await checkpointer.get_latest("base")
        assert original is not None
        assert original.state["nested"]["step"] == 0

    @pytest.mark.asyncio
    async def test_fork_preserves_original_thread(self, checkpointer):
        await _seed(checkpointer, steps=2)
        before = [c.checkpoint_id for c in await checkpointer.list_thread("base")]
        await checkpointer.fork("base", "branch")
        after = [c.checkpoint_id for c in await checkpointer.list_thread("base")]
        assert before == after

    @pytest.mark.asyncio
    async def test_fork_into_existing_thread_rejected(self, checkpointer):
        await _seed(checkpointer, steps=1)
        await checkpointer.save(_cp("occupied", 0, value=1))
        with pytest.raises(ValueError):
            await checkpointer.fork("base", "occupied")

    @pytest.mark.asyncio
    async def test_fork_empty_thread_returns_none(self, checkpointer):
        assert await checkpointer.fork("ghost", "branch") is None

    @pytest.mark.asyncio
    async def test_fork_nonexistent_step_returns_none(self, checkpointer):
        await _seed(checkpointer, steps=2)
        assert await checkpointer.fork("base", "branch", at_step=7) is None

    @pytest.mark.asyncio
    async def test_forked_ids_are_fresh(self, checkpointer):
        await _seed(checkpointer, steps=2)
        await checkpointer.fork("base", "branch")
        base_ids = {c.checkpoint_id for c in await checkpointer.list_thread("base")}
        branch_ids = {c.checkpoint_id for c in await checkpointer.list_thread("branch")}
        assert base_ids.isdisjoint(branch_ids)
