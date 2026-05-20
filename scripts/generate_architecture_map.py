"""Generate the architecture constellation map JSON from the YAML manifest.

Reads `docs/website/architecture-map.yaml`, verifies every `files:` path
exists under `src/agent_orchestrator/`, and writes
`docs/website/static/architecture-map.json` consumed by the React page.

If a path no longer exists the script exits non-zero — this keeps the curated
map honest as the codebase evolves.

Run: `python scripts/generate_architecture_map.py`
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "agent_orchestrator"
YAML_PATH = REPO_ROOT / "docs" / "website" / "architecture-map.yaml"
OUTPUT = REPO_ROOT / "docs" / "website" / "static" / "architecture-map.json"
GITHUB_BASE = "https://github.com/pjcau/agent-orchestrator/blob/main"


def _verify_files(items: list[dict]) -> list[str]:
    errors: list[str] = []
    for cluster in items:
        cid = cluster["id"]
        for item in cluster.get("items", []):
            for rel in item.get("files", []):
                target = SRC_ROOT / rel
                if not target.exists():
                    errors.append(
                        f"cluster '{cid}' item '{item['name']}': missing src/agent_orchestrator/{rel}"
                    )
    return errors


def _attach_github_urls(clusters: list[dict]) -> None:
    for cluster in clusters:
        for item in cluster.get("items", []):
            item["urls"] = [
                f"{GITHUB_BASE}/src/agent_orchestrator/{rel}" for rel in item.get("files", [])
            ]


def build_payload() -> dict:
    raw = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    clusters = raw.get("clusters", [])
    errors = _verify_files(clusters)
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(
            f"\n{len(errors)} stale file reference(s) in {YAML_PATH}.\n"
            "Update the YAML or restore the missing files."
        )
    _attach_github_urls(clusters)
    return {
        "generated_from": "docs/website/architecture-map.yaml",
        "view_box": "0 0 1000 800",
        "clusters": clusters,
        "stats": {
            "total_clusters": len(clusters),
            "total_items": sum(len(c.get("items", [])) for c in clusters),
        },
    }


def main() -> None:
    payload = build_payload()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    stats = payload["stats"]
    print(f"Wrote {OUTPUT.relative_to(REPO_ROOT)}")
    print(f"  clusters: {stats['total_clusters']}  items: {stats['total_items']}")
    for cluster in payload["clusters"]:
        print(f"  - {cluster['label']:14s} {len(cluster.get('items', [])):2d} items")


if __name__ == "__main__":
    main()
