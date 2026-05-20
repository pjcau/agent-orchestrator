"""Import verifier — catches imports that have no matching `requirements*.txt` entry.

Why this exists
---------------

`DependencyVerifier` confirms that every line in `requirements.txt` resolves on
PyPI. It does NOT confirm that every `import X` in the source has a matching
entry in `requirements.txt`. The 2026-05-16(b) learning-path-test run hit
exactly this gap: `backend/models.py` imported `passlib.context` and
`backend/crud.py` imported `from jose import jwt`, but `requirements.txt`
declared neither `passlib` nor `python-jose`. The repair loop's gate returned
``passed`` on every iteration; the produced repo simply could not boot.

Design
------

This verifier walks every ``*.py`` under ``workdir`` (skipping caches and
virtualenvs), AST-parses each file, and collects the **top-level** module of
each import. It then drops:

- stdlib modules (``sys.stdlib_module_names``);
- relative imports (``from . import x``);
- modules that resolve to a local file or package under ``workdir``
  (sibling ``.py`` files in the same dir, or any package directory rooted at
  the workdir, the workdir's ``backend/``/``frontend/``/etc.).

What's left is "third-party". For each third-party module, it confirms a
matching entry exists in any ``requirements*.txt`` under ``workdir``. The map
``MODULE_TO_PACKAGE`` handles known package/module name mismatches
(``jose`` → ``python-jose``, ``bs4`` → ``beautifulsoup4``, etc.).

Cost
----

AST-parse + filesystem walk only — no subprocess, no network. Cheap enough
to run on every team-run. Estimated overhead ~0.2 s for a typical workspace.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from agent_orchestrator.core.verification_gate import VerifierFailure

# Known package <-> module-name mismatches. Keys are the *import* name (what
# appears at the top of a `import X` / `from X import ...`); values are the
# canonical PyPI package name that ships that module.
MODULE_TO_PACKAGE: dict[str, str] = {
    "jose": "python-jose",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "yaml": "PyYAML",
    "sklearn": "scikit-learn",
    "MySQLdb": "mysqlclient",
    "dotenv": "python-dotenv",
    "magic": "python-magic",
    "dateutil": "python-dateutil",
    "OpenSSL": "pyOpenSSL",
    "Crypto": "pycryptodome",
    # Prefer wheel-only variants so `pip install` does not need a compiler.
    # Found in 2026-05-16(d) — auto-fix added bare `psycopg2`, which needs
    # libpq-dev headers, breaking the runtime check.
    "psycopg2": "psycopg2-binary",
    "MySQLdb_binary": "mysqlclient",
    "lxml": "lxml",  # no rename, but listed for completeness
    "ujson": "ujson",
}

_STDLIB = set(sys.stdlib_module_names) | {
    # PEP 561 / common namespace packages that look like stdlib but aren't always present.
    "__future__",
    "typing_extensions",  # very commonly bundled with toolchains; harmless if listed.
}

_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", "dist", "build", ".tox", ".pytest_cache"}


class ImportVerifier:
    name = "imports"
    cost_estimate_s = 0.5

    def __init__(self, *, extra_module_aliases: dict[str, str] | None = None) -> None:
        self._aliases = {**MODULE_TO_PACKAGE, **(extra_module_aliases or {})}

    async def verify(self, workdir: Path) -> list[VerifierFailure]:
        py_files = _find_py_files(workdir)
        if not py_files:
            return []
        declared = _collect_declared_packages(workdir)
        local_modules = _collect_local_modules(workdir, py_files)
        # First-seen wins so the failure points at the first usage site.
        seen_missing: dict[str, tuple[str, int]] = {}
        failures: list[VerifierFailure] = []
        for py in py_files:
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except (SyntaxError, OSError, UnicodeDecodeError):
                # Syntax-broken files are SyntaxVerifier's job, not ours.
                continue
            rel = str(py.relative_to(workdir))
            for node, lineno, mod in _iter_imports(tree):
                if not mod:
                    continue  # relative imports (`from . import x`)
                top = mod.split(".", 1)[0]
                if top in _STDLIB:
                    continue
                if top in local_modules:
                    continue
                package = self._aliases.get(top, top)
                if _is_declared(package, declared):
                    continue
                # Defence-in-depth: an alias maps `psycopg2` → `psycopg2-binary`,
                # but a user can also declare the bare module name (`psycopg2`)
                # directly and pip will still resolve it. Accept either form so
                # we never flag a usage that pip can actually install.
                if _normalize(top) in declared:
                    continue
                if top in seen_missing:
                    continue
                seen_missing[top] = (rel, lineno)
                failures.append(
                    VerifierFailure(
                        verifier=self.name,
                        severity="error",
                        category="missing_dep",
                        message=f"No module named '{top}' (declared by no requirements*.txt)",
                        detail=(
                            f"{rel}:{lineno}: import '{mod}' resolves to top-level "
                            f"module '{top}', which is not in the Python stdlib, not "
                            f"a local module under the workdir, and has no matching "
                            f"entry in any requirements*.txt. Expected package on "
                            f"PyPI: '{package}'."
                        ),
                        file=rel,
                        exit_code=1,
                    )
                )
        return failures


# ---------------------------------------------------------------------------
# Helpers (pure functions, easy to test).
# ---------------------------------------------------------------------------


def _find_py_files(workdir: Path) -> list[Path]:
    out: list[Path] = []
    for path in workdir.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        out.append(path)
    out.sort()  # deterministic iteration → deterministic failure ordering
    return out


def _iter_imports(tree: ast.AST):
    """Yield (node, lineno, top_module_name) for every import in the AST."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node, node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # relative import — not a third-party dep
                yield node, node.lineno, None
            elif node.module:
                yield node, node.lineno, node.module


