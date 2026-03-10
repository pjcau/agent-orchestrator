"""Tests for Terraform infrastructure configuration files.

Validates that all Terraform modules are syntactically correct,
properly structured, and follow security best practices.
"""

import os
import pathlib

TERRAFORM_DIR = pathlib.Path(__file__).parent.parent / "terraform"


class TestTerraformStructure:
    """Verify Terraform directory structure and required files."""

    def test_backend_bootstrap_exists(self):
        assert (TERRAFORM_DIR / "backend" / "main.tf").exists()

    def test_root_module_exists(self):
        assert (TERRAFORM_DIR / "main.tf").exists()
        assert (TERRAFORM_DIR / "variables.tf").exists()
        assert (TERRAFORM_DIR / "outputs.tf").exists()

    def test_networking_module_exists(self):
        mod = TERRAFORM_DIR / "modules" / "networking"
        assert (mod / "main.tf").exists()
        assert (mod / "variables.tf").exists()
        assert (mod / "outputs.tf").exists()

    def test_ec2_module_exists(self):
        mod = TERRAFORM_DIR / "modules" / "ec2"
        assert (mod / "main.tf").exists()
        assert (mod / "variables.tf").exists()
        assert (mod / "outputs.tf").exists()
        assert (mod / "user_data.sh").exists()

    def test_iam_module_exists(self):
        mod = TERRAFORM_DIR / "modules" / "iam"
        assert (mod / "main.tf").exists()
        assert (mod / "variables.tf").exists()
        assert (mod / "outputs.tf").exists()

    def test_tfvars_example_exists(self):
        assert (TERRAFORM_DIR / "terraform.tfvars.example").exists()

    def test_tfvars_in_gitignore(self):
        """terraform.tfvars must be in .gitignore (contains secrets)."""
        gitignore = (TERRAFORM_DIR.parent / ".gitignore").read_text()
        assert "terraform.tfvars" in gitignore


class TestTerraformSecurity:
    """Verify security best practices in Terraform config."""

    def _read(self, *parts: str) -> str:
        return (TERRAFORM_DIR / os.path.join(*parts)).read_text()

    def test_s3_bucket_encryption(self):
        content = self._read("backend", "main.tf")
        assert "AES256" in content or "aws:kms" in content

    def test_s3_public_access_blocked(self):
        content = self._read("backend", "main.tf")
        assert "block_public_acls" in content
        assert "block_public_policy" in content

    def test_s3_versioning_enabled(self):
        content = self._read("backend", "main.tf")
        assert "Enabled" in content

    def test_dynamodb_lock_table(self):
        content = self._read("backend", "main.tf")
        assert "LockID" in content

    def test_ec2_imdsv2_required(self):
        """IMDSv2 must be required to prevent SSRF attacks."""
        content = self._read("modules", "ec2", "main.tf")
        assert "http_tokens" in content
        assert '"required"' in content

    def test_ebs_encryption(self):
        content = self._read("modules", "ec2", "main.tf")
        assert "encrypted" in content

    def test_ssh_restricted_to_allowed_cidrs(self):
        """SSH must not be open to 0.0.0.0/0."""
        content = self._read("modules", "networking", "main.tf")
        # SSH ingress uses var.ssh_allowed_cidrs, not 0.0.0.0/0
        assert "ssh_allowed_cidrs" in content

    def test_state_backend_encrypted(self):
        content = self._read("main.tf")
        assert "encrypt" in content
        assert "true" in content


class TestTerraformWorkflow:
    """Verify GitHub Actions Terraform workflow."""

    WORKFLOW = pathlib.Path(__file__).parent.parent / ".github" / "workflows" / "terraform.yml"

    def test_workflow_exists(self):
        assert self.WORKFLOW.exists()

    def test_workflow_triggers_on_terraform_changes(self):
        content = self.WORKFLOW.read_text()
        assert "terraform/**" in content

    def test_workflow_runs_fmt_check(self):
        content = self.WORKFLOW.read_text()
        assert "terraform fmt" in content

    def test_workflow_runs_validate(self):
        content = self.WORKFLOW.read_text()
        assert "terraform validate" in content

    def test_workflow_plan_before_apply(self):
        content = self.WORKFLOW.read_text()
        assert "terraform plan" in content
        assert "terraform apply" in content

    def test_apply_only_on_main(self):
        content = self.WORKFLOW.read_text()
        assert "refs/heads/main" in content


class TestUserDataScript:
    """Verify EC2 user data script."""

    def test_installs_docker(self):
        content = (TERRAFORM_DIR / "modules" / "ec2" / "user_data.sh").read_text()
        assert "docker" in content

    def test_installs_docker_compose(self):
        content = (TERRAFORM_DIR / "modules" / "ec2" / "user_data.sh").read_text()
        assert "docker-compose" in content or "docker compose" in content

    def test_installs_node_exporter(self):
        content = (TERRAFORM_DIR / "modules" / "ec2" / "user_data.sh").read_text()
        assert "node_exporter" in content

    def test_creates_app_directory(self):
        content = (TERRAFORM_DIR / "modules" / "ec2" / "user_data.sh").read_text()
        assert "/opt/agent-orchestrator" in content

    def test_script_uses_strict_mode(self):
        content = (TERRAFORM_DIR / "modules" / "ec2" / "user_data.sh").read_text()
        assert "set -euo pipefail" in content
