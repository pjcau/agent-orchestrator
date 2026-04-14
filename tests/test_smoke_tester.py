"""Tests for core.smoke_tester — language detection and syntax checks.

Subprocess calls are mocked so tests don't require real toolchains installed
(javac, cargo, ghc, etc.). Detection tests hit the filesystem with tmp_path.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agent_orchestrator.core.smoke_tester import (
    LANGUAGE_SPECS,
    SmokeResult,
    detect_language,
    run_smoke_test,
    suggest_agent_for_language,
)


# ---------------------------------------------------------------------------
# Detection — one test per language, driven by (config file, entry file)
# parametrisation so adding a new LanguageSpec is trivial.
# ---------------------------------------------------------------------------

# (expected_language, config_file_to_create, entry_file_to_create)
_DETECTION_CASES = [
    ("python", "pyproject.toml", "main.py"),
    ("python", "setup.py", "src/main.py"),
    ("python", "requirements.txt", "backend/main.py"),
    ("rust", "Cargo.toml", "src/main.rs"),
    ("go", "go.mod", "main.go"),
    ("typescript", "tsconfig.json", "src/index.ts"),
    ("typescript", "tsconfig.json", "src/App.tsx"),
    ("javascript", "package.json", "index.js"),
    ("javascript", "package.json", "server.js"),
    ("csharp", "MyApp.csproj", "Program.cs"),
    ("java", "pom.xml", "src/main/java/Main.java"),
    ("java", "build.gradle", "Main.java"),
    ("kotlin", "build.gradle.kts", "src/main/kotlin/Main.kt"),
    ("scala", "build.sbt", "src/main/scala/Main.scala"),
    ("swift", "Package.swift", "Sources/main.swift"),
    ("dart", "pubspec.yaml", "bin/main.dart"),
    ("php", "composer.json", "index.php"),
    ("ruby", "Gemfile", "app.rb"),
    ("elixir", "mix.exs", "lib/application.ex"),
    ("haskell", "stack.yaml", "app/Main.hs"),
    ("r", "DESCRIPTION", "main.R"),
    ("lua", None, "main.lua"),
    ("cpp", "CMakeLists.txt", "main.cpp"),
    ("c", "Makefile", "main.c"),
    ("shell", None, "run.sh"),
]


@pytest.mark.parametrize("expected,config,entry", _DETECTION_CASES)
def test_detects_language(tmp_path: Path, expected: str, config: str | None, entry: str) -> None:
    if config:
        (tmp_path / config).write_text("")
    entry_path = tmp_path / entry
    entry_path.parent.mkdir(parents=True, exist_ok=True)
    entry_path.write_text("// entry stub")

    spec, found = detect_language(tmp_path)

    assert spec is not None, f"no spec detected for {expected}"
    assert spec.name == expected
    assert found == entry


def test_empty_directory_returns_none(tmp_path: Path) -> None:
    spec, entry = detect_language(tmp_path)
    assert spec is None
    assert entry is None


def test_config_file_present_but_no_entry_returns_none(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("")
    spec, entry = detect_language(tmp_path)
    # No entry file exists: nothing matches, even though config is Python-ish.
    assert spec is None
    assert entry is None


def test_twenty_languages_defined() -> None:
    """Guard: dropping to <20 specs requires an explicit edit + test update."""
    assert len(LANGUAGE_SPECS) == 20
    names = {s.name for s in LANGUAGE_SPECS}
    expected = {
        "python", "rust", "go", "typescript", "javascript", "csharp",
        "java", "kotlin", "scala", "swift", "dart", "php", "ruby",
        "elixir", "haskell", "r", "lua", "cpp", "c", "shell",
    }
    assert names == expected


def test_config_priority_over_fallback(tmp_path: Path) -> None:
    """When both a `shell` entry and a `python` config+entry exist, python wins."""
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "main.py").write_text("")
    (tmp_path / "run.sh").write_text("")
    spec, entry = detect_language(tmp_path)
    assert spec is not None and spec.name == "python"
    assert entry == "main.py"


def test_fallback_matches_shell_when_no_config(tmp_path: Path) -> None:
    (tmp_path / "run.sh").write_text("#!/bin/bash\necho hi\n")
    spec, entry = detect_language(tmp_path)
    assert spec is not None and spec.name == "shell"


def test_detects_config_in_subdir_next_to_entry(tmp_path: Path) -> None:
    """Polyglot layouts often have `backend/requirements.txt` + `backend/main.py`
    at top level, not at the repo root. Detection must match that too."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "requirements.txt").write_text("fastapi\n")
    (tmp_path / "backend" / "main.py").write_text("app = 1\n")
    # also throw a frontend/ so both config files exist in subdirs
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text("{}")
    (tmp_path / "frontend" / "index.js").write_text("// ok")

    spec, entry = detect_language(tmp_path)
    assert spec is not None
    assert spec.name == "python"
    assert entry == "backend/main.py"


