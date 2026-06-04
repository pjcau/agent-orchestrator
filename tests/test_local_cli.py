"""Tests for the subprocess entrypoint used by `ago run --local`.

The Rust CLI spawns `python3 -m agent_orchestrator.local_cli`, writes the
request as JSON on stdin, and reads a single JSON response object from
stdout. These tests pin that wire contract — both happy path and the
documented failure shapes — so a future refactor of `local_cli.py` does
not silently break the CLI integration.
"""

from __future__ import annotations

import io
import json
import os
from unittest.mock import patch

import pytest

from agent_orchestrator.core.agent import TaskResult, TaskStatus
from agent_orchestrator import local_cli


@pytest.mark.asyncio
async def test_run_returns_completed_envelope():
    """The happy path: `_run` returns a dict with the documented keys."""

    fake_result = TaskResult(
        status=TaskStatus.COMPLETED,
        output="all good",
        total_tokens=42,
        total_cost_usd=0.01,
        steps_taken=3,
    )

    async def fake_run_agent(self, **kwargs):  # noqa: ARG001 — interface match
        return fake_result

    with patch(
        "agent_orchestrator.client.OrchestratorClient.run_agent",
        new=fake_run_agent,
    ), patch.object(
        local_cli, "_build_provider", lambda key, model: _StubProvider()
    ):
        out = await local_cli._run(
            {
                "agent": "backend",
                "task": "do thing",
                "model": "claude-sonnet-4-6",
                "provider": "anthropic",
            }
        )

    assert out["success"] is True
    assert out["output"] == "all good"
    assert out["error"] is None
    assert out["total_input_tokens"] == 42
    assert out["total_cost_usd"] == 0.01
    assert out["steps_taken"] == 3
    assert out["status"] == "completed"
    assert isinstance(out["elapsed_s"], float)


@pytest.mark.asyncio
async def test_run_failed_status_maps_to_error():
    fake_result = TaskResult(
        status=TaskStatus.FAILED,
        output="",
        error="provider unreachable",
    )

    async def fake_run_agent(self, **kwargs):  # noqa: ARG001
        return fake_result

    with patch(
        "agent_orchestrator.client.OrchestratorClient.run_agent",
        new=fake_run_agent,
    ), patch.object(
        local_cli, "_build_provider", lambda key, model: _StubProvider()
    ):
        out = await local_cli._run(
            {
                "agent": "backend",
                "task": "do thing",
                "model": "m",
                "provider": "anthropic",
            }
        )

    assert out["success"] is False
    assert out["output"] is None
    assert out["error"] == "provider unreachable"


@pytest.mark.asyncio
async def test_missing_required_fields_raise():
    with pytest.raises(ValueError, match="missing 'agent'"):
        await local_cli._run({"task": "x", "model": "m"})
    with pytest.raises(ValueError, match="missing 'task'"):
        await local_cli._run({"agent": "a", "model": "m"})
    with pytest.raises(ValueError, match="missing 'model'"):
        await local_cli._run({"agent": "a", "task": "x"})


def test_build_provider_anthropic_requires_env():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            local_cli._build_provider("anthropic", "claude-sonnet-4-6")


def test_build_provider_rejects_unknown():
    with pytest.raises(RuntimeError, match="unknown provider"):
        local_cli._build_provider("not-a-real-vendor", "m")


def test_main_empty_stdin_returns_error_envelope(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    rc = local_cli.main()
    captured = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(captured.strip())
    assert payload["success"] is False
    assert "empty stdin" in payload["error"]


def test_main_invalid_json_returns_error_envelope(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("{not-json"))
    rc = local_cli.main()
    captured = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(captured.strip())
    assert payload["success"] is False
    assert "invalid JSON" in payload["error"]


def test_main_unknown_provider_surfaces_as_json(monkeypatch, capsys):
    body = json.dumps(
        {"agent": "a", "task": "x", "model": "m", "provider": "bogus"}
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(body))
    rc = local_cli.main()
    captured = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(captured.strip())
    assert payload["success"] is False
    assert "unknown provider" in payload["error"]


class _StubProvider:
    """Minimal provider stand-in for patched `_build_provider`. Has no
    methods because every `run_agent` call here is itself patched."""

    name = "stub"
