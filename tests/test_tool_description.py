"""Tests for the _description parameter on tool calls.

Covers:
- _description is extracted from params before skill execution
- Tool works without _description (backward compatibility)
- _description appears in audit log entries
- _description appears in dashboard events
- _description does not interfere with the tool's own parameters
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from agent_orchestrator.core.audit import (
    AuditEntry,
    AuditLog,
    EVENT_TOOL_CALL,
)
from agent_orchestrator.core.skill import (
    Skill,
    SkillRegistry,
    SkillResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class RecorderSkill(Skill):
    """Skill that records the exact params it receives."""

    def __init__(self) -> None:
        self.received_params: dict[str, Any] | None = None

    @property
    def name(self) -> str:
        return "recorder"

    @property
    def description(self) -> str:
        return "Records params for testing"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "count": {"type": "integer"},
            },
        }

    async def execute(self, params: dict) -> SkillResult:
        self.received_params = dict(params)
        return SkillResult(success=True, output=params.get("text", ""))


# ---------------------------------------------------------------------------
# _description extraction
# ---------------------------------------------------------------------------


class TestDescriptionExtraction:
    @pytest.fixture
    def registry(self) -> SkillRegistry:
        reg = SkillRegistry()
        reg.register(RecorderSkill())
        return reg

    @pytest.mark.asyncio
    async def test_description_extracted_before_execution(self, registry: SkillRegistry) -> None:
        """_description must be stripped from params before the skill sees them."""
        result = await registry.execute(
            "recorder",
            {"text": "hello", "_description": "Looking up greeting text"},
        )
        assert result.success
        skill: RecorderSkill = registry.get("recorder")  # type: ignore[assignment]
        assert "_description" not in skill.received_params
        assert skill.received_params == {"text": "hello"}

    @pytest.mark.asyncio
    async def test_works_without_description(self, registry: SkillRegistry) -> None:
        """Backward compatibility: no _description is fine."""
        result = await registry.execute("recorder", {"text": "world"})
        assert result.success
        assert result.output == "world"
        skill: RecorderSkill = registry.get("recorder")  # type: ignore[assignment]
        assert "_description" not in skill.received_params

    @pytest.mark.asyncio
    async def test_description_does_not_interfere_with_params(
        self, registry: SkillRegistry
    ) -> None:
        """Ensure _description removal doesn't affect other params."""
        result = await registry.execute(
            "recorder",
            {"text": "hi", "count": 3, "_description": "Testing multi-param"},
        )
        assert result.success
        skill: RecorderSkill = registry.get("recorder")  # type: ignore[assignment]
        assert skill.received_params == {"text": "hi", "count": 3}

    @pytest.mark.asyncio
    async def test_description_logged(
        self, registry: SkillRegistry, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When _description is provided it should be logged."""
        with caplog.at_level(logging.INFO, logger="agent_orchestrator.core.skill"):
            await registry.execute(
                "recorder",
                {"text": "x", "_description": "Reading config file"},
            )
        assert any("Reading config file" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_no_log_without_description(
        self, registry: SkillRegistry, caplog: pytest.LogCaptureFixture
    ) -> None:
        """No description log when _description is absent."""
        with caplog.at_level(logging.INFO, logger="agent_orchestrator.core.skill"):
            await registry.execute("recorder", {"text": "x"})
        assert not any("Tool recorder:" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_description_propagated_in_metadata(self, registry: SkillRegistry) -> None:
        """_description should be available in SkillRequest.metadata via middleware."""
        captured_metadata: dict[str, Any] = {}

        async def spy_middleware(request, next_fn):
            captured_metadata.update(request.metadata)
            return await next_fn(request)

        registry.use(spy_middleware)
        await registry.execute(
            "recorder",
            {"text": "a", "_description": "Spy test"},
        )
        assert captured_metadata.get("tool_description") == "Spy test"

    @pytest.mark.asyncio
    async def test_no_metadata_without_description(self, registry: SkillRegistry) -> None:
        """Without _description, metadata should not contain tool_description."""
        captured_metadata: dict[str, Any] = {}

        async def spy_middleware(request, next_fn):
            captured_metadata.update(request.metadata)
            return await next_fn(request)

        registry.use(spy_middleware)
        await registry.execute("recorder", {"text": "a"})
        assert "tool_description" not in captured_metadata


# ---------------------------------------------------------------------------
# Tool schema generation
# ---------------------------------------------------------------------------


class TestToolDefinitions:
    def test_description_param_injected_in_schema(self) -> None:
        """to_tool_definitions() should inject _description into every tool schema."""
        reg = SkillRegistry()
        reg.register(RecorderSkill())
        defs = reg.to_tool_definitions()
        assert len(defs) == 1
        props = defs[0]["parameters"]["properties"]
        assert "_description" in props
        assert props["_description"]["type"] == "string"

    def test_original_params_preserved(self) -> None:
        """Injection must not mutate the original skill's parameters."""
        skill = RecorderSkill()
        reg = SkillRegistry()
        reg.register(skill)
        reg.to_tool_definitions()
        # The skill's own parameters dict should be untouched
        assert "_description" not in skill.parameters.get("properties", {})


# ---------------------------------------------------------------------------
# Audit log integration
# ---------------------------------------------------------------------------


class TestAuditToolDescription:
    def test_audit_entry_has_tool_description_field(self) -> None:
        """AuditEntry must accept and store tool_description."""
        entry = AuditEntry(
            timestamp=1.0,
            event_type=EVENT_TOOL_CALL,
            action="call recorder",
            tool_description="Reading user config",
        )
        assert entry.tool_description == "Reading user config"

    def test_audit_entry_default_none(self) -> None:
        """tool_description defaults to None for backward compat."""
        entry = AuditEntry(
            timestamp=1.0,
            event_type=EVENT_TOOL_CALL,
            action="call recorder",
        )
        assert entry.tool_description is None

    def test_log_action_with_tool_description(self) -> None:
        """AuditLog.log_action() should accept tool_description kwarg."""
        log = AuditLog()
        entry = log.log_action(
            EVENT_TOOL_CALL,
            "backend",
            "call recorder",
            tool_description="Checking file contents",
        )
        assert entry.tool_description == "Checking file contents"

    def test_log_action_without_tool_description(self) -> None:
        """AuditLog.log_action() should work without tool_description."""
        log = AuditLog()
        entry = log.log_action(EVENT_TOOL_CALL, "backend", "call recorder")
        assert entry.tool_description is None

    def test_export_json_includes_tool_description(self) -> None:
        """export_json() must include the tool_description field."""
        log = AuditLog()
        log.log_action(
            EVENT_TOOL_CALL,
            "backend",
            "call recorder",
            tool_description="Listing directory",
        )
        exported = log.export_json()
        assert len(exported) == 1
        assert exported[0]["tool_description"] == "Listing directory"

    def test_export_json_null_tool_description(self) -> None:
        """export_json() with no description should have None."""
        log = AuditLog()
        log.log_action(EVENT_TOOL_CALL, "backend", "call recorder")
        exported = log.export_json()
        assert exported[0]["tool_description"] is None
