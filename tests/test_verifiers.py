"""Unit tests for the bundled verifiers under `core.verifiers`."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.core.verifiers import (
    DependencyVerifier,
    EncodingVerifier,
    ImportVerifier,
    SyntaxVerifier,
    WorkspaceCoherenceVerifier,
)


# ---------------------------- SyntaxVerifier ----------------------------


@pytest.mark.asyncio
async def test_syntax_verifier_passes_on_valid_python(tmp_path: Path):
    (tmp_path / "ok.py").write_text("def f():\n    return 1\n")
    fails = await SyntaxVerifier().verify(tmp_path)
    assert fails == []


@pytest.mark.asyncio
async def test_syntax_verifier_flags_python_error(tmp_path: Path):
    (tmp_path / "bad.py").write_text("def f(:\n    pass\n")
    fails = await SyntaxVerifier().verify(tmp_path)
    assert len(fails) == 1
    assert fails[0].category == "py_syntax"
    assert fails[0].severity == "error"
    assert fails[0].file == "bad.py"


@pytest.mark.asyncio
async def test_syntax_verifier_passes_on_valid_json(tmp_path: Path):
    (tmp_path / "a.json").write_text('{"x": 1}\n')
    fails = await SyntaxVerifier().verify(tmp_path)
    assert fails == []


@pytest.mark.asyncio
async def test_syntax_verifier_flags_json_error(tmp_path: Path):
    (tmp_path / "broken.json").write_text("{not json}")
    fails = await SyntaxVerifier().verify(tmp_path)
    assert len(fails) == 1
    assert fails[0].category == "json_syntax"


@pytest.mark.asyncio
async def test_syntax_verifier_skips_cache_dirs(tmp_path: Path):
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.py").write_text("def f(:\n")
    fails = await SyntaxVerifier().verify(tmp_path)
    assert fails == []


@pytest.mark.asyncio
async def test_syntax_verifier_handles_unreadable_file(tmp_path: Path):
    # Binary content masquerading as .py — will fail decode → warning, not error.
    (tmp_path / "binary.py").write_bytes(b"\xff\xfe\x00\x00binary")
    fails = await SyntaxVerifier().verify(tmp_path)
    assert len(fails) == 1
    assert fails[0].severity == "warning"
    assert fails[0].category == "unreadable"


# ---------------------------- EncodingVerifier ----------------------------


@pytest.mark.asyncio
async def test_encoding_verifier_catches_literal_newlines_in_json(tmp_path: Path):
    # The exact failure mode from the 2026-05-16 task-tracker run (15 literal '\n').
    bad = (
        r'{\n  "name": "task-tracker-frontend",\n  "version": "1.0.0",\n'
        r'  "scripts": {\n    "dev": "vite",\n    "build": "vite build"\n  },\n'
        r'  "dependencies": {\n    "react": "^19"\n  }\n}'
    )
    (tmp_path / "package.json").write_text(bad)
    fails = await EncodingVerifier().verify(tmp_path)
    assert len(fails) == 1
    assert fails[0].category == "json_escape"
    assert fails[0].file == "package.json"


@pytest.mark.asyncio
async def test_encoding_verifier_clean_file_passes(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        '{\n  "name": "task-tracker",\n  "version": "1.0.0"\n}'
    )
    fails = await EncodingVerifier().verify(tmp_path)
    assert fails == []


@pytest.mark.asyncio
async def test_encoding_verifier_tolerates_legit_backslash_n_in_multiline(tmp_path: Path):
    # A normal Python file may contain `\n` as a string literal, but it's still
    # multi-line — should not trip the heuristic.
    src = 'def f():\n    s = "hello\\n"\n    t = "world\\n"\n    return s + t\n'
    (tmp_path / "ok.py").write_text(src)
    fails = await EncodingVerifier().verify(tmp_path)
    assert fails == []


@pytest.mark.asyncio
async def test_encoding_verifier_handles_tsx(tmp_path: Path):
    # The 2026-05-16 App.tsx failure mode (iter 0 version).
    bad = r"import React from 'react';\n\nconst App = () => {\n  return <div />;\n};\n\nexport default App;"
    (tmp_path / "App.tsx").write_text(bad)
    fails = await EncodingVerifier().verify(tmp_path)
    assert len(fails) == 1
    assert fails[0].category == "literal_newline"


# ---------------------------- DependencyVerifier ----------------------------


@pytest.mark.asyncio
async def test_dependency_verifier_catches_psycopg_lt_3(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("fastapi>=0.109\npsycopg>=2.9,<3\n")
    fails = await DependencyVerifier().verify(tmp_path)
    assert len(fails) == 1
    f = fails[0]
    assert f.category == "pypi_resolve"
    assert "psycopg" in f.message
    assert "psycopg2-binary" in f.detail


@pytest.mark.asyncio
async def test_dependency_verifier_accepts_psycopg_v3(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("psycopg>=3.0,<4\n")
    fails = await DependencyVerifier().verify(tmp_path)
    assert fails == []


@pytest.mark.asyncio
async def test_dependency_verifier_accepts_psycopg2_binary(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("psycopg2-binary>=2.9\n")
    fails = await DependencyVerifier().verify(tmp_path)
    assert fails == []


@pytest.mark.asyncio
async def test_dependency_verifier_ignores_comments_and_blanks(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text(
        "# Comment line\n\nfastapi>=0.109\n# psycopg<3 -- not active\n"
    )
    fails = await DependencyVerifier().verify(tmp_path)
    assert fails == []


@pytest.mark.asyncio
async def test_dependency_verifier_finds_multiple_requirements_files(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("fastapi>=0.109\n")
    (tmp_path / "requirements-dev.txt").write_text("psycopg>=2.9,<3\n")
    fails = await DependencyVerifier().verify(tmp_path)
    assert len(fails) == 1
    assert fails[0].file == "requirements-dev.txt"


@pytest.mark.asyncio
async def test_dependency_verifier_custom_pin_list(tmp_path: Path):
    # Inject a new rule without modifying the bundled registry.
    custom = {"madeup": (5, "madeup only ships v5+")}
    (tmp_path / "requirements.txt").write_text("madeup<4\n")
    fails = await DependencyVerifier(known_bad_pins=custom).verify(tmp_path)
    assert len(fails) == 1
    assert fails[0].category == "pypi_resolve"
    # The bundled rule is not active.
    (tmp_path / "requirements.txt").write_text("psycopg<3\n")
    fails = await DependencyVerifier(known_bad_pins=custom).verify(tmp_path)
    assert fails == []


# ---------------------------- ImportVerifier ----------------------------


@pytest.mark.asyncio
async def test_import_verifier_clean_when_all_deps_declared(tmp_path: Path):
    (tmp_path / "main.py").write_text("import fastapi\nimport pydantic\n")
    (tmp_path / "requirements.txt").write_text("fastapi>=0.109\npydantic>=2\n")
    fails = await ImportVerifier().verify(tmp_path)
    assert fails == []


@pytest.mark.asyncio
async def test_import_verifier_catches_missing_dep(tmp_path: Path):
    """The 2026-05-16(b) failure mode: passlib imported but never declared."""
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "models.py").write_text(
        "from passlib.context import CryptContext\n"
        "pwd_context = CryptContext(schemes=['bcrypt'])\n"
    )
    (backend / "requirements.txt").write_text("fastapi>=0.109\n")
    fails = await ImportVerifier().verify(tmp_path)
    assert len(fails) == 1
    f = fails[0]
    assert f.category == "missing_dep"
    assert "passlib" in f.message
    assert f.file == "backend/models.py"
    assert f.severity == "error"


@pytest.mark.asyncio
async def test_import_verifier_resolves_module_to_package(tmp_path: Path):
    """`from jose import jwt` → expected package `python-jose`, not `jose`."""
    (tmp_path / "crud.py").write_text("from jose import jwt\n")
    (tmp_path / "requirements.txt").write_text("fastapi>=0.109\n")
    fails = await ImportVerifier().verify(tmp_path)
    assert len(fails) == 1
    assert "'python-jose'" in fails[0].detail


@pytest.mark.asyncio
async def test_import_verifier_accepts_known_alias_in_requirements(tmp_path: Path):
    """If requirements declares `python-jose`, then `from jose import x` is fine."""
    (tmp_path / "crud.py").write_text("from jose import jwt\n")
    (tmp_path / "requirements.txt").write_text("python-jose>=3.3\n")
    fails = await ImportVerifier().verify(tmp_path)
    assert fails == []


@pytest.mark.asyncio
async def test_import_verifier_ignores_stdlib_imports(tmp_path: Path):
    (tmp_path / "main.py").write_text(
        "import os\nimport json\nfrom pathlib import Path\nfrom typing import Any\n"
    )
    fails = await ImportVerifier().verify(tmp_path)
    assert fails == []


@pytest.mark.asyncio
async def test_import_verifier_ignores_local_modules(tmp_path: Path):
    """`from routers import tasks_router` where routers/ is a sibling dir is local."""
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "main.py").write_text(
        "from routers import tasks_router\nfrom database import get_db\n"
    )
    (backend / "database.py").write_text("def get_db(): pass\n")
    routers = backend / "routers"
    routers.mkdir()
    (routers / "__init__.py").write_text("from .tasks import tasks_router\n")
    (routers / "tasks.py").write_text("tasks_router = object()\n")
    (backend / "requirements.txt").write_text("")
    fails = await ImportVerifier().verify(tmp_path)
    assert fails == []


@pytest.mark.asyncio
async def test_import_verifier_ignores_relative_imports(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from . import b\nfrom ..other import x\n")
    (pkg / "b.py").write_text("")
    fails = await ImportVerifier().verify(tmp_path)
    assert fails == []


@pytest.mark.asyncio
async def test_import_verifier_first_occurrence_wins(tmp_path: Path):
    """Multiple files importing the same missing dep should still produce one failure."""
    (tmp_path / "a.py").write_text("import passlib\n")
    (tmp_path / "b.py").write_text("import passlib\n")
    (tmp_path / "requirements.txt").write_text("")
    fails = await ImportVerifier().verify(tmp_path)
    assert len(fails) == 1
    # Sorted file traversal → a.py wins.
    assert fails[0].file == "a.py"


@pytest.mark.asyncio
async def test_import_verifier_tolerates_syntax_errors(tmp_path: Path):
    """Broken .py files are SyntaxVerifier's job; ImportVerifier just skips them."""
    (tmp_path / "broken.py").write_text("def f(:\n")
    (tmp_path / "ok.py").write_text("import fastapi\n")
    (tmp_path / "requirements.txt").write_text("fastapi\n")
    fails = await ImportVerifier().verify(tmp_path)
    assert fails == []


