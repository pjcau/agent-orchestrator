"""Generate a dynamic feature map of the project for the Docusaurus site.

Scans `src/agent_orchestrator/{core,providers,skills,dashboard,integrations}`,
extracts module docstrings, top-level classes, and intra-package imports, and
writes a JSON file consumed by `docs/website/src/pages/feature-map.jsx`.

Run: `python scripts/generate_feature_map.py`

Output: `docs/website/static/feature-map.json`
"""

from __future__ import annotations

import ast
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "agent_orchestrator"
OUTPUT = REPO_ROOT / "docs" / "website" / "static" / "feature-map.json"

# Layer is determined by directory: harness vs app.
HARNESS_DIRS = {"core", "providers", "skills"}
APP_DIRS = {"dashboard", "integrations"}

LAYERS = {
    "core": "harness",
    "providers": "harness",
    "skills": "harness",
    "dashboard": "app",
    "integrations": "app",
}

CATEGORY_LABELS = {
    "core": "Core",
    "providers": "Provider",
    "skills": "Skill",
    "dashboard": "Dashboard",
    "integrations": "Integration",
}

GITHUB_BASE = "https://github.com/pjcau/agent-orchestrator/blob/main"


@dataclass
class FeatureNode:
    id: str
    name: str
    category: str
    layer: str
    path: str
    description: str
    classes: list[str] = field(default_factory=list)
    in_degree: int = 0
    out_degree: int = 0
    lines: int = 0
    weight: float = 1.0


@dataclass
class FeatureEdge:
    source: str
    target: str


def _module_id(rel_path: Path) -> str:
    """Convert `core/agent.py` → `core.agent` and `core/__init__.py` → `core`."""
    parts = list(rel_path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _first_paragraph(docstring: str | None) -> str:
    if not docstring:
        return ""
    para = docstring.strip().split("\n\n", 1)[0]
    return " ".join(para.split())


def _top_level_classes(tree: ast.Module) -> list[str]:
    return [n.name for n in tree.body if isinstance(n, ast.ClassDef)]


def _intra_imports(tree: ast.Module, rel_path: Path) -> set[str]:
    """Resolve `from ..core.agent import Agent` and `from .skill import Skill`
    relative to the containing package of `rel_path` (e.g. `core/agent.py` →
    package `core`), returning module IDs in the same form as `_module_id`.
    """
    parts = list(rel_path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        # __init__.py IS the package
        package_parts = parts[:-1]
    else:
        # a module inside its containing package
        package_parts = parts[:-1]

    targets: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        level = node.level
        if level > 0:
            if level - 1 > len(package_parts):
                continue
            base = package_parts[: len(package_parts) - (level - 1)]
            if module:
                base = base + module.split(".")
            resolved = ".".join(base)
        else:
            resolved = module
            if resolved.startswith("agent_orchestrator."):
                resolved = resolved[len("agent_orchestrator.") :]
        if resolved and resolved.split(".", 1)[0] in (HARNESS_DIRS | APP_DIRS):
            targets.add(resolved)
    return targets


def scan() -> tuple[list[FeatureNode], list[FeatureEdge]]:
    nodes: dict[str, FeatureNode] = {}
    edges: list[FeatureEdge] = []
    edge_set: set[tuple[str, str]] = set()

    for top in sorted(HARNESS_DIRS | APP_DIRS):
        root = SRC_ROOT / top
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            name = path.name
            if name.startswith("__") and name != "__init__.py":
                # skip __main__ etc.
                continue
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(SRC_ROOT)
            module_id = _module_id(rel)
            # skip __init__ unless it has meaningful content
            try:
                source = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            doc = _first_paragraph(ast.get_docstring(tree))
            classes = _top_level_classes(tree)
            if name == "__init__.py" and not doc and not classes:
                continue
            category = top
            line_count = source.count("\n") + 1
            node = FeatureNode(
                id=module_id,
                name=rel.stem if rel.stem != "__init__" else rel.parent.name,
                category=CATEGORY_LABELS.get(category, category),
                layer=LAYERS.get(category, "unknown"),
                path=str(rel),
                description=doc,
                classes=classes[:6],
                lines=line_count,
            )
            nodes[module_id] = node

            for target in _intra_imports(tree, rel):
                if target == module_id:
                    continue
                key = (module_id, target)
                if key in edge_set:
                    continue
                edge_set.add(key)
                edges.append(FeatureEdge(source=module_id, target=target))

    # Drop edges that point to modules we did not register.
    edges = [e for e in edges if e.target in nodes]

    # Compute in/out degree and a composite weight per node.
    # weight = in_degree * 2 + classes_count + log10(lines)
    import math

    for e in edges:
        nodes[e.source].out_degree += 1
        nodes[e.target].in_degree += 1
    for node in nodes.values():
        node.weight = round(
            1.0
            + node.in_degree * 2.0
            + len(node.classes)
            + (math.log10(max(node.lines, 1)) if node.lines else 0.0),
            2,
        )

    return list(nodes.values()), edges


def build_payload() -> dict:
    nodes, edges = scan()
    payload = {
        "generated_from": "scripts/generate_feature_map.py",
        "github_base": GITHUB_BASE,
        "layers": [
            {"id": "harness", "label": "Harness (library)"},
            {"id": "app", "label": "App (dashboard + integrations)"},
        ],
        "categories": sorted({n.category for n in nodes}),
        "nodes": [asdict(n) for n in sorted(nodes, key=lambda x: (x.layer, x.category, x.id))],
        "edges": [asdict(e) for e in edges],
        "stats": {
            "total_modules": len(nodes),
            "total_edges": len(edges),
            "by_category": {
                cat: sum(1 for n in nodes if n.category == cat)
                for cat in sorted({n.category for n in nodes})
            },
        },
    }
    return payload


def main() -> None:
    payload = build_payload()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    stats = payload["stats"]
    print(f"Wrote {OUTPUT.relative_to(REPO_ROOT)}")
    print(f"  modules: {stats['total_modules']}  edges: {stats['total_edges']}")
    for cat, count in stats["by_category"].items():
        print(f"  - {cat}: {count}")


if __name__ == "__main__":
    main()
