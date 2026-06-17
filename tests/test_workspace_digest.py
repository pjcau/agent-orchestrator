"""Tests for the cross-turn workspace digest (core.workspace_digest) and its
wiring into the dashboard agent_runner.

The digest is the bounded middle ground between carrying the full transcript
across turns (unbounded growth) and carrying nothing (the agent re-explores the
workspace every turn). It keeps durable facts while iterations stay consecutive
on the same goal, and resets on a pivot.
"""

from typing import AsyncIterator

import pytest

from agent_orchestrator.core.workspace_digest import (
    WorkspaceDigest,
    WorkspaceDigestStore,
    is_followup_goal,
)
from agent_orchestrator.core.provider import (
    Completion,
    ModelCapabilities,
    Provider,
    StreamChunk,
    Usage,
)
from agent_orchestrator.dashboard.agent_runner import get_digest_store, run_agent


# ===== is_followup_goal =====


def test_followup_empty_previous_is_not_followup():
    # Nothing carried yet → treat as fresh (no digest to keep).
    assert is_followup_goal("", "fix the tests") is False
    assert is_followup_goal(None, "fix the tests") is False


def test_followup_explicit_phrase_keeps_digest():
    assert is_followup_goal("make the docker build pass", "still broken") is True
    assert is_followup_goal("fix the frontend tests", "non funziona") is True
    assert is_followup_goal("get setupTests working", "riprova") is True


def test_followup_high_topical_overlap_keeps_digest():
    assert (
        is_followup_goal(
            "fix the failing frontend setupTests configuration",
            "the frontend setupTests configuration is still wrong",
        )
        is True
    )


def test_followup_pivot_resets_digest():
    # Unrelated new goal, no follow-up markers → reset.
    assert (
        is_followup_goal(
            "fix the failing frontend setupTests configuration",
            "write a marketing email about our new pricing",
        )
        is False
    )


def test_followup_empty_new_goal_keeps_digest():
    assert is_followup_goal("fix the tests", "") is True


# ===== note_file =====


def test_note_file_dedup_and_recency():
    d = WorkspaceDigest()
    d.note_file("apps/01/frontend/src/setupTests.js")
    d.note_file("apps/01/frontend/src/setupTests.js")
    assert len(d.layout) == 1
    entry = d.layout["apps/01/frontend/src/setupTests.js"]
    assert entry.hits == 2


def test_note_file_skips_session_and_placeholder_paths():
    d = WorkspaceDigest()
    d.note_file("?")
    d.note_file("jobs/job_abc123def/output.txt")
    d.note_file("/tmp/deadbeef/scratch.txt")
    d.note_file("")
    assert d.is_empty()


# ===== note_command =====


def test_note_command_skips_exploration():
    d = WorkspaceDigest()
    d.note_command("ls -la apps/01", ok=True)
    d.note_command("find . -name setupTests.js", ok=False, reason="nonzero")
    d.note_command("grep -r foo .", ok=True)
    assert not d.commands_ok
    assert not d.commands_bad


def test_note_command_records_ok_and_bad():
    d = WorkspaceDigest()
    d.note_command("npm test", ok=False, reason="shell_timeout")
    d.note_command("CI=true npm test", ok=True)
    assert "npm test" in d.commands_bad
    assert d.commands_bad["npm test"].extra == "shell_timeout"
    assert "CI=true npm test" in d.commands_ok


def test_note_command_strips_env_prefix_for_explore_check():
    # `CI=true find ...` is still an exploration command → skipped.
    d = WorkspaceDigest()
    d.note_command("CI=true find . -name x", ok=True)
    assert not d.commands_ok


def test_command_that_now_works_leaves_the_bad_bucket():
    d = WorkspaceDigest()
    d.note_command("pytest tests/test_x.py", ok=False, reason="exit_code")
    assert "pytest tests/test_x.py" in d.commands_bad
    d.note_command("pytest tests/test_x.py", ok=True)
    assert "pytest tests/test_x.py" not in d.commands_bad
    assert "pytest tests/test_x.py" in d.commands_ok


# ===== update_from_step_log =====


def test_update_from_step_log_parses_all_shapes():
    d = WorkspaceDigest()
    d.update_from_step_log(
        [
            "wrote apps/01/frontend/src/setupTests.js",
            "read apps/01/frontend/package.json",
            "ran: CI=true npm test",
            "ran-failed[shell_timeout]: npm test",
            "truncated, retrying with max_tokens=8000",  # ignored
            "file_read: ok",  # ignored
        ]
    )
    assert "apps/01/frontend/src/setupTests.js" in d.layout
    assert "apps/01/frontend/package.json" in d.layout
    assert "CI=true npm test" in d.commands_ok
    assert "npm test" in d.commands_bad
    assert d.commands_bad["npm test"].extra == "shell_timeout"


# ===== eviction / bounding =====


def test_eviction_keeps_only_most_recent_entries():
    d = WorkspaceDigest(max_entries_per_category=3)
    for i in range(6):
        d.note_file(f"file_{i}.py")
    assert len(d.layout) == 3
    # The three most recently added survive.
    assert set(d.layout) == {"file_3.py", "file_4.py", "file_5.py"}


def test_render_caps_total_chars():
    d = WorkspaceDigest(max_render_chars=120)
    for i in range(12):
        d.note_file(f"some/long/path/to/file_number_{i}.py")
    block = d.render()
    assert len(block) <= 120
    assert block.endswith("</workspace_digest>")


