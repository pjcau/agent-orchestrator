"""Tests for Phase 2: verification gate, atomic decomp, context loader,
hierarchical namespaces, verbatim checkpoint log (PRs #59, #61, #81)."""

from agent_orchestrator.core.atomic_tasks import (
    validate_atomic_tasks,
)
from agent_orchestrator.core.checkpoint import (
    Checkpoint,
    InMemoryCheckpointer,
    SQLiteCheckpointer,
)
from agent_orchestrator.core.metrics import MetricsRegistry
from agent_orchestrator.core.skill import (
    SkillRegistry,
    SkillResult,
    context_loader_middleware,
    verification_middleware,
)
from agent_orchestrator.core.store import (
    InMemoryStore,
    descends_from,
    namespace_depth,
    namespace_to_path,
    path_to_namespace,
)


# ═══════════════════════════════════════════════════════════════════════
# PR #59 — Verification Gate Middleware
# ═══════════════════════════════════════════════════════════════════════


class _StubSkill:
    name = "stub"
    description = ""
    parameters = {"type": "object"}

    def __init__(self, output: str) -> None:
        self._output = output

    @property
    def category(self) -> str:
        return "general"

    @property
    def full_instructions(self) -> str | None:
        return None

    async def execute(self, params: dict) -> SkillResult:
        return SkillResult(success=True, output=self._output)


class TestVerificationMiddleware:
    async def test_validator_passes_result_through(self):
        reg = SkillRegistry()
        reg.register(_StubSkill("GOOD"))
        reg.use(verification_middleware({"stub": lambda r: True}))
        result = await reg.execute("stub", {})
        assert result.success
        assert result.output == "GOOD"

    async def test_validator_false_converts_to_error(self):
        reg = SkillRegistry()
        reg.register(_StubSkill("BAD"))
        reg.use(verification_middleware({"stub": lambda r: False}))
        result = await reg.execute("stub", {})
        assert not result.success
        assert "verification" in (result.error or "").lower()

    async def test_validator_tuple_false_includes_reason(self):
        reg = SkillRegistry()
        reg.register(_StubSkill("x"))
        reg.use(verification_middleware({"stub": lambda r: (False, "output too short")}))
        result = await reg.execute("stub", {})
        assert "output too short" in (result.error or "")

    async def test_skills_without_validator_unaffected(self):
        reg = SkillRegistry()
        reg.register(_StubSkill("OK"))
        reg.use(verification_middleware({"other-skill": lambda r: False}))
        result = await reg.execute("stub", {})
        assert result.success

    async def test_metrics_recorded(self):
        metrics = MetricsRegistry()
        reg = SkillRegistry()
        reg.register(_StubSkill("x"))
        reg.use(verification_middleware({"stub": lambda r: True}, metrics=metrics))
        await reg.execute("stub", {})

        total = metrics.counter("verification_total", "", labels={"skill": "stub"}).get()
        passes = metrics.counter("verification_pass_total", "", labels={"skill": "stub"}).get()
        assert total == 1
        assert passes == 1


# ═══════════════════════════════════════════════════════════════════════
# PR #59 — Atomic Task Decomposition Validator
# ═══════════════════════════════════════════════════════════════════════


class TestAtomicTaskValidator:
    def test_simple_task_is_atomic(self):
        assignments = [{"agent": "backend", "task": "Add a /health endpoint."}]
        issues = validate_atomic_tasks(assignments)
        assert issues == []

    def test_task_too_long_flagged(self):
        long_task = "x" * 801
        issues = validate_atomic_tasks([{"agent": "a", "task": long_task}], max_chars=800)
        assert len(issues) == 1
        assert "too long" in issues[0].reason

    def test_too_many_imperatives_flagged(self):
        task = "add test build update deploy release document"
        issues = validate_atomic_tasks([{"agent": "a", "task": task}], max_imperatives=3)
        assert len(issues) == 1
        assert "imperatives" in issues[0].reason

    def test_conjunction_detection(self):
        task = "Build the API and then add tests and also deploy"
        issues = validate_atomic_tasks([{"agent": "a", "task": task}], max_conjunctions=1)
        assert len(issues) == 1
        assert "conjunction" in issues[0].reason.lower()

    def test_multiple_assignments(self):
        assignments = [
            {"agent": "a", "task": "Add endpoint."},
            {"agent": "b", "task": "x" * 900},
        ]
        issues = validate_atomic_tasks(assignments, max_chars=800)
        assert len(issues) == 1
        assert issues[0].index == 1
        assert issues[0].agent == "b"


# ═══════════════════════════════════════════════════════════════════════
# PR #61 — Context Loader Middleware
# ═══════════════════════════════════════════════════════════════════════


