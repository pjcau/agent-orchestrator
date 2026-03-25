"""Tests for CodeQL security fixes.

Covers:
- Log injection prevention (newlines stripped from user-controlled log inputs)
- Stack trace exposure prevention (generic error messages in HTTP responses)
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.core.skill import Skill, SkillRegistry, SkillResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class EchoSkill(Skill):
    """Skill that echoes params for testing."""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo params"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"key": {"type": "string"}}}

    async def execute(self, params: dict[str, Any]) -> SkillResult:
        return SkillResult(success=True, output=params)


# ---------------------------------------------------------------------------
# Log injection tests — SkillRegistry
# ---------------------------------------------------------------------------


class TestLogInjectionSkillRegistry:
    """Verify that newlines in tool_description are sanitized before logging."""

    @pytest.mark.asyncio
    async def test_tool_description_newlines_sanitized(self, caplog):
        """Newlines in _description must not appear in log output."""
        registry = SkillRegistry()
        registry.register(EchoSkill())

        malicious_desc = "legit\nINFO:root:Fake log entry\rAnother fake"
        with caplog.at_level(logging.INFO):
            await registry.execute("echo", {"_description": malicious_desc, "key": "val"})

        for record in caplog.records:
            assert "\n" not in record.getMessage(), "Log message contains newline (log injection)"
            assert "\r" not in record.getMessage(), (
                "Log message contains carriage return (log injection)"
            )

    @pytest.mark.asyncio
    async def test_skill_name_newlines_sanitized(self, caplog):
        """Newlines in the skill name param must not appear in log output."""
        registry = SkillRegistry()
        registry.register(EchoSkill())

        # Execute with a known skill but pass malicious name indirectly
        # The name "echo" is safe, but we test that sanitization runs
        # by registering a skill whose name contains newlines
        class BadNameSkill(EchoSkill):
            @property
            def name(self) -> str:
                return "bad\nskill"

        registry.register(BadNameSkill())
        with caplog.at_level(logging.INFO):
            await registry.execute("bad\nskill", {"_description": "test", "key": "val"})

        for record in caplog.records:
            assert "\n" not in record.getMessage(), "Log injection via skill name"
            assert "\r" not in record.getMessage(), "Log injection via skill name"

    @pytest.mark.asyncio
    async def test_tool_description_without_newlines_works(self, caplog):
        """Normal descriptions still appear in logs."""
        registry = SkillRegistry()
        registry.register(EchoSkill())

        with caplog.at_level(logging.INFO):
            result = await registry.execute("echo", {"_description": "Read file", "key": "val"})

        assert result.success
        assert any("Read file" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Log injection tests — ConversationManager
# ---------------------------------------------------------------------------


class TestLogInjectionConversation:
    """Verify that conversation thread loading sanitizes log parameters."""

    @pytest.mark.asyncio
    async def test_dangling_tool_call_log_sanitized(self, caplog):
        """Newlines in thread_id/tool name/call_id must be stripped from log output."""
        from agent_orchestrator.core.conversation import ConversationManager

        mgr = ConversationManager()
        # Inject a thread with a dangling tool call containing newlines
        malicious_thread_id = "thread\nINFO:root:INJECTED"
        mgr._threads[malicious_thread_id] = [
            {
                "role": "assistant",
                "content": "thinking...",
                "timestamp": 1.0,
                "metadata": {"tool_calls": [{"id": "call\r\nFAKE", "name": "tool\nINJECT"}]},
            }
        ]

        with caplog.at_level(logging.WARNING):
            await mgr.get_history(malicious_thread_id)

        for record in caplog.records:
            msg = record.getMessage()
            assert "\n" not in msg, f"Log injection via newline: {msg!r}"
            assert "\r" not in msg, f"Log injection via carriage return: {msg!r}"


# ---------------------------------------------------------------------------
# Stack trace exposure tests
# ---------------------------------------------------------------------------


class TestStackTraceExposure:
    """Verify upload endpoint returns generic error messages, not exception details."""

    def test_no_str_exc_in_json_responses(self):
        """AST check: no str(exc) inside JSONResponse calls in except handlers."""
        app_path = (
            Path(__file__).parent.parent / "src" / "agent_orchestrator" / "dashboard" / "app.py"
        )
        source = app_path.read_text()
        tree = ast.parse(source)

        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.name:
                exc_name = node.name
                # Find JSONResponse calls within this handler
                for child in ast.walk(node):
                    if (
                        isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Name)
                        and child.func.id == "JSONResponse"
                    ):
                        # Check if str(exc) appears anywhere in the JSONResponse args
                        for grandchild in ast.walk(child):
                            if (
                                isinstance(grandchild, ast.Call)
                                and isinstance(grandchild.func, ast.Name)
                                and grandchild.func.id == "str"
                                and grandchild.args
                                and isinstance(grandchild.args[0], ast.Name)
                                and grandchild.args[0].id == exc_name
                            ):
                                violations.append(
                                    f"Line {grandchild.lineno}: str({exc_name}) "
                                    "in JSONResponse within except handler"
                                )

        assert not violations, "Exception details exposed in HTTP responses:\n" + "\n".join(
            violations
        )
