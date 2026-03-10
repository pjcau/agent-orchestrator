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
        assert {
            "nginx",
            "dashboard",
            "postgres",
            "redis",
            "prometheus",
            "grafana",
            "certbot",
            "node-exporter",
            "cadvisor",
        } <= services

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

    def test_prometheus_localhost_only(self):
        """Prometheus ports must be bound to 127.0.0.1 only (SSH tunnel access)."""
        config = self._load()
        ports = config["services"]["prometheus"].get("ports", [])
        for p in ports:
            assert p.startswith("127.0.0.1:"), f"Prometheus port {p} is not localhost-only"

    def test_grafana_not_exposed_directly(self):
        """Grafana must not expose ports directly (accessed via Nginx reverse proxy)."""
        config = self._load()
        assert "ports" not in config["services"]["grafana"], "Grafana should use expose, not ports"
        expose = config["services"]["grafana"].get("expose", [])
        assert "3000" in [str(p) for p in expose]

    def test_nginx_exposes_port_80(self):
        config = self._load()
        ports = config["services"]["nginx"]["ports"]
        port_numbers = [p.split(":")[0] for p in ports]
        assert "80" in port_numbers

    def test_volumes_defined(self):
        config = self._load()
        assert "pgdata" in config["volumes"]
        assert "certbot-webroot" in config["volumes"]

    def test_certbot_uses_bind_mount_certs(self):
        """Certbot must use bind mount ./certs for Let's Encrypt data."""
        config = self._load()
        certbot = config["services"]["certbot"]
        volumes = certbot["volumes"]
        assert any("./certs" in v or "letsencrypt" in v for v in volumes)


class TestNginxConfig:
    """Verify nginx reverse proxy configuration."""

    def _read(self) -> str:
        return (DOCKER_DIR / "nginx" / "nginx.conf").read_text()

    def test_config_exists(self):
        assert (DOCKER_DIR / "nginx" / "nginx.conf").exists()

    def test_ssl_config_ready(self):
        """SSL config exists (commented out), ready for when certs are available."""
        content = self._read()
        assert "ssl_certificate" in content
        assert "TLSv1.2" in content

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

    def test_domains_configured(self):
        content = self._read()
        assert "agents-orchestrator.com" in content
        assert "monitoring.agents-orchestrator.com" in content

    def test_grafana_upstream(self):
        content = self._read()
        assert "upstream grafana" in content


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

    def test_dashboard_scrape_uses_https(self):
        """Dashboard serves HTTPS (self-signed), Prometheus must use scheme: https."""
        config = yaml.safe_load((DOCKER_DIR / "prometheus" / "prometheus.yml").read_text())
        dashboard_job = [s for s in config["scrape_configs"] if s["job_name"] == "dashboard"][0]
        assert dashboard_job.get("scheme") == "https"
        assert dashboard_job.get("tls_config", {}).get("insecure_skip_verify") is True

    def test_scrapes_node_exporter(self):
        config = yaml.safe_load((DOCKER_DIR / "prometheus" / "prometheus.yml").read_text())
        jobs = [s["job_name"] for s in config["scrape_configs"]]
        assert "node" in jobs

    def test_scrapes_cadvisor(self):
        config = yaml.safe_load((DOCKER_DIR / "prometheus" / "prometheus.yml").read_text())
        jobs = [s["job_name"] for s in config["scrape_configs"]]
        assert "cadvisor" in jobs

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

    def test_all_dashboards_exist(self):
        dashboards_dir = DOCKER_DIR / "grafana" / "dashboards"
        expected = {
            "orchestrator.json",
            "api-calls.json",
            "errors.json",
            "agents.json",
            "cost-analysis.json",
            "infrastructure.json",
        }
        actual = {f.name for f in dashboards_dir.glob("*.json")}
        assert expected <= actual, f"Missing dashboards: {expected - actual}"

    def test_dashboards_valid_json(self):
        import json

        dashboards_dir = DOCKER_DIR / "grafana" / "dashboards"
        for f in dashboards_dir.glob("*.json"):
            data = json.loads(f.read_text())
            assert "panels" in data, f"{f.name} missing panels"
            assert "uid" in data, f"{f.name} missing uid"
            assert "title" in data, f"{f.name} missing title"


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

    def test_nginx_restart_after_deploy(self):
        """Nginx must be restarted after dashboard rebuild to pick up new container IP."""
        content = self.WORKFLOW.read_text()
        assert "restart nginx" in content

    def test_excludes_sensitive_files(self):
        content = self.WORKFLOW.read_text()
        assert ".env" in content
        assert "terraform" in content
