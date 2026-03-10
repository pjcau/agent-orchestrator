"""Tests for Sprint 2 production deployment configuration.

Validates docker-compose.prod.yml, nginx config, Prometheus/Grafana setup,
and deploy workflow.
"""

import pathlib

import yaml

ROOT = pathlib.Path(__file__).parent.parent
DOCKER_DIR = ROOT / "docker"


class TestDockerComposeProd:
    """Verify docker-compose.prod.yml structure and security."""

    def _load(self) -> dict:
        return yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())

    def test_file_exists(self):
        assert (ROOT / "docker-compose.prod.yml").exists()

    def test_required_services(self):
        config = self._load()
        services = set(config["services"].keys())
        assert {"nginx", "dashboard", "postgres", "redis", "prometheus", "grafana"} <= services

    def test_dashboard_uses_production_env(self):
        config = self._load()
        env = config["services"]["dashboard"]["environment"]
        assert "ENVIRONMENT=production" in env

    def test_dashboard_has_healthcheck(self):
        config = self._load()
        assert "healthcheck" in config["services"]["dashboard"]

    def test_postgres_uses_env_password(self):
        """Postgres password must come from env var, not hardcoded."""
        config = self._load()
        pw = config["services"]["postgres"]["environment"]["POSTGRES_PASSWORD"]
        assert "${" in pw  # must be a variable reference

    def test_redis_has_maxmemory(self):
        config = self._load()
        cmd = config["services"]["redis"]["command"]
        assert "maxmemory" in cmd

    def test_prometheus_not_exposed(self):
        """Prometheus must not have public ports — SSH tunnel only."""
        config = self._load()
        assert "ports" not in config["services"]["prometheus"]

    def test_grafana_not_exposed(self):
        """Grafana must not have public ports — SSH tunnel only."""
        config = self._load()
        assert "ports" not in config["services"]["grafana"]

    def test_nginx_exposes_only_80_443(self):
        config = self._load()
        ports = config["services"]["nginx"]["ports"]
        port_numbers = [p.split(":")[0] for p in ports]
        assert set(port_numbers) == {"80", "443"}

    def test_volumes_defined(self):
        config = self._load()
        assert "pgdata" in config["volumes"]
        assert "ssl-certs" in config["volumes"]


class TestNginxConfig:
    """Verify nginx reverse proxy configuration."""

    def _read(self) -> str:
        return (DOCKER_DIR / "nginx" / "nginx.conf").read_text()

    def test_config_exists(self):
        assert (DOCKER_DIR / "nginx" / "nginx.conf").exists()

    def test_http_to_https_redirect(self):
        content = self._read()
        assert "return 301 https://" in content

    def test_ssl_modern_protocols(self):
        content = self._read()
        assert "TLSv1.2" in content
        assert "TLSv1.3" in content

    def test_hsts_header(self):
        content = self._read()
        assert "Strict-Transport-Security" in content

    def test_security_headers(self):
        content = self._read()
        assert "X-Frame-Options" in content
        assert "X-Content-Type-Options" in content

    def test_websocket_upgrade(self):
        content = self._read()
        assert "Upgrade" in content
        assert "upgrade" in content

    def test_metrics_blocked(self):
        """Metrics endpoint must not be accessible from outside."""
        content = self._read()
        assert "deny all" in content

    def test_rate_limiting(self):
        content = self._read()
        assert "limit_req_zone" in content
        assert "limit_req zone" in content


class TestPrometheusConfig:
    """Verify Prometheus configuration."""

    def test_config_exists(self):
        assert (DOCKER_DIR / "prometheus" / "prometheus.yml").exists()

    def test_alerts_exist(self):
        assert (DOCKER_DIR / "prometheus" / "alerts.yml").exists()

    def test_scrapes_dashboard(self):
        config = yaml.safe_load((DOCKER_DIR / "prometheus" / "prometheus.yml").read_text())
        jobs = [s["job_name"] for s in config["scrape_configs"]]
        assert "dashboard" in jobs

    def test_scrapes_node_exporter(self):
        config = yaml.safe_load((DOCKER_DIR / "prometheus" / "prometheus.yml").read_text())
        jobs = [s["job_name"] for s in config["scrape_configs"]]
        assert "node" in jobs

    def test_alert_rules_defined(self):
        alerts = yaml.safe_load((DOCKER_DIR / "prometheus" / "alerts.yml").read_text())
        rules = alerts["groups"][0]["rules"]
        alert_names = [r["alert"] for r in rules]
        assert "HighErrorRate" in alert_names
        assert "AgentStalled" in alert_names
        assert "HighCostSpike" in alert_names
        assert "HighCPU" in alert_names


class TestGrafanaConfig:
    """Verify Grafana provisioning."""

    def test_datasource_configured(self):
        ds = yaml.safe_load(
            (DOCKER_DIR / "grafana" / "provisioning" / "datasources" / "prometheus.yml").read_text()
        )
        assert ds["datasources"][0]["type"] == "prometheus"
        assert ds["datasources"][0]["url"] == "http://prometheus:9090"

    def test_dashboard_provisioned(self):
        assert (DOCKER_DIR / "grafana" / "dashboards" / "orchestrator.json").exists()


class TestDeployWorkflow:
    """Verify GitHub Actions deploy workflow."""

    WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"

    def test_workflow_exists(self):
        assert self.WORKFLOW.exists()

    def test_runs_tests_before_deploy(self):
        config = yaml.safe_load(self.WORKFLOW.read_text())
        assert "test" in config["jobs"]
        assert config["jobs"]["deploy"]["needs"] == "test"

    def test_deploy_only_on_main(self):
        content = self.WORKFLOW.read_text()
        assert "refs/heads/main" in content

    def test_health_check_included(self):
        content = self.WORKFLOW.read_text()
        assert "Health Check" in content
        assert "/health" in content

    def test_excludes_sensitive_files(self):
        content = self.WORKFLOW.read_text()
        assert ".env" in content
        assert "terraform" in content
