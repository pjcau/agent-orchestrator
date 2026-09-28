"""HTTP tests for the P2 Evaluator API (/api/evals/*).

Uses httpx + ASGITransport (same pattern as tests/test_dashboard.py).
No real LLM calls — agent execution is stubbed via app.state.eval_agent_factory.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from agent_orchestrator.core.evaluator import EvalCase, EvalRun

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_suite(cases: list[dict] | None = None) -> Path:
    """Write a minimal suite JSON file and return its path."""
    if cases is None:
        cases = [
            {"prompt": "What is 2+2?", "expected": "4", "metadata": {"case_id": "math"}},
            {"prompt": "Capital of France?", "expected": "Paris", "metadata": {"case_id": "geo"}},
        ]
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({"cases": cases}, tmp)
    tmp.close()
    return Path(tmp.name)


def _stub_factory(agent_name: str, model: str, provider: str):
    """Return a stub agent callable that always answers with expected text."""

    async def _agent(case: EvalCase) -> EvalRun:
        await asyncio.sleep(0)
        cid = str(case.metadata.get("case_id", "unknown"))
        return EvalRun(case_id=cid, agent_output=case.expected or "ok", ok=True)

    return _agent


def _make_app():
    import os

    os.environ.setdefault("ALLOW_DEV_MODE", "true")
    from agent_orchestrator.dashboard.app import create_dashboard_app

    app = create_dashboard_app()
    app.state.eval_agent_factory = _stub_factory
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEvalsRoutes:
    @pytest.mark.asyncio
    async def test_post_run_returns_run_id(self, monkeypatch):
        """POST /api/evals/run returns a run_id immediately (202)."""
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")
        suite_path = str(_write_suite())
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/evals/run",
                json={"suite_path": suite_path, "agent": "team-lead"},
            )
        assert resp.status_code == 202
        data = resp.json()
        assert "run_id" in data
        assert len(data["run_id"]) > 0

    @pytest.mark.asyncio
    async def test_post_run_missing_suite_path_returns_400(self, monkeypatch):
        """POST /api/evals/run with no suite_path returns 400."""
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/evals/run", json={"agent": "team-lead"})
        assert resp.status_code == 400
        assert "suite_path" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_list_runs_empty_initially(self, monkeypatch):
        """GET /api/evals/runs returns empty list on a fresh app."""
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/evals/runs")
        assert resp.status_code == 200
        assert resp.json()["runs"] == []

    @pytest.mark.asyncio
    async def test_get_run_not_found_returns_404(self, monkeypatch):
        """GET /api/evals/runs/<unknown> returns 404."""
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/evals/runs/nonexistent-run-id")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_compare_missing_params_returns_400(self, monkeypatch):
        """GET /api/evals/compare without a or b returns 400."""
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/evals/compare?a=foo")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_compare_unknown_runs_returns_404(self, monkeypatch):
        """GET /api/evals/compare?a=x&b=y returns 404 when runs don't exist."""
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/evals/compare?a=no-such-a&b=no-such-b")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_full_run_and_retrieve_flow(self, monkeypatch):
        """Trigger a run, wait for it to complete, then retrieve the full report."""
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")
        suite_path = str(_write_suite())
        app = _make_app()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Trigger run
            post_resp = await client.post(
                "/api/evals/run",
                json={"suite_path": suite_path, "agent": "team-lead"},
            )
            assert post_resp.status_code == 202
            run_id = post_resp.json()["run_id"]

            # Wait briefly for the background task to complete
            for _ in range(30):
                await asyncio.sleep(0.05)
                get_resp = await client.get(f"/api/evals/runs/{run_id}")
                assert get_resp.status_code == 200
                body = get_resp.json()
                if body.get("status") != "pending" and "runs" in body:
                    break
            else:
                pytest.fail("Timed out waiting for eval run to complete")

            assert body["suite"] is not None
            assert "summary" in body

    @pytest.mark.asyncio
    async def test_compare_two_runs(self, monkeypatch):
        """Trigger two runs, compare them, check delta keys."""
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")
        suite_path = str(_write_suite())
        app = _make_app()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Start run A
            r_a = await client.post("/api/evals/run", json={"suite_path": suite_path})
            run_a = r_a.json()["run_id"]

            # Start run B
            r_b = await client.post("/api/evals/run", json={"suite_path": suite_path})
            run_b = r_b.json()["run_id"]

            # Wait for both to finish
            for _ in range(60):
                await asyncio.sleep(0.05)
                a_body = (await client.get(f"/api/evals/runs/{run_a}")).json()
                b_body = (await client.get(f"/api/evals/runs/{run_b}")).json()
                if (
                    a_body.get("status") != "pending"
                    and b_body.get("status") != "pending"
                    and "runs" in a_body
                    and "runs" in b_body
                ):
                    break
            else:
                pytest.fail("Timed out waiting for both eval runs to complete")

            compare_resp = await client.get(f"/api/evals/compare?a={run_a}&b={run_b}")

        assert compare_resp.status_code == 200
        cmp = compare_resp.json()
        assert "a" in cmp
        assert "b" in cmp
        assert "delta" in cmp
        assert "pass_rate" in cmp["delta"]
        assert "mean_score" in cmp["delta"]

    @pytest.mark.asyncio
    async def test_list_runs_shows_run_after_completion(self, monkeypatch):
        """GET /api/evals/runs includes a completed run."""
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")
        suite_path = str(_write_suite())
        app = _make_app()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            post_resp = await client.post("/api/evals/run", json={"suite_path": suite_path})
            run_id = post_resp.json()["run_id"]

            # Wait for completion
            for _ in range(30):
                await asyncio.sleep(0.05)
                runs_resp = await client.get("/api/evals/runs")
                runs = runs_resp.json()["runs"]
                run_entry = next((r for r in runs if r["run_id"] == run_id), None)
                if run_entry and run_entry.get("status") != "pending" and "summary" in run_entry:
                    break
            else:
                pytest.fail("Timed out waiting for run to appear in list")

        assert run_entry is not None
        assert "summary" in run_entry
