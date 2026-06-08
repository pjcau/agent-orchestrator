"""Tests for the AWS cost exporter's on-disk cache.

The exporter (`docker/aws-cost-exporter/exporter.py`) is a standalone script,
not part of the package, so we load it by path. The cache is what keeps a
container restart from re-issuing paid Cost Explorer API calls — these tests
pin that behaviour.
"""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

EXPORTER_PATH = (
    Path(__file__).resolve().parent.parent / "docker" / "aws-cost-exporter" / "exporter.py"
)


@pytest.fixture
def exporter(tmp_path, monkeypatch):
    """Load the exporter module fresh with a temp cache path."""
    spec = importlib.util.spec_from_file_location("aws_cost_exporter", EXPORTER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "COST_CACHE_PATH", str(tmp_path / "costs.json"))
    # Reset the in-memory stores so tests don't leak into each other.
    mod._metrics.clear()
    mod._service_costs_today.clear()
    mod._service_costs_yesterday.clear()
    return mod


def test_cache_roundtrip(exporter):
    exporter._save_cost_cache(13.5, 59.4, {"EC2": 1.2}, {"EC2": 1.1})
    loaded = exporter._load_cost_cache()
    assert loaded["monthly_usd"] == 13.5
    assert loaded["forecast_usd"] == 59.4
    assert loaded["today"] == {"EC2": 1.2}
    assert loaded["yesterday"] == {"EC2": 1.1}
    assert isinstance(loaded["fetched_at"], (int, float))


def test_load_missing_cache_returns_none(exporter):
    assert exporter._load_cost_cache() is None


def test_freshness_window(exporter):
    now = 1_000_000.0
    fresh = {"fetched_at": now - 100}
    stale = {"fetched_at": now - (exporter.REFRESH_INTERVAL + 1)}
    assert exporter._cost_cache_is_fresh(fresh, now=now)
    assert not exporter._cost_cache_is_fresh(stale, now=now)
    assert not exporter._cost_cache_is_fresh(None, now=now)
    assert not exporter._cost_cache_is_fresh({"fetched_at": "bad"}, now=now)


def test_fetch_uses_fresh_cache_without_calling_ce(exporter, monkeypatch):
    # A fresh cache must short-circuit before any Cost Explorer client is made.
    exporter._save_cost_cache(20.0, 60.0, {"EC2": 2.0}, {})
    ce_factory = MagicMock(side_effect=AssertionError("CE must not be called on cache hit"))
    monkeypatch.setattr(exporter, "_get_ce_client", ce_factory)

    exporter._fetch_costs()  # not forced

    ce_factory.assert_not_called()
    # Cached values were applied to the live metrics.
    assert exporter._metrics["aws_cost_monthly_usd"] == 20.0
    assert exporter._metrics["aws_cost_monthly_forecast_usd"] == 60.0
    assert exporter._service_costs_today == {"EC2": 2.0}


def test_stale_cache_triggers_ce_fetch_and_rewrites_cache(exporter, monkeypatch):
    # Write a stale cache, then a fetch must hit CE and refresh the cache.
    exporter._save_cost_cache(1.0, 2.0, {}, {}, now=time.time() - (exporter.REFRESH_INTERVAL + 10))

    ce = MagicMock()
    ce.get_cost_and_usage.return_value = {"ResultsByTime": [{"Groups": []}]}
    ce.get_cost_forecast.return_value = {"Total": {"Amount": "0.0"}}
    monkeypatch.setattr(exporter, "_get_ce_client", lambda: ce)

    exporter._fetch_costs()

    assert ce.get_cost_and_usage.called
    # Cache was rewritten with a fresh timestamp → next call would be a hit.
    assert exporter._cost_cache_is_fresh(exporter._load_cost_cache())


def test_force_bypasses_fresh_cache(exporter, monkeypatch):
    exporter._save_cost_cache(99.0, 99.0, {}, {})
    ce = MagicMock()
    ce.get_cost_and_usage.return_value = {"ResultsByTime": [{"Groups": []}]}
    ce.get_cost_forecast.return_value = {"Total": {"Amount": "0.0"}}
    monkeypatch.setattr(exporter, "_get_ce_client", lambda: ce)

    exporter._fetch_costs(force=True)

    assert ce.get_cost_and_usage.called
