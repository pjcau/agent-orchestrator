"""Tests for the dynamic feature map generator."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "generate_feature_map.py"


def _load_generator():
    name = "generate_feature_map"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses need cls.__module__ to be importable
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gen():
    return _load_generator()


def test_payload_has_required_keys(gen):
    payload = gen.build_payload()
    for key in ("nodes", "edges", "categories", "layers", "stats", "github_base"):
        assert key in payload, f"missing key: {key}"


def test_nodes_have_required_fields(gen):
    payload = gen.build_payload()
    assert payload["nodes"], "no nodes were discovered — generator broken?"
    required = {
        "id", "name", "category", "layer", "path", "description", "classes",
        "weight", "in_degree", "out_degree", "lines",
    }
    for node in payload["nodes"]:
        assert set(node) >= required
        assert node["layer"] in {"harness", "app"}
        assert node["category"] in {"Core", "Provider", "Skill", "Dashboard", "Integration"}
        assert node["weight"] >= 1.0, "weight is built from a baseline of 1"
        assert node["lines"] >= 0


def test_weight_grows_with_in_degree(gen):
    payload = gen.build_payload()
    by_id = {n["id"]: n for n in payload["nodes"]}
    # core.provider is imported by every provider implementation + core.agent,
    # so it must have a non-trivial weight.
    assert by_id["core.provider"]["in_degree"] >= 5
    assert by_id["core.provider"]["weight"] > by_id["core.provider"]["in_degree"]


def test_harness_modules_never_in_app_paths(gen):
    payload = gen.build_payload()
    for node in payload["nodes"]:
        if node["layer"] == "harness":
            assert not node["path"].startswith("dashboard/")
            assert not node["path"].startswith("integrations/")
        else:
            assert node["path"].startswith(("dashboard/", "integrations/"))


def test_edges_reference_existing_nodes(gen):
    payload = gen.build_payload()
    node_ids = {n["id"] for n in payload["nodes"]}
    for edge in payload["edges"]:
        assert edge["source"] in node_ids
        assert edge["target"] in node_ids


def test_harness_nodes_do_not_import_from_app_layer(gen):
    """Mirrors tests/test_import_boundary.py: harness modules MUST NOT import
    from `dashboard/` or `integrations/`. The feature map is a useful sanity
    check that this invariant still holds via the edge list.
    """
    payload = gen.build_payload()
    by_id = {n["id"]: n for n in payload["nodes"]}
    for edge in payload["edges"]:
        source = by_id[edge["source"]]
        target = by_id[edge["target"]]
        if source["layer"] == "harness":
            assert target["layer"] == "harness", (
                f"harness module {source['id']} imports app module {target['id']}"
            )


def test_payload_can_be_serialized_to_disk(tmp_path, gen):
    out = tmp_path / "feature-map.json"
    payload = gen.build_payload()
    out.write_text(json.dumps(payload))
    data = json.loads(out.read_text())
    assert data["stats"]["total_modules"] > 0
    assert data["nodes"], "expected at least one node"
