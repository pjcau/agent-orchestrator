"""Unit tests for the bundled verifiers under `core.verifiers`."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.core.verifiers import (
    DependencyVerifier,
    EncodingVerifier,
    SyntaxVerifier,
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