def _collect_local_modules(workdir: Path, py_files: list[Path]) -> set[str]:
    """Set of top-level module names that are LOCAL to the workdir.

    A name is local if a sibling ``X.py`` exists, or a directory ``X/``
    containing ``__init__.py`` exists, or a top-level directory ``X/`` (with
    or without ``__init__.py``) exists at the workdir or one level down (this
    catches ``backend/main.py`` importing ``from routers import ...`` even
    though ``backend/__init__.py`` is absent — common in FastAPI scaffolds).
    """
    locals_: set[str] = set()
    # Sibling-of-file local modules: a *.py next to the importer counts.
    for py in py_files:
        locals_.add(py.stem)
        # also the parent dir name, in case files reference it via package path
        parent = py.parent
        if parent != workdir:
            locals_.add(parent.name)
    # First two levels of directories — covers `backend/routers` style layouts.
    for depth1 in workdir.iterdir() if workdir.is_dir() else []:
        if depth1.is_dir() and depth1.name not in _SKIP_DIRS:
            locals_.add(depth1.name)
            for depth2 in depth1.iterdir():
                if depth2.is_dir() and depth2.name not in _SKIP_DIRS:
                    locals_.add(depth2.name)
    return locals_


def _collect_declared_packages(workdir: Path) -> set[str]:
    """Lower-cased PyPI names extracted from every requirements*.txt and
    pyproject.toml under the workdir."""
    declared: set[str] = set()
    for req in workdir.rglob("requirements*.txt"):
        if any(p in _SKIP_DIRS for p in req.parts):
            continue
        try:
            text = req.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            if "#" in line:
                line = line.split("#", 1)[0].strip()
            # Take the package name up to the first version specifier or bracket.
            for sep in ("[", "<", ">", "=", "!", "~", ";", " "):
                idx = line.find(sep)
                if idx != -1:
                    line = line[:idx]
            if line:
                declared.add(_normalize(line))
    # Best-effort pyproject.toml scan — no toml dep, just regex.
    for py_proj in workdir.rglob("pyproject.toml"):
        if any(p in _SKIP_DIRS for p in py_proj.parts):
            continue
        try:
            text = py_proj.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # Match anything that looks like a `"<package>[..]<spec>"` in a
        # dependencies list — keeps the heuristic offline.
        import re as _re
        for m in _re.finditer(r'"\s*([A-Za-z0-9][A-Za-z0-9._\-]*)', text):
            declared.add(_normalize(m.group(1)))
    return declared


def _normalize(pkg: str) -> str:
    """PyPI name normalisation: lower-case, '_' and '.' collapsed to '-'."""
    return pkg.strip().lower().replace("_", "-").replace(".", "-")


def _is_declared(package: str, declared: set[str]) -> bool:
    return _normalize(package) in declared