@pytest.mark.asyncio
async def test_import_verifier_skips_venv_and_cache(tmp_path: Path):
    venv = tmp_path / ".venv" / "lib" / "site-packages" / "thirdparty"
    venv.mkdir(parents=True)
    (venv / "__init__.py").write_text("import some_internal_dep\n")
    (tmp_path / "main.py").write_text("import fastapi\n")
    (tmp_path / "requirements.txt").write_text("fastapi\n")
    fails = await ImportVerifier().verify(tmp_path)
    assert fails == []


@pytest.mark.asyncio
async def test_import_verifier_pyproject_toml_counts_as_declaration(tmp_path: Path):
    (tmp_path / "main.py").write_text("import fastapi\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["fastapi>=0.109", "pydantic>=2"]\n'
    )
    fails = await ImportVerifier().verify(tmp_path)
    assert fails == []


@pytest.mark.asyncio
async def test_import_verifier_normalizes_package_names(tmp_path: Path):
    """`python_jose` in requirements should match `from jose import ...` via the alias."""
    (tmp_path / "x.py").write_text("from jose import jwt\n")
    (tmp_path / "requirements.txt").write_text("python_jose>=3\n")
    fails = await ImportVerifier().verify(tmp_path)
    assert fails == []


# ---------------------- WorkspaceCoherenceVerifier ----------------------