# ===== render =====


def test_render_empty_returns_empty_string():
    assert WorkspaceDigest().render() == ""


def test_summary_counts():
    d = WorkspaceDigest()
    d.note_file("a.py")
    d.note_file("b.py")
    d.note_command("pytest -q", ok=True)
    d.note_command("npm test", ok=False, reason="timeout")
    assert d.summary() == "2 files, 1 ok-cmd, 1 bad-cmd"


def test_render_contains_sections():
    d = WorkspaceDigest()
    d.note_file("src/app.py")
    d.note_command("pytest -q", ok=True)
    d.note_command("npm test", ok=False, reason="timeout")
    block = d.render()
    assert "<workspace_digest>" in block
    assert "src/app.py" in block
    assert "pytest -q" in block
    assert "npm test" in block
    assert "timeout" in block
    assert "FAILED" in block


# ===== reset / serialization =====


def test_reset_clears_everything():
    d = WorkspaceDigest(goal="fix tests")
    d.note_file("a.py")
    d.note_command("pytest", ok=True)
    d.reset()
    assert d.is_empty()
    assert d.goal == ""


def test_to_dict_from_dict_roundtrip():
    d = WorkspaceDigest(goal="fix tests")
    d.note_file("src/app.py")
    d.note_command("npm test", ok=False, reason="timeout")
    restored = WorkspaceDigest.from_dict(d.to_dict())
    assert restored.goal == "fix tests"
    assert "src/app.py" in restored.layout
    assert restored.commands_bad["npm test"].extra == "timeout"
    assert restored.render() == d.render()


# ===== WorkspaceDigestStore =====


def test_store_get_or_create_and_put():
    store = WorkspaceDigestStore()
    assert store.get("c1") is None
    d = store.get_or_create("c1")
    d.note_file("a.py")
    store.put("c1", d)
    assert "a.py" in store.get("c1").layout


def test_store_reset_and_clear():
    store = WorkspaceDigestStore()
    store.get_or_create("c1")
    store.get_or_create("c2")
    store.reset("c1")
    assert store.get("c1") is None
    assert store.get("c2") is not None
    store.clear()
    assert store.get("c2") is None


def test_store_evicts_when_over_capacity():
    store = WorkspaceDigestStore(max_conversations=2)
    store.put("c1", WorkspaceDigest())
    store.put("c2", WorkspaceDigest())
    store.put("c3", WorkspaceDigest())
    assert len(store._digests) <= 2
    assert store.get("c3") is not None


# ===== run_agent wiring =====


class _CaptureProvider(Provider):
    """Provider that records the system prompt of each completion call."""

    def __init__(self):
        self.systems: list[str] = []

    @property
    def model_id(self) -> str:
        return "capture-1"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            max_context=8192,
            supports_tools=True,
            supports_vision=False,
            supports_streaming=False,
        )

    @property
    def input_cost_per_million(self) -> float:
        return 0.0

    @property
    def output_cost_per_million(self) -> float:
        return 0.0

    async def complete(self, messages, tools=None, system=None, **kwargs):
        self.systems.append(system or "")
        return Completion(
            content="done",
            tool_calls=[],
            usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.0),
        )

    async def stream(
        self, messages, tools=None, system=None, **kwargs
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content="done", is_final=True)


@pytest.mark.asyncio
async def test_run_agent_injects_digest_on_followup():
    store = WorkspaceDigestStore()
    digest = WorkspaceDigest(goal="fix the failing frontend setupTests")
    digest.note_file("apps/01/frontend/src/setupTests.js")
    digest.note_command("npm test", ok=False, reason="shell_timeout")
    store.put("conv-1", digest)

    provider = _CaptureProvider()
    await run_agent(
        agent_name="frontend",
        task_description="the frontend setupTests are still failing",
        provider=provider,
        conversation_id="conv-1",
        digest_store=store,
    )
    system_seen = provider.systems[0]
    assert "<workspace_digest>" in system_seen
    assert "apps/01/frontend/src/setupTests.js" in system_seen
    assert "npm test" in system_seen


@pytest.mark.asyncio
async def test_run_agent_resets_digest_on_pivot():
    store = WorkspaceDigestStore()
    digest = WorkspaceDigest(goal="fix the failing frontend setupTests")
    digest.note_file("apps/01/frontend/src/setupTests.js")
    store.put("conv-2", digest)

    provider = _CaptureProvider()
    await run_agent(
        agent_name="content-strategist",
        task_description="write a marketing email about our new pricing tiers",
        provider=provider,
        conversation_id="conv-2",
        digest_store=store,
    )
    assert "<workspace_digest>" not in provider.systems[0]
    # Digest was reset by the pivot.
    assert store.get("conv-2").is_empty() or store.get("conv-2").goal == (
        "write a marketing email about our new pricing tiers"
    )


@pytest.mark.asyncio
async def test_run_agent_updates_digest_goal_after_run():
    store = WorkspaceDigestStore()
    provider = _CaptureProvider()
    await run_agent(
        agent_name="frontend",
        task_description="fix the login form",
        provider=provider,
        conversation_id="conv-3",
        digest_store=store,
    )
    saved = store.get("conv-3")
    assert saved is not None
    assert saved.goal == "fix the login form"


def test_module_level_digest_store_is_shared():
    assert get_digest_store() is get_digest_store()