# ---------------------------------------------------------------------------
# Execution — subprocess mocked
# ---------------------------------------------------------------------------


def _mock_subprocess(returncode: int, stdout: bytes = b"", stderr: bytes = b""):
    """Factory returning an async-mock for `asyncio.create_subprocess_exec`."""

    class _FakeProc:
        def __init__(self) -> None:
            self.returncode = returncode

        async def communicate(self) -> tuple[bytes, bytes]:
            return stdout, stderr

    async def _factory(*_args, **_kwargs):
        return _FakeProc()

    return _factory


def test_run_smoke_test_success(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "main.py").write_text("print('ok')\n")

    with patch("shutil.which", return_value="/usr/bin/python3"), \
         patch("asyncio.create_subprocess_exec", side_effect=_mock_subprocess(0, b"ok", b"")):
        result = asyncio.run(run_smoke_test(tmp_path))

    assert result.success is True
    assert result.language == "python"
    assert result.entry_point == "main.py"
    assert result.exit_code == 0
    assert result.skipped_reason is None
    assert "smoke-test PASSED" in result.as_feedback


def test_run_smoke_test_failure_contains_stderr(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").write_text("fn main() { oops }")

    with patch("shutil.which", return_value="/usr/bin/cargo"), \
         patch("asyncio.create_subprocess_exec",
               side_effect=_mock_subprocess(1, b"", b"error: cannot find value `oops`")):
        result = asyncio.run(run_smoke_test(tmp_path))

    assert result.success is False
    assert result.language == "rust"
    assert result.exit_code == 1
    assert "oops" in result.stderr
    assert "FAILED" in result.as_feedback


def test_run_smoke_test_skips_when_binary_missing(tmp_path: Path) -> None:
    (tmp_path / "Package.swift").write_text("")
    (tmp_path / "Sources").mkdir()
    (tmp_path / "Sources" / "main.swift").write_text("print(\"hi\")\n")

    with patch("shutil.which", return_value=None):
        result = asyncio.run(run_smoke_test(tmp_path))

    assert result.success is True  # "skipped" is not a failure
    assert result.skipped_reason is not None
    assert "swiftc" in result.skipped_reason
    assert result.language == "swift"
    assert "skipped" in result.as_feedback


def test_run_smoke_test_no_language_detected(tmp_path: Path) -> None:
    (tmp_path / "random.txt").write_text("hello")
    result = asyncio.run(run_smoke_test(tmp_path))
    assert result.success is True
    assert result.skipped_reason == "no known language detected"
    assert result.language is None


def test_run_smoke_test_bad_cwd() -> None:
    result = asyncio.run(run_smoke_test("/nonexistent/path/abc123"))
    assert result.success is True  # graceful
    assert result.skipped_reason is not None


def test_run_smoke_test_timeout(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "main.py").write_text("")

    async def _hang(*_a, **_k):
        class _Hang:
            returncode = 0
            async def communicate(self) -> tuple[bytes, bytes]:
                await asyncio.sleep(10)
                return b"", b""
        return _Hang()

    with patch("shutil.which", return_value="/usr/bin/python3"), \
         patch("asyncio.create_subprocess_exec", side_effect=_hang):
        result = asyncio.run(run_smoke_test(tmp_path, timeout=0.05))

    assert result.success is False
    assert "timed out" in result.stderr


def test_run_smoke_test_never_raises(tmp_path: Path) -> None:
    """Any unexpected exception inside the test must be swallowed into SmokeResult."""
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "main.py").write_text("")

    async def _boom(*_a, **_k):
        raise RuntimeError("simulated crash")

    with patch("shutil.which", return_value="/usr/bin/python3"), \
         patch("asyncio.create_subprocess_exec", side_effect=_boom):
        result = asyncio.run(run_smoke_test(tmp_path))

    assert result.success is False
    assert "RuntimeError" in result.stderr


# ---------------------------------------------------------------------------
# Agent routing hint
# ---------------------------------------------------------------------------


def test_suggest_agent_covers_all_languages() -> None:
    for spec in LANGUAGE_SPECS:
        agent = suggest_agent_for_language(spec.name)
        assert agent in {"backend", "frontend", "devops", "data-analyst"}


def test_suggest_agent_unknown_language_defaults_to_backend() -> None:
    assert suggest_agent_for_language(None) == "backend"
    assert suggest_agent_for_language("klingon") == "backend"