@pytest.mark.asyncio
async def test_coherence_verifier_clean_when_db_urls_match(tmp_path: Path):
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  backend:\n    environment:\n"
        "      DATABASE_URL: postgresql+psycopg2://u:p@db:5432/x\n"
    )
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "database.py").write_text(
        "import os\nDATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://u:p@db/x')\n"
    )
    fails = await WorkspaceCoherenceVerifier().verify(tmp_path)
    assert fails == []


@pytest.mark.asyncio
async def test_coherence_verifier_catches_sqlite_vs_postgres_split(tmp_path: Path):
    """The 2026-05-16(b) failure mode."""
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  backend:\n    environment:\n"
        "      DATABASE_URL: postgresql+psycopg2://tasks:tasks@db:5432/tasks\n"
    )
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "database.py").write_text(
        "import os\nDATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./tasks.db')\n"
    )
    fails = await WorkspaceCoherenceVerifier().verify(tmp_path)
    assert len(fails) == 1
    f = fails[0]
    assert f.category == "db_url_mismatch"
    assert "postgresql" in f.message
    assert "sqlite" in f.message
    assert f.file == "backend/database.py"


@pytest.mark.asyncio
async def test_coherence_verifier_accepts_environment_as_list(tmp_path: Path):
    """docker-compose `environment` may be a list of `KEY=value` strings."""
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  backend:\n    environment:\n"
        "      - DATABASE_URL=postgresql://u:p@db/x\n"
    )
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "database.py").write_text(
        "DATABASE_URL = 'postgresql+psycopg2://u:p@db/x'\n"
    )
    fails = await WorkspaceCoherenceVerifier().verify(tmp_path)
    assert fails == []


