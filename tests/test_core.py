"""Tests for core abstractions."""

import pytest
from agent_orchestrator.core.skill import Skill, SkillRegistry, SkillResult
from agent_orchestrator.core.cooperation import (
    CooperationProtocol,
    TaskAssignment,
    TaskReport,
    SharedContextStore,
    Artifact,
)


class EchoSkill(Skill):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Returns the input message"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"message": {"type": "string"}}}

    async def execute(self, params: dict) -> SkillResult:
        return SkillResult(success=True, output=params.get("message", ""))


class TestSkillRegistry:
    @pytest.fixture
    def registry(self):
        reg = SkillRegistry()
        reg.register(EchoSkill())
        return reg

    def test_register_and_get(self, registry):
        skill = registry.get("echo")
        assert skill is not None
        assert skill.name == "echo"

    def test_get_unknown(self, registry):
        assert registry.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_execute(self, registry):
        result = await registry.execute("echo", {"message": "hello"})
        assert result.success
        assert result.output == "hello"

    @pytest.mark.asyncio
    async def test_execute_unknown(self, registry):
        result = await registry.execute("unknown", {})
        assert not result.success
        assert "Unknown skill" in result.error

    def test_list_skills(self, registry):
        assert registry.list_skills() == ["echo"]


class TestCooperationProtocol:
    def test_assign_and_complete(self):
        proto = CooperationProtocol()
        assignment = TaskAssignment(
            task_id="t1", from_agent="lead", to_agent="backend", description="Build API"
        )
        proto.assign(assignment)
        assert len(proto.get_pending()) == 1
        assert not proto.all_complete()

        proto.complete(TaskReport(task_id="t1", agent_name="backend", success=True, output="Done"))
        assert proto.all_complete()

    def test_dependency_ordering(self):
        proto = CooperationProtocol()
        proto.assign(
            TaskAssignment(
                task_id="t1", from_agent="lead", to_agent="backend", description="Build API"
            )
        )
        proto.assign(
            TaskAssignment(
                task_id="t2",
                from_agent="lead",
                to_agent="frontend",
                description="Build UI",
                depends_on=["t1"],
            )
        )

        ready = proto.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].task_id == "t1"

        proto.complete(TaskReport(task_id="t1", agent_name="backend", success=True, output="Done"))
        ready = proto.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].task_id == "t2"


class TestSharedContextStore:
    def test_publish_and_get(self):
        store = SharedContextStore()
        store.publish(Artifact(name="api_spec", type="spec", content="{}", produced_by="backend"))
        artifact = store.get_artifact("api_spec")
        assert artifact is not None
        assert artifact.version == 1

    def test_version_increment(self):
        store = SharedContextStore()
        store.publish(Artifact(name="api_spec", type="spec", content="v1", produced_by="backend"))
        store.publish(Artifact(name="api_spec", type="spec", content="v2", produced_by="backend"))
        assert store.get_artifact("api_spec").version == 2
