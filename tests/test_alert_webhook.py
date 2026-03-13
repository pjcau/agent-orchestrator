"""Tests for the alert webhook handler."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_orchestrator.dashboard.alert_webhook import AlertHandler, _find_gh_cli


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


class MockUsageDB:
    """Minimal UsageDB stub for testing."""

    async def get_recent_errors(self, limit: int = 20) -> list[dict]:
        return [
            {
                "ts": time.time(),
                "agent": "backend",
                "tool_name": "bash",
                "error_type": "exit_code_error",
                "error_message": "non-zero exit code 1",
            }
        ]

    async def get_error_summary(self) -> dict:
        return {"by_agent": [{"agent": "backend", "error_type": "exit_code_error", "count": 3}]}

    def get_summary(self) -> dict:
        return {
            "total_requests": 42,
            "total_tokens": 10000,
            "total_cost_usd": 0.05,
            "db_connected": True,
        }


# ---------------------------------------------------------------------------
# AlertHandler.handle_alert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_alert_resolved_returns_early():
    handler = AlertHandler()
    result = await handler.handle_alert({"title": "HighCPU", "status": "resolved"})
    assert result["status"] == "resolved"
    assert result["alert"] == "HighCPU"


@pytest.mark.asyncio
async def test_handle_alert_stores_in_recent_alerts():
    handler = AlertHandler()
    await handler.handle_alert({"title": "LowDisk", "status": "resolved"})
    recent = handler.get_recent_alerts()
    assert len(recent) == 1
    assert recent[0]["alert_name"] == "LowDisk"


@pytest.mark.asyncio
async def test_handle_alert_caps_recent_alerts_at_max():
    handler = AlertHandler()
    handler._max_alerts = 3
    for i in range(5):
        await handler.handle_alert({"title": f"Alert{i}", "status": "resolved"})
    assert len(handler.get_recent_alerts()) == 3


@pytest.mark.asyncio
async def test_handle_alert_firing_without_gh_returns_logged_only():
    """When gh CLI is absent, alert should be logged but no issue created."""
    handler = AlertHandler()
    with patch("agent_orchestrator.dashboard.alert_webhook._find_gh_cli", return_value=None):
        result = await handler.handle_alert({"title": "AgentStalled", "status": "alerting"})
    assert result["status"] == "logged_only"
    assert result["issue_url"] is None


@pytest.mark.asyncio
async def test_handle_alert_firing_creates_issue_when_gh_present():
    """When gh CLI is present and succeeds, issue_url is returned."""
    handler = AlertHandler(usage_db=MockUsageDB())

    fake_process = MagicMock()
    fake_process.returncode = 0
    fake_process.stdout = "https://github.com/owner/repo/issues/99\n"

    with (
        patch(
            "agent_orchestrator.dashboard.alert_webhook._find_gh_cli",
            return_value="/usr/local/bin/gh",
        ),
        patch("asyncio.to_thread", new=AsyncMock(return_value=fake_process)),
    ):
        result = await handler.handle_alert(
            {
                "title": "HighCostSpike",
                "status": "alerting",
                "labels": {"severity": "warning"},
                "annotations": {"summary": "Cost spike"},
            }
        )

    assert result["status"] == "issue_created"
    assert result["issue_url"] == "https://github.com/owner/repo/issues/99"


@pytest.mark.asyncio
async def test_handle_alert_gh_failure_returns_logged_only():
    """When gh returns non-zero, status should be logged_only."""
    handler = AlertHandler()

    fake_process = MagicMock()
    fake_process.returncode = 1
    fake_process.stderr = "gh: error: label not found"

    with (
        patch(
            "agent_orchestrator.dashboard.alert_webhook._find_gh_cli",
            return_value="/usr/local/bin/gh",
        ),
        patch("asyncio.to_thread", new=AsyncMock(return_value=fake_process)),
    ):
        result = await handler.handle_alert({"title": "SomeAlert", "status": "alerting"})

    assert result["status"] == "logged_only"
    assert result["issue_url"] is None


@pytest.mark.asyncio
async def test_handle_alert_severity_critical():
    handler = AlertHandler()
    with patch("agent_orchestrator.dashboard.alert_webhook._find_gh_cli", return_value=None):
        result = await handler.handle_alert(
            {
                "title": "HighMemory",
                "status": "alerting",
                "labels": {"severity": "critical"},
            }
        )
    recent = handler.get_recent_alerts()
    assert recent[-1]["severity"] == "critical"
    assert result["alert"] == "HighMemory"


# ---------------------------------------------------------------------------
# _collect_diagnostics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_diagnostics_without_usage_db():
    handler = AlertHandler()
    alert = {"alert_name": "Test", "status": "alerting", "severity": "warning", "timestamp": 0}
    diagnostics = await handler._collect_diagnostics(alert)
    assert "alert" in diagnostics
    assert "collected_at" in diagnostics
    assert "recent_errors" not in diagnostics


@pytest.mark.asyncio
async def test_collect_diagnostics_with_usage_db():
    handler = AlertHandler(usage_db=MockUsageDB())
    alert = {"alert_name": "Test", "status": "alerting", "severity": "warning", "timestamp": 0}
    diagnostics = await handler._collect_diagnostics(alert)
    assert "recent_errors" in diagnostics
    assert "error_summary" in diagnostics
    assert "usage_summary" in diagnostics
    assert diagnostics["usage_summary"]["total_requests"] == 42


@pytest.mark.asyncio
async def test_collect_diagnostics_db_error_is_captured():
    """If usage_db raises, the error is captured gracefully without crash."""

    class BrokenDB:
        async def get_recent_errors(self, limit: int = 20):
            raise RuntimeError("DB connection lost")

        async def get_error_summary(self):
            raise RuntimeError("DB connection lost")

        def get_summary(self):
            raise RuntimeError("DB connection lost")

    handler = AlertHandler(usage_db=BrokenDB())
    alert = {"alert_name": "Test", "status": "alerting", "severity": "warning", "timestamp": 0}
    diagnostics = await handler._collect_diagnostics(alert)
    assert "error_collection_failed" in diagnostics


# ---------------------------------------------------------------------------
# _find_gh_cli
# ---------------------------------------------------------------------------


def test_find_gh_cli_returns_none_when_absent():
    """Should return None when `which gh` fails and common paths do not exist."""
    with (
        patch(
            "subprocess.run",
            return_value=MagicMock(returncode=1, stdout=""),
        ),
        patch("os.path.isfile", return_value=False),
    ):
        result = _find_gh_cli()
    assert result is None


def test_find_gh_cli_returns_path_from_which():
    with patch(
        "subprocess.run",
        return_value=MagicMock(returncode=0, stdout="/usr/bin/gh\n"),
    ):
        result = _find_gh_cli()
    assert result == "/usr/bin/gh"


# ---------------------------------------------------------------------------
# FastAPI endpoint integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alert_webhook_endpoint_resolved(monkeypatch):
    """POST /api/alerts/webhook with resolved status returns 200 with resolved status."""
    monkeypatch.setenv("ALLOW_DEV_MODE", "true")

    from fastapi.testclient import TestClient
    from agent_orchestrator.dashboard.app import create_dashboard_app
    from agent_orchestrator.dashboard.events import EventBus

    app = create_dashboard_app(event_bus=EventBus())
    client = TestClient(app, raise_server_exceptions=True)

    with patch("agent_orchestrator.dashboard.alert_webhook._find_gh_cli", return_value=None):
        resp = client.post(
            "/api/alerts/webhook",
            json={"title": "HighCPU", "status": "resolved"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "resolved"


@pytest.mark.asyncio
async def test_alert_recent_endpoint_empty(monkeypatch):
    """GET /api/alerts/recent returns empty list initially."""
    monkeypatch.setenv("ALLOW_DEV_MODE", "true")

    from fastapi.testclient import TestClient
    from agent_orchestrator.dashboard.app import create_dashboard_app
    from agent_orchestrator.dashboard.events import EventBus

    app = create_dashboard_app(event_bus=EventBus())
    client = TestClient(app)

    resp = client.get("/api/alerts/recent")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_alert_recent_endpoint_after_webhook(monkeypatch):
    """GET /api/alerts/recent returns alert after it has been received."""
    monkeypatch.setenv("ALLOW_DEV_MODE", "true")

    from fastapi.testclient import TestClient
    from agent_orchestrator.dashboard.app import create_dashboard_app
    from agent_orchestrator.dashboard.events import EventBus

    app = create_dashboard_app(event_bus=EventBus())
    client = TestClient(app)

    with patch("agent_orchestrator.dashboard.alert_webhook._find_gh_cli", return_value=None):
        client.post(
            "/api/alerts/webhook",
            json={"title": "LowDisk", "status": "alerting"},
        )

    resp = client.get("/api/alerts/recent")
    assert resp.status_code == 200
    alerts = resp.json()
    assert len(alerts) == 1
    assert alerts[0]["alert_name"] == "LowDisk"