@pytest.mark.asyncio
async def test_coherence_verifier_silent_without_compose(tmp_path: Path):
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "database.py").write_text("DATABASE_URL = 'sqlite:///./x.db'\n")
    fails = await WorkspaceCoherenceVerifier().verify(tmp_path)
    assert fails == []


@pytest.mark.asyncio
async def test_coherence_verifier_silent_without_db_url_default(tmp_path: Path):
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  backend:\n    environment:\n      DATABASE_URL: postgresql://u:p@db/x\n"
    )
    (tmp_path / "main.py").write_text("def main(): pass\n")
    fails = await WorkspaceCoherenceVerifier().verify(tmp_path)
    assert fails == []


@pytest.mark.asyncio
async def test_coherence_verifier_handles_broken_yaml(tmp_path: Path):
    (tmp_path / "docker-compose.yml").write_text("services: {\nbroken")
    (tmp_path / "main.py").write_text("DATABASE_URL = 'sqlite:///x.db'\n")
    # Broken YAML is SyntaxVerifier's responsibility — coherence stays quiet.
    fails = await WorkspaceCoherenceVerifier().verify(tmp_path)
    assert fails == []


@pytest.mark.asyncio
async def test_import_verifier_psycopg2_binary_satisfies_import(tmp_path: Path):
    """The 2026-05-16(d) regression: `import psycopg2` was flagged as missing
    even though `psycopg2-binary` (which provides the same module) was
    declared. The follow-up auto-fix then added bare `psycopg2`, breaking
    pip install. Both paths must now be accepted."""
    (tmp_path / "database.py").write_text("import psycopg2\n")
    # User declared the wheel-only variant.
    (tmp_path / "requirements.txt").write_text("psycopg2-binary>=2.9\n")
    fails = await ImportVerifier().verify(tmp_path)
    assert fails == []


@pytest.mark.asyncio
async def test_import_verifier_bare_psycopg2_in_requirements_also_accepted(tmp_path: Path):
    """The inverse: user declared bare `psycopg2`. Still satisfies `import psycopg2`."""
    (tmp_path / "database.py").write_text("import psycopg2\n")
    (tmp_path / "requirements.txt").write_text("psycopg2>=2.9\n")
    fails = await ImportVerifier().verify(tmp_path)
    assert fails == []
