"""Alert webhook handler — receives Grafana alerts and creates GitHub issues for analysis.

When a Grafana alert fires, it:
1. Receives the alert payload via webhook
2. Collects diagnostic context (recent errors, metrics snapshot, alert details)
3. Creates a GitHub issue with the diagnostic report using `gh` CLI
4. The issue triggers a GitHub Actions workflow for automated root-cause analysis
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from typing import Any

logger = logging.getLogger(__name__)


def _sanitize_log(value: str, max_len: int = 200) -> str:
    """Sanitize a string for safe log output (no newlines, no control chars)."""
    return value.replace("\n", " ").replace("\r", " ")[:max_len]


class AlertHandler:
    """Processes Grafana webhook alerts and creates GitHub issues."""

    def __init__(self, usage_db: Any = None) -> None:
        self._usage_db = usage_db
        self._recent_alerts: list[dict] = []
        self._max_alerts = 100

    async def handle_alert(self, payload: dict) -> dict:
        """Process a Grafana alert webhook payload.

        Returns dict with status and issue_url if created.
        """
        alert_name = payload.get("title", payload.get("ruleName", "Unknown Alert"))
        status = payload.get("status", payload.get("state", "alerting"))
        severity = "critical" if "critical" in str(payload.get("labels", {})) else "warning"
        message = payload.get("message", "")

        # Extract alert details
        alert_record = {
            "alert_name": alert_name,
            "status": status,
            "severity": severity,
            "message": message,
            "timestamp": time.time(),
            "labels": payload.get("labels", {}),
            "annotations": payload.get("annotations", {}),
            "values": payload.get("values", {}),
        }

        self._recent_alerts.append(alert_record)
        if len(self._recent_alerts) > self._max_alerts:
            self._recent_alerts = self._recent_alerts[-self._max_alerts :]

        # Only create issues for firing alerts, not resolved ones
        if status == "resolved":
            logger.info("Alert resolved: %s", _sanitize_log(alert_name))
            return {"status": "resolved", "alert": alert_name}

        # Collect diagnostic context
        diagnostics = await self._collect_diagnostics(alert_record)

        # Create GitHub issue
        issue_url = await self._create_github_issue(alert_record, diagnostics)

        return {
            "status": "issue_created" if issue_url else "logged_only",
            "alert": alert_name,
            "issue_url": issue_url,
        }

    async def _collect_diagnostics(self, alert: dict) -> dict:
        """Gather diagnostic context for the alert report."""
        diagnostics: dict[str, Any] = {
            "alert": alert,
            "collected_at": time.time(),
        }

        # Recent errors from DB
        if self._usage_db:
            try:
                recent_errors = await self._usage_db.get_recent_errors(limit=20)
                error_summary = await self._usage_db.get_error_summary()
                diagnostics["recent_errors"] = recent_errors
                diagnostics["error_summary"] = error_summary
            except Exception as e:
                diagnostics["error_collection_failed"] = str(e)

        # Usage summary
        if self._usage_db:
            try:
                diagnostics["usage_summary"] = self._usage_db.get_summary()
            except Exception as e:
                diagnostics["usage_collection_failed"] = str(e)

        return diagnostics

    async def _create_github_issue(self, alert: dict, diagnostics: dict) -> str | None:
        """Create a GitHub issue with alert diagnostics using gh CLI.

        Returns the issue URL or None if creation failed.
        """
        gh_path = _find_gh_cli()
        if not gh_path:
            logger.warning("gh CLI not found, cannot create alert issue")
            return None

        title = f"[Alert] {alert['alert_name']} — {alert['severity']}"

        # Build issue body
        body_parts = [
            "## Alert Details\n",
            f"- **Alert**: {alert['alert_name']}",
            f"- **Severity**: {alert['severity']}",
            f"- **Status**: {alert['status']}",
            f"- **Message**: {alert.get('message', 'N/A')}",
            f"- **Timestamp**: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(alert['timestamp']))}",
        ]

        if alert.get("labels"):
            body_parts.append(f"\n**Labels**: `{json.dumps(alert['labels'])}`")
        if alert.get("annotations"):
            body_parts.append(f"\n**Annotations**: `{json.dumps(alert['annotations'])}`")

        # Recent errors
        errors = diagnostics.get("recent_errors", [])
        if errors:
            body_parts.append("\n## Recent Errors (last 20)\n")
            body_parts.append("| Time | Agent | Tool | Type | Message |")
            body_parts.append("|------|-------|------|------|---------|")
            for err in errors[:20]:
                ts = time.strftime("%H:%M:%S", time.gmtime(err.get("ts", 0)))
                agent = err.get("agent", "?")
                tool = err.get("tool_name", "?")
                etype = err.get("error_type", "?")
                msg = str(err.get("error_message", ""))[:80].replace("|", "\\|")
                body_parts.append(f"| {ts} | {agent} | {tool} | {etype} | {msg} |")

        # Error summary
        summary = diagnostics.get("error_summary", {})
        if summary.get("by_agent"):
            body_parts.append("\n## Error Summary\n")
            body_parts.append("| Agent | Error Type | Count |")
            body_parts.append("|-------|-----------|-------|")
            for item in summary["by_agent"][:15]:
                body_parts.append(f"| {item['agent']} | {item['error_type']} | {item['count']} |")

        # Usage snapshot
        usage = diagnostics.get("usage_summary", {})
        if usage:
            body_parts.append("\n## Usage Snapshot\n")
            body_parts.append(f"- Total requests: {usage.get('total_requests', 0)}")
            body_parts.append(f"- Total tokens: {usage.get('total_tokens', 0)}")
            body_parts.append(f"- Total cost: ${usage.get('total_cost_usd', 0):.4f}")
            body_parts.append(f"- DB connected: {usage.get('db_connected', False)}")

        body_parts.append("\n---")
        body_parts.append(
            "_This issue was automatically created by the alert webhook. "
            "The `alert-analysis` workflow will run automated root-cause analysis._"
        )
        body_parts.append("\nLabel: `alert`, `automated`")

        body = "\n".join(body_parts)

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    gh_path,
                    "issue",
                    "create",
                    "--title",
                    title,
                    "--body",
                    body,
                    "--label",
                    "alert,automated",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                issue_url = result.stdout.strip()
                logger.info("Created alert issue: %s", _sanitize_log(issue_url))
                return issue_url
            else:
                logger.warning("gh issue create failed: %s", _sanitize_log(result.stderr, 500))
                return None
        except Exception as e:
            logger.warning("Failed to create GitHub issue: %s", e)
            return None

    def get_recent_alerts(self) -> list[dict]:
        """Return recent alert records."""
        return list(self._recent_alerts)


def _find_gh_cli() -> str | None:
    """Find the gh CLI binary."""
    try:
        result = subprocess.run(["which", "gh"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    # Common paths
    for path in ["/usr/local/bin/gh", "/usr/bin/gh", "/opt/homebrew/bin/gh"]:
        if os.path.isfile(path):
            return path
    return None
