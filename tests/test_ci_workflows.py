"""Structural tests for CI workflow YAML files.

These tests protect against silent regressions in monitoring and alerting
pipelines — specifically the uptime-check schedule and the deploy-failure
issue hooks — which are easy to break with a typo and hard to notice until
the next incident.
"""

from __future__ import annotations

import re
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
        domains = list(job["strategy"]["matrix"]["domain"])
        # Compare as set elements (exact equality), not via `in` on a string,
        # so CodeQL's py/incomplete-url-substring-sanitization rule does not
        # mistake this list-membership check for URL sanitization.
        expected = {"agents-orchestrator.com", "monitoring.agents-orchestrator.com"}
        assert expected.issubset(set(domains)), (
            f"Probe matrix must include {expected}, got {domains}"
        )

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
        # Word-boundary regex so CodeQL's py/incomplete-url-substring-sanitization
        # rule does not flag this content assertion on a YAML script body
        # as URL sanitization (it is structural verification, not validation).
        assert re.search(r"\bagents-orchestrator\.com\b", probe["run"]), (
            "Probe must target the production domain"
        )

    def test_probe_fails_on_untrusted_cert(self, wf: dict) -> None:
        """Regression guard: an untrusted cert must fail the deploy, not pass as DEGRADED.

        An earlier version of the probe accepted any HTTP 200/301/302/401/403
        response over an untrusted cert and exited 0 with a "DEGRADED — deploy
        continues" message. That hid a self-signed fallback being shipped to
        real users (ERR_CERT_AUTHORITY_INVALID in browsers).
        """
        steps = wf["jobs"]["deploy"]["steps"]
        probe = next((s for s in steps if "public" in s.get("name", "").lower()), None)
        run = probe["run"]
        assert "deploy continues" not in run, (
            "Untrusted-cert branch must no longer silently continue the deploy"
        )
        assert "openssl s_client" in run, (
            "Probe must read the served cert's issuer to diagnose failures"
        )
        assert "Self-signed certificate detected" in run, (
            "Probe must explicitly flag self-signed certs for debuggability"
        )
        # The untrusted-cert branch must terminate with a failure, not success.
        untrusted_branch = run.split("Public probe FAILED", 1)[1]
        assert "exit 1" in untrusted_branch, (
            "The untrusted-cert branch must exit non-zero to fail the deploy"
        )

    def test_has_issues_write_permission(self, wf: dict) -> None:
        perms = wf.get("permissions", {})
        assert perms.get("issues") == "write", (
            "deploy.yml must have issues:write so the failure hook can open issues"
        )

    def test_serializes_concurrent_deploys(self, wf: dict) -> None:
        """Regression guard: two back-to-back pushes must not race on EC2.

        Two deploys racing on the same EC2 host collide on shared state (docker
        networks and container names like `agent-orchestrator-certbot-1`) and
        leave the site down — the second deploy's `up -d` fails on a container
        the first deploy just created. Once that happens, every subsequent
        deploy keeps failing until the stale container is manually cleared.
        """
        concurrency = wf.get("concurrency")
        assert concurrency is not None, "deploy.yml must declare a top-level `concurrency:` block"
        assert concurrency.get("group"), "concurrency.group must be set"
        assert concurrency.get("cancel-in-progress") is False, (
            "cancel-in-progress must be false — cancelling a mid-flight deploy "
            "leaves EC2 in an inconsistent state (half-created containers, "
            "partial cert provisioning)."
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


class TestAutoMergeMaintenanceWorkflow:
    """`.github/workflows/auto-merge-maintenance.yml` — weekly deps/ci/docs auto-merge."""

    @pytest.fixture
    def wf(self) -> dict:
        return _load("auto-merge-maintenance.yml")

    def test_runs_weekly(self, wf: dict) -> None:
        on = _on_block(wf)
        assert "schedule" in on, "auto-merge must run on a schedule"
        crons = [entry["cron"] for entry in on["schedule"]]
        # Weekly cron: day-of-week field (5th) must pin a single weekday.
        assert any(c.split()[4] not in ("*", "?") for c in crons), (
            f"Expected a weekly cron pinned to a weekday, got {crons}"
        )

    def test_allows_manual_dispatch_with_dry_run(self, wf: dict) -> None:
        on = _on_block(wf)
        assert "workflow_dispatch" in on, "Manual dispatch must be available"
        inputs = on["workflow_dispatch"]["inputs"]
        assert "dry_run" in inputs, "Must expose a dry_run input for safe inspection"

    def test_grants_pr_and_contents_write(self, wf: dict) -> None:
        perms = wf.get("permissions", {})
        assert perms.get("pull-requests") == "write", "Need pull-requests:write to merge PRs"
        assert perms.get("contents") == "write", "Need contents:write to merge into the branch"

    def test_serializes_runs(self, wf: dict) -> None:
        concurrency = wf.get("concurrency")
        assert concurrency is not None, "Must declare a concurrency block"
        assert concurrency.get("cancel-in-progress") is False, (
            "Must not cancel an in-progress merge run mid-flight"
        )

    def test_only_targets_maintenance_prefixes(self, wf: dict) -> None:
        """Regression guard: the title filter must stay scoped to deps/ci/docs.

        Loosening this regex (e.g. to match `feat:` or an empty prefix) would
        let the weekly job auto-merge feature PRs without human review.
        """
        run = wf["jobs"]["auto-merge"]["steps"][0]["run"]
        assert "^(deps|ci|docs)" in run, (
            "Eligibility regex must be anchored to the deps/ci/docs prefixes"
        )

    def test_skips_prs_with_failing_or_missing_checks(self, wf: dict) -> None:
        run = wf["jobs"]["auto-merge"]["steps"][0]["run"]
        # A PR with zero reported checks must be skipped, not merged blind.
        assert "no checks reported" in run, "Must skip PRs that report no checks"
        # Only SUCCESS/NEUTRAL/SKIPPED count as green.
        assert "SUCCESS" in run and "NEUTRAL" in run and "SKIPPED" in run

    def test_requests_rebase_for_conflicting_dependabot_prs(self, wf: dict) -> None:
        run = wf["jobs"]["auto-merge"]["steps"][0]["run"]
        assert "@dependabot rebase" in run, (
            "Conflicting Dependabot PRs must be nudged to rebase for the next run"
        )

    def test_uses_squash_merge(self, wf: dict) -> None:
        run = wf["jobs"]["auto-merge"]["steps"][0]["run"]
        assert "--squash" in run, "Maintenance bumps should land as single squash commits"

    def test_repolls_unknown_mergeable_state(self, wf: dict) -> None:
        """Regression guard: a freshly rebased PR reports mergeable=UNKNOWN.

        GitHub computes the mergeable state lazily, so a PR queried right after
        a push/rebase comes back UNKNOWN and would be skipped forever if we did
        not re-poll. The job must retry the single PR before giving up.
        """
        run = wf["jobs"]["auto-merge"]["steps"][0]["run"]
        assert "UNKNOWN" in run, "Must explicitly handle the UNKNOWN mergeable state"
        assert "gh pr view" in run, "Must re-fetch the PR to resolve a stale UNKNOWN state"

    def test_refetches_state_before_each_merge(self, wf: dict) -> None:
        """Regression guard: the list snapshot goes stale as siblings merge.

        Several deps PRs edit the same pyproject.toml region, so merging one
        flips the others to CONFLICTING. The decision must read each PR's
        current state (not the initial `gh pr list` snapshot), otherwise a
        now-conflicting PR is attempted with a stale CLEAN state and errors out.
        """
        run = wf["jobs"]["auto-merge"]["steps"][0]["run"]
        # mergeable/state must NOT be read from the snapshot ($pr) — only the
        # live `gh pr view` fetch should populate them.
        assert "echo \"$pr\"   | jq -r '.mergeable'" not in run
        assert "echo \"$pr\"  | jq -r '.mergeStateStatus'" not in run

    def test_requests_rebase_on_late_merge_failure(self, wf: dict) -> None:
        """A conflict that appears mid-run must trigger a rebase, not a dead error."""
        run = wf["jobs"]["auto-merge"]["steps"][0]["run"]
        assert "merge failed, rebase requested" in run, (
            "A failed merge on a Dependabot PR must request a rebase for the next run"
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