class TestContextLoaderMiddleware:
    async def test_reads_md_files_and_injects_into_metadata(self, tmp_path):
        (tmp_path / "rules.md").write_text("Be concise.")
        (tmp_path / "style.md").write_text("Use imperative voice.")

        metrics = MetricsRegistry()
        reg = SkillRegistry()
        reg.register(_StubSkill("ok"))
        reg.use(context_loader_middleware(tmp_path, metrics=metrics))
        # Execute — no way to read request.metadata from inside StubSkill,
        # but we can verify the counters fired.
        await reg.execute("stub", {})
        loaded = metrics.counter("context_files_loaded_total", "", labels={"skill": "stub"}).get()
        assert loaded == 2  # two .md files

    async def test_skill_filter_respected(self, tmp_path):
        (tmp_path / "rules.md").write_text("rules.")
        metrics = MetricsRegistry()
        reg = SkillRegistry()
        reg.register(_StubSkill("ok"))
        reg.use(context_loader_middleware(tmp_path, target_skills={"other"}, metrics=metrics))
        await reg.execute("stub", {})
        loaded = metrics.counter("context_files_loaded_total", "", labels={"skill": "stub"}).get()
        assert loaded == 0  # skipped: stub is not in target set

    async def test_missing_directory_is_noop(self, tmp_path):
        reg = SkillRegistry()
        reg.register(_StubSkill("ok"))
        reg.use(context_loader_middleware(tmp_path / "does-not-exist"))
        result = await reg.execute("stub", {})
        assert result.success


# ═══════════════════════════════════════════════════════════════════════
# PR #81 — Hierarchical Namespace Helpers + BaseStore path methods
# ═══════════════════════════════════════════════════════════════════════


class TestNamespaceHelpers:
    def test_path_to_namespace_basic(self):
        assert path_to_namespace("a.b.c") == ("a", "b", "c")

    def test_path_trims_empty_segments(self):
        assert path_to_namespace(".a..b.") == ("a", "b")

    def test_path_empty(self):
        assert path_to_namespace("") == ()

    def test_namespace_to_path_roundtrip(self):
        ns = ("project", "alice", "tasks")
        assert path_to_namespace(namespace_to_path(ns)) == ns

    def test_descends_from_strict(self):
        assert descends_from(("a", "b", "c"), ("a",))
        assert descends_from(("a", "b"), ("a", "b"))
        assert not descends_from(("a", "x"), ("a", "y"))

    def test_namespace_depth(self):
        assert namespace_depth(()) == 0
        assert namespace_depth(("a",)) == 1
        assert namespace_depth(("a", "b", "c")) == 3


class TestStorePathMethods:
    async def test_put_get_via_path(self):
        store = InMemoryStore()
        await store.aput_path("project.alice", "profile", {"role": "admin"})
        item = await store.aget_path("project.alice", "profile")
        assert item is not None
        assert item.value["role"] == "admin"

    async def test_search_by_path_prefix(self):
        store = InMemoryStore()
        await store.aput_path("org.team1", "alpha", {"x": 1})
        await store.aput_path("org.team1", "beta", {"x": 2})
        await store.aput_path("org.team2", "gamma", {"x": 3})
        results = await store.asearch_path("org.team1")
        names = {r.key for r in results}
        assert names == {"alpha", "beta"}


# ═══════════════════════════════════════════════════════════════════════
# PR #81 — Verbatim checkpoint log
# ═══════════════════════════════════════════════════════════════════════


class TestVerbatimCheckpointLog:
    async def test_inmemory_checkpoint_preserves_raw_log(self):
        cp = Checkpoint(
            checkpoint_id="t1:0",
            thread_id="t1",
            state={"x": 1},
            next_nodes=[],
            step_index=0,
            raw_log="user: hello\nassistant: hi",
        )
        checkpointer = InMemoryCheckpointer()
        await checkpointer.save(cp)
        restored = await checkpointer.get("t1:0")
        assert restored is not None
        assert restored.raw_log == "user: hello\nassistant: hi"

    async def test_default_raw_log_is_none(self):
        cp = Checkpoint(
            checkpoint_id="t2:0",
            thread_id="t2",
            state={},
            next_nodes=[],
            step_index=0,
        )
        assert cp.raw_log is None

    async def test_sqlite_checkpoint_preserves_raw_log(self, tmp_path):
        db_path = str(tmp_path / "ck.db")
        checkpointer = SQLiteCheckpointer(db_path)
        cp = Checkpoint(
            checkpoint_id="s1:0",
            thread_id="s1",
            state={"k": "v"},
            next_nodes=[],
            step_index=0,
            raw_log="raw-message-trace",
        )
        await checkpointer.save(cp)
        restored = await checkpointer.get("s1:0")
        assert restored is not None
        assert restored.raw_log == "raw-message-trace"
        checkpointer.close()
