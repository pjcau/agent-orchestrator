"""Structural tests for CI workflow YAML files.

These tests protect against silent regressions in monitoring and alerting
pipelines — specifically the uptime-check schedule and the deploy-failure
issue hooks — which are easy to break with a typo and hard to notice until
the next incident.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _load(name: str) -> dict:
    path = WORKFLOWS_DIR / name
    assert path.exists(), f"Workflow file not found: {path}"
    # PyYAML parses the YAML "on" key as the boolean True; use a plain loader
    # and access it via the True key below.
    with path.open() as fh:
        return yaml.safe_load(fh)


def _on_block(workflow: dict) -> dict:
    return workflow.get(True) or workflow.get("on") or {}


class TestUptimeCheckWorkflow:
    """`.github/workflows/uptime-check.yml` — scheduled external probe."""

    @pytest.fixture
    def wf(self) -> dict:
        return _load("uptime-check.yml")

    def test_has_schedule_trigger(self, wf: dict) -> None:
        on = _on_block(wf)
        assert "schedule" in on, "uptime-check must run on a schedule"
        crons = [entry["cron"] for entry in on["schedule"]]
        assert any(c.startswith("*/") for c in crons), (
            f"Expected a periodic cron (*/N), got {crons}"
        )

    def test_allows_manual_dispatch(self, wf: dict) -> None:
        on = _on_block(wf)
        assert "workflow_dispatch" in on, "Manual dispatch must be available for on-call testing"

    def test_probes_both_production_domains(self, wf: dict) -> None:
        job = wf["jobs"]["probe"]
        domains = job["strategy"]["matrix"]["domain"]
        assert "agents-orchestrator.com" in domains
        assert "monitoring.agents-orchestrator.com" in domains

    def test_grants_issues_write(self, wf: dict) -> None:
        perms = wf.get("permissions", {})
        assert perms.get("issues") == "write", (
            "Workflow must have issues:write to open incident issues"
        )

    def test_opens_incident_issue_on_failure(self, wf: dict) -> None:
        steps = wf["jobs"]["probe"]["steps"]
        issue_step = next((s for s in steps if "issue" in s.get("name", "").lower()), None)
        assert issue_step is not None, "Missing issue-opening step"
        assert issue_step.get("if") == "failure()", (
            f"Issue step must run only on probe failure, got {issue_step.get('if')!r}"
        )
        assert issue_step["uses"].startswith("actions/github-script@"), (
            "Expected github-script action for issue creation"
        )
        assert "uptime-incident" in issue_step["with"]["script"], (
            "Issue must carry the uptime-incident label for dedup"
        )


class TestDeployWorkflowAlerts:
    """`.github/workflows/deploy.yml` — deploy-failure issue hook + public probe."""

    @pytest.fixture
    def wf(self) -> dict:
        return _load("deploy.yml")

    def test_has_public_https_probe(self, wf: dict) -> None:
        steps = wf["jobs"]["deploy"]["steps"]
        probe = next((s for s in steps if "public" in s.get("name", "").lower()), None)
        assert probe is not None, "Missing 'Public HTTPS probe' step"
        assert "agents-orchestrator.com" in probe["run"], "Probe must target the production domain"

    def test_has_issues_write_permission(self, wf: dict) -> None:
        perms = wf.get("permissions", {})
        assert perms.get("issues") == "write", (
            "deploy.yml must have issues:write so the failure hook can open issues"
        )

    def test_opens_issue_on_deploy_failure(self, wf: dict) -> None:
        steps = wf["jobs"]["deploy"]["steps"]
        issue_step = next(
            (
                s
                for s in steps
                if "issue" in s.get("name", "").lower() and s.get("if") == "failure()"
            ),
            None,
        )
        assert issue_step is not None, "deploy.yml must open an issue on failure (step missing)"
        assert issue_step["uses"].startswith("actions/github-script@")
        assert "deploy-failure" in issue_step["with"]["script"], (
            "Failure issues must carry the deploy-failure label for dedup"
        )


class TestEC2RestartWorkflow:
    """`.github/workflows/ec2-restart.yml` — emergency instance restart."""

    @pytest.fixture
    def wf(self) -> dict:
        return _load("ec2-restart.yml")

    def test_dispatch_only(self, wf: dict) -> None:
        on = _on_block(wf)
        assert "workflow_dispatch" in on, "Restart must be manually triggered"
        assert "schedule" not in on, "Restart must NOT be scheduled — it's a manual recovery action"

    def test_handles_stopped_and_running_states(self, wf: dict) -> None:
        steps = wf["jobs"]["restart"]["steps"]
        restart_step = next(
            (
                s
                for s in steps
                if "restart" in s.get("name", "").lower() and "Request" in s.get("name", "")
            ),
            None,
        )
        assert restart_step is not None
        script = restart_step["run"]
        assert "start-instances" in script, "Must call start-instances for stopped state"
        assert "running" in script, "Must branch on running state"
