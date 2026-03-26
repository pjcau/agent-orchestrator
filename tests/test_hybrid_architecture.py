"""Tests for hybrid architecture (React frontend + Rust core engine).

Verifies build configuration, directory structure, and integration points.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


class TestDockerfileMultiStage:
    """Verify Dockerfile has multi-stage build for React + Rust + Python."""

    DOCKERFILE = ROOT / "docker" / "dashboard" / "Dockerfile"

    def test_dockerfile_exists(self):
        assert self.DOCKERFILE.exists()

    def test_has_frontend_build_stage(self):
        content = self.DOCKERFILE.read_text()
        assert "frontend-build" in content
        assert "node:" in content
        assert "npm" in content

    def test_has_rust_build_stage(self):
        content = self.DOCKERFILE.read_text()
        assert "rust-build" in content
        assert "maturin" in content
        assert "rustup" in content

    def test_copies_react_dist(self):
        content = self.DOCKERFILE.read_text()
        assert "frontend/dist" in content

    def test_rust_is_graceful(self):
        """Rust build failure should not break the Docker build."""
        content = self.DOCKERFILE.read_text()
        assert "|| true" in content or "|| echo" in content


class TestPyprojectRustExtra:
    """Verify pyproject.toml includes Rust optional dependency."""

    PYPROJECT = ROOT / "pyproject.toml"

    def test_rust_extra_defined(self):
        content = self.PYPROJECT.read_text()
        assert "rust" in content
        assert "agent-orchestrator-rust" in content


class TestFrontendStructure:
    """Verify frontend directory structure (files created by frontend agent)."""

    FRONTEND = ROOT / "frontend"

    def test_frontend_dir_exists(self):
        # This test will pass once the frontend agent creates the directory
        if not self.FRONTEND.exists():
            pytest.skip("Frontend not yet created")
        assert self.FRONTEND.is_dir()

    def test_package_json_exists(self):
        pkg = self.FRONTEND / "package.json"
        if not pkg.exists():
            pytest.skip("Frontend not yet created")
        assert pkg.exists()

    def test_vite_config_exists(self):
        cfg = self.FRONTEND / "vite.config.ts"
        if not cfg.exists():
            pytest.skip("Frontend not yet created")
        assert cfg.exists()


class TestRustCrateStructure:
    """Verify Rust crate directory structure (files created by Rust agent)."""

    RUST = ROOT / "rust"

    def test_rust_dir_exists(self):
        if not self.RUST.exists():
            pytest.skip("Rust crate not yet created")
        assert self.RUST.is_dir()

    def test_cargo_toml_exists(self):
        cargo = self.RUST / "Cargo.toml"
        if not cargo.exists():
            pytest.skip("Rust crate not yet created")
        content = cargo.read_text()
        assert "pyo3" in content
        assert "_agent_orchestrator_rust" in content

    def test_rust_pyproject_exists(self):
        pyproject = self.RUST / "pyproject.toml"
        if not pyproject.exists():
            pytest.skip("Rust crate not yet created")
        content = pyproject.read_text()
        assert "maturin" in content


class TestAppDualServe:
    """Verify app.py has dual-serve logic for React/vanilla JS."""

    APP_PY = ROOT / "src" / "agent_orchestrator" / "dashboard" / "app.py"

    def test_react_dist_path_in_app(self):
        content = self.APP_PY.read_text()
        assert "react_dist" in content
        assert "frontend" in content

    def test_fallback_to_static(self):
        content = self.APP_PY.read_text()
        assert "STATIC_DIR" in content
        # Both paths should serve index.html
        assert content.count("index.html") >= 2


class TestFallbackImports:
    """Verify all core modules have try/except import for Rust."""

    def test_graph_has_rust_import(self):
        content = (ROOT / "src" / "agent_orchestrator" / "core" / "graph.py").read_text()
        assert "_agent_orchestrator_rust" in content
        assert "_HAS_RUST" in content

    def test_router_has_rust_import(self):
        content = (ROOT / "src" / "agent_orchestrator" / "core" / "router.py").read_text()
        assert "_agent_orchestrator_rust" in content
        assert "_HAS_RUST" in content

    def test_task_queue_has_rust_import(self):
        content = (ROOT / "src" / "agent_orchestrator" / "core" / "task_queue.py").read_text()
        assert "_agent_orchestrator_rust" in content
        assert "_HAS_RUST" in content

    def test_rate_limiter_has_rust_import(self):
        content = (ROOT / "src" / "agent_orchestrator" / "core" / "rate_limiter.py").read_text()
        assert "_agent_orchestrator_rust" in content
        assert "_HAS_RUST_RL" in content

    def test_metrics_has_rust_import(self):
        content = (ROOT / "src" / "agent_orchestrator" / "core" / "metrics.py").read_text()
        assert "_agent_orchestrator_rust" in content
        assert "_HAS_RUST_METRICS" in content
