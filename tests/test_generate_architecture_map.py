"""Tests for the curated architecture-map generator."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "generate_architecture_map.py"
YAML_PATH = REPO_ROOT / "docs" / "website" / "architecture-map.yaml"
SRC_ROOT = REPO_ROOT / "src" / "agent_orchestrator"


def _load_generator():
    name = "generate_architecture_map"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gen():
    return _load_generator()


def test_payload_has_six_clusters(gen):
    payload = gen.build_payload()
    assert payload["stats"]["total_clusters"] == 6
    assert payload["stats"]["total_items"] >= 30


def test_every_yaml_file_reference_exists(gen):
    payload = gen.build_payload()
    for cluster in payload["clusters"]:
        for item in cluster["items"]:
            for rel in item["files"]:
                assert (SRC_ROOT / rel).exists(), f"missing: {rel}"


def test_each_cluster_has_id_label_color_and_items(gen):
    payload = gen.build_payload()
    for cluster in payload["clusters"]:
        for field in ("id", "label", "color", "cx", "cy", "rx", "ry", "items"):
            assert field in cluster, f"cluster missing {field}"
        assert len(cluster["items"]) > 0
        for item in cluster["items"]:
            assert {"name", "files", "cx", "cy", "size", "description", "urls"} <= set(item)


def test_generator_fails_when_a_referenced_file_disappears(tmp_path, gen, monkeypatch):
    bad_yaml = tmp_path / "architecture-map.yaml"
    bad_yaml.write_text(
        "clusters:\n"
        "  - id: bogus\n"
        "    label: Bogus\n"
        "    color: '#000000'\n"
        "    cx: 0\n    cy: 0\n    rx: 1\n    ry: 1\n"
        "    items:\n"
        "      - name: Ghost\n"
        "        cx: 0\n        cy: 0\n        size: 10\n"
        "        description: missing\n"
        "        files: [core/this_file_does_not_exist.py]\n"
    )
    monkeypatch.setattr(gen, "YAML_PATH", bad_yaml)
    with pytest.raises(SystemExit):
        gen.build_payload()
