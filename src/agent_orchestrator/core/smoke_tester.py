"""Smoke tester — detect a project's language and run a deps-free syntax check.

Runs between the sub-agent fan-out and the summary step in `run_team`.
The goal is to catch broken code (hallucinated deps, missing imports,
syntax errors, unwired routers) that the LLM summary would otherwise
declare as "success".

Design goals:
- **Generic**: 20 languages covered via a data-driven spec table. Nothing
  task-specific. Same code works on a FastAPI app, a Rust CLI, a Go
  microservice, a Node script — detection comes from config files +
  conventional entry-point filenames, not from prompt content.
- **Graceful**: if the toolchain (python3, cargo, javac, ...) is not on
  PATH, the smoke test *skips with a reason* — it NEVER raises.
- **Deps-free where possible**: uses syntax-only commands (`py_compile`,
  `node --check`, `bash -n`, `php -l`, `gcc -fsyntax-only`, ...) so the
  test doesn't require a full `pip install` or `npm ci` first.
- **Pure**: lives in the harness layer (`core/`). No FastAPI/dashboard
  imports. Callable from any context.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LanguageSpec:
    """Detection + smoke-test recipe for one language.

    `config_files`: presence in the project root is a strong signal the
        project is written in this language. Empty tuple means detection
        relies on `entry_patterns` only (low-priority fallback).
    `entry_patterns`: relative paths tried in order. First file that
        exists is used as the smoke-test target.
    `binary`: executable that must be on PATH for the check to run.
    `syntax_cmd`: argv to execute, with `{entry}` substituted for the
        resolved entry point. Commands should be as deps-free as possible
        (e.g. `python -m py_compile` instead of `python -c 'import x'`).
    `uses_entry`: whether the command needs the entry path substituted.
        Some commands (cargo check, go vet) operate on the whole project.
    """

    name: str
    config_files: tuple[str, ...]
    entry_patterns: tuple[str, ...]
    binary: str
    syntax_cmd: tuple[str, ...]
    uses_entry: bool = True


@dataclass
class SmokeResult:
    language: str | None
    entry_point: str | None
    success: bool
    stdout: str = ""
    stderr: str = ""
    skipped_reason: str | None = None
    exit_code: int | None = None

    @property
    def as_feedback(self) -> str:
        """Compact one-line summary for logs + re-delegation prompts."""
        if self.skipped_reason:
            return f"smoke-test skipped ({self.language or 'unknown'}): {self.skipped_reason}"
        loc = f"{self.language} @ {self.entry_point}" if self.entry_point else self.language
        if self.success:
            return f"smoke-test PASSED ({loc})"
        err = (self.stderr or self.stdout or "").strip()
        return f"smoke-test FAILED ({loc}, exit={self.exit_code}):\n{err[:500]}"


# ---------------------------------------------------------------------------
# 20 languages — ordered by detection priority
# Higher-signal configs (strong framework indicators) come first so they win
# when a repo contains multiple config files (e.g. a polyglot project with
# both pyproject.toml and package.json).
# ---------------------------------------------------------------------------

LANGUAGE_SPECS: tuple[LanguageSpec, ...] = (
    LanguageSpec(
        name="python",
        config_files=("pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "requirements.txt"),
        entry_patterns=(
            "main.py",
            "app.py",
            "__main__.py",
            "manage.py",
            "src/main.py",
            "src/app.py",
            "backend/main.py",
            "backend/app.py",
            "backend/__main__.py",
        ),
        binary="python3",
        syntax_cmd=("python3", "-m", "py_compile", "{entry}"),
    ),
    LanguageSpec(
        name="rust",
        config_files=("Cargo.toml",),
        entry_patterns=("src/main.rs", "src/lib.rs"),
        binary="cargo",
        syntax_cmd=("cargo", "check", "--quiet", "--offline"),
        uses_entry=False,
    ),
    LanguageSpec(
        name="go",
        config_files=("go.mod",),
        entry_patterns=("main.go", "cmd/main.go", "cmd/*/main.go"),
        binary="go",
        syntax_cmd=("go", "vet", "./..."),
        uses_entry=False,
    ),
    LanguageSpec(
        name="typescript",
        config_files=("tsconfig.json",),
        entry_patterns=(
            "src/index.ts",
            "src/main.ts",
            "index.ts",
            "src/App.tsx",
            "src/main.tsx",
            "frontend/src/main.tsx",
            "frontend/src/App.tsx",
        ),
        binary="node",
        # `node --check` does not parse TSX/TS semantics but catches lex-level
        # problems. Full typecheck requires `tsc` + deps which we avoid here.
        syntax_cmd=("node", "--check", "{entry}"),
    ),
    LanguageSpec(
        name="javascript",
        config_files=("package.json",),
        entry_patterns=(
            "index.js",
            "src/index.js",
            "src/main.js",
            "server.js",
            "frontend/src/main.js",
            "frontend/index.js",
        ),
        binary="node",
        syntax_cmd=("node", "--check", "{entry}"),
    ),
    LanguageSpec(
        name="csharp",
        config_files=("global.json",),  # *.csproj handled specially in detect
        entry_patterns=("Program.cs", "src/Program.cs"),
        binary="dotnet",
        syntax_cmd=("dotnet", "build", "--no-restore", "--nologo", "-v", "q"),
        uses_entry=False,
    ),
    LanguageSpec(
        name="java",
        config_files=("pom.xml", "build.gradle", "build.gradle.kts"),
        entry_patterns=(
            "src/main/java/Main.java",
            "src/main/java/App.java",
            "Main.java",
        ),
        binary="javac",
        syntax_cmd=("javac", "-d", "/tmp/.smoke", "{entry}"),
    ),
    LanguageSpec(
        name="kotlin",
        config_files=("build.gradle.kts", "settings.gradle.kts"),
        entry_patterns=("src/main/kotlin/Main.kt", "Main.kt"),
        binary="kotlinc",
        syntax_cmd=("kotlinc", "-nowarn", "-d", "/tmp/.smoke", "{entry}"),
    ),
    LanguageSpec(
        name="scala",
        config_files=("build.sbt",),
        entry_patterns=("src/main/scala/Main.scala", "Main.scala"),
        binary="scalac",
        syntax_cmd=("scalac", "-Ystop-after:parser", "{entry}"),
    ),
    LanguageSpec(
        name="swift",
        config_files=("Package.swift",),
        entry_patterns=("Sources/main.swift", "main.swift"),
        binary="swiftc",
        syntax_cmd=("swiftc", "-parse", "{entry}"),
    ),
    LanguageSpec(
        name="dart",
        config_files=("pubspec.yaml",),
        entry_patterns=("bin/main.dart", "lib/main.dart"),
        binary="dart",
        syntax_cmd=("dart", "analyze", "{entry}"),
    ),
    LanguageSpec(
        name="php",
        config_files=("composer.json",),
        entry_patterns=("index.php", "public/index.php", "src/index.php"),
        binary="php",
        syntax_cmd=("php", "-l", "{entry}"),
    ),
    LanguageSpec(
        name="ruby",
        config_files=("Gemfile", "Rakefile"),
        entry_patterns=("app.rb", "config.ru", "main.rb", "lib/main.rb"),
        binary="ruby",
        syntax_cmd=("ruby", "-c", "{entry}"),
    ),
    LanguageSpec(
        name="elixir",
        config_files=("mix.exs",),
        entry_patterns=("lib/application.ex", "lib/main.ex"),
        binary="elixir",
        # `elixir --no-compile` just parses; `elixirc` would compile.
        syntax_cmd=("elixir", "--no-halt", "-e", 'Code.compile_file("{entry}")'),
    ),
    LanguageSpec(
        name="haskell",
        config_files=("stack.yaml", "cabal.project"),
        entry_patterns=("app/Main.hs", "src/Main.hs", "Main.hs"),
        binary="ghc",
        syntax_cmd=("ghc", "-fno-code", "{entry}"),
    ),
    LanguageSpec(
        name="r",
        config_files=("DESCRIPTION", "renv.lock"),
        entry_patterns=("main.R", "src/main.R", "R/main.R"),
        binary="Rscript",
        syntax_cmd=("Rscript", "-e", 'parse(file="{entry}")'),
    ),
    LanguageSpec(
        name="lua",
        config_files=(),  # no universally-adopted config file
        entry_patterns=("main.lua", "init.lua", "src/main.lua"),
        binary="luac",
        syntax_cmd=("luac", "-p", "{entry}"),
    ),
    LanguageSpec(
        name="cpp",
        config_files=("CMakeLists.txt",),
        entry_patterns=("main.cpp", "src/main.cpp"),
        binary="g++",
        syntax_cmd=("g++", "-fsyntax-only", "-std=c++17", "{entry}"),
    ),
    LanguageSpec(
        name="c",
        config_files=("Makefile", "configure.ac", "configure"),
        entry_patterns=("main.c", "src/main.c"),
        binary="gcc",
        syntax_cmd=("gcc", "-fsyntax-only", "{entry}"),
    ),
    LanguageSpec(
        name="shell",
        config_files=(),
        entry_patterns=(
            "run.sh",
            "main.sh",
            "start.sh",
            "entrypoint.sh",
            "bin/run.sh",
            "scripts/run.sh",
        ),
        binary="bash",
        syntax_cmd=("bash", "-n", "{entry}"),
    ),
)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _matches_spec_config(cwd: Path, spec: LanguageSpec, entry: str | None = None) -> bool:
    """True if at least one of the spec's config files exists near the project root.

    "Near" means: in `cwd` itself, OR in any ancestor of `entry` up to `cwd`.
    This matters for polyglot / subdirectory layouts like::

        repo/
        ├── backend/requirements.txt  ← config here
        ├── backend/main.py           ← entry here
        └── frontend/package.json

    where the config is next to the entry, not at the top level.

    Special cases:
    - `csharp`: any `*.csproj` file counts (no single fixed name). Searched
      recursively up to depth 2.
    - `lua` / `shell`: no config file required; match on entry patterns only.
    """
    if spec.name == "csharp":
        # csproj can live at any depth in typical .NET repos
        for depth1 in list(cwd.iterdir()) + [cwd]:
            if depth1.is_file() and depth1.suffix == ".csproj":
                return True
            if depth1.is_dir():
                for p in depth1.iterdir():
                    if p.is_file() and p.suffix == ".csproj":
                        return True
        return False
    if not spec.config_files:
        return True
    # cwd itself
    if any((cwd / cf).exists() for cf in spec.config_files):
        return True
    # ancestors of entry, up to (and excluding) cwd
    if entry:
        entry_path = (cwd / entry).resolve()
        for parent in entry_path.parents:
            if parent == cwd.parent or not str(parent).startswith(str(cwd)):
                break
            if parent == cwd:
                break
            if any((parent / cf).exists() for cf in spec.config_files):
                return True
    return False


def _find_entry(cwd: Path, spec: LanguageSpec) -> str | None:
    """First entry pattern that resolves to an existing file."""
    for pat in spec.entry_patterns:
        if "*" in pat:
            # Limited glob — only single-star in a single path component.
            matches = sorted(cwd.glob(pat))
            for m in matches:
                if m.is_file():
                    return str(m.relative_to(cwd))
            continue
        p = cwd / pat
        if p.is_file():
            return pat
    return None


def detect_language(cwd: Path) -> tuple[LanguageSpec | None, str | None]:
    """Return (spec, entry_path) or (None, None) if no known language matches.

    Priority:
    1. A spec whose config file matches AND an entry pattern matches — strongest signal.
    2. A spec with empty config_files (shell, lua) whose entry pattern matches — fallback.
    """
    # Pass 1: require both config and entry (config can live next to the entry,
    # not only in cwd — see _matches_spec_config docstring).
    for spec in LANGUAGE_SPECS:
        if spec.config_files or spec.name == "csharp":
            entry = _find_entry(cwd, spec)
            if entry and _matches_spec_config(cwd, spec, entry):
                return spec, entry

    # Pass 2: config-less specs (shell, lua) with matching entry
    for spec in LANGUAGE_SPECS:
        if not spec.config_files and spec.name != "csharp":
            entry = _find_entry(cwd, spec)
            if entry:
                return spec, entry

    return None, None


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


async def run_smoke_test(cwd: str | Path, timeout: float = 30.0) -> SmokeResult:
    """Detect the project's language and run a syntax-only smoke test.

    Never raises. Failures, timeouts, and missing toolchains all translate
    into a `SmokeResult` with structured fields.
    """
    cwd_path = Path(cwd).resolve()
    if not cwd_path.is_dir():
        return SmokeResult(None, None, True, skipped_reason=f"cwd is not a directory: {cwd}")

    spec, entry = detect_language(cwd_path)
    if spec is None or entry is None:
        return SmokeResult(None, None, True, skipped_reason="no known language detected")

    if not shutil.which(spec.binary):
        return SmokeResult(
            language=spec.name,
            entry_point=entry,
            success=True,
            skipped_reason=f"toolchain '{spec.binary}' not on PATH",
        )

    cmd = [part.format(entry=entry) if spec.uses_entry else part for part in spec.syntax_cmd]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return SmokeResult(
            language=spec.name,
            entry_point=entry,
            success=(proc.returncode == 0),
            stdout=stdout_b.decode(errors="replace"),
            stderr=stderr_b.decode(errors="replace"),
            exit_code=proc.returncode,
        )
    except asyncio.TimeoutError:
        return SmokeResult(
            language=spec.name,
            entry_point=entry,
            success=False,
            stderr=f"smoke-test timed out after {timeout}s",
            exit_code=None,
        )
    except FileNotFoundError as e:
        # Race between shutil.which() and exec.
        return SmokeResult(
            language=spec.name,
            entry_point=entry,
            success=True,
            skipped_reason=f"exec failed: {e}",
        )
    except Exception as e:  # noqa: BLE001 — defensive: smoke-test must never raise
        return SmokeResult(
            language=spec.name,
            entry_point=entry,
            success=False,
            stderr=f"smoke-test error: {type(e).__name__}: {e}",
        )


# ---------------------------------------------------------------------------
# Routing hint — used by run_team to pick a re-delegation agent when the
# smoke test fails. Kept here to stay generic (no dashboard imports).
# ---------------------------------------------------------------------------

_AGENT_FOR_LANGUAGE: dict[str, str] = {
    "python": "backend",
    "rust": "backend",
    "go": "backend",
    "java": "backend",
    "kotlin": "backend",
    "scala": "backend",
    "csharp": "backend",
    "cpp": "backend",
    "c": "backend",
    "php": "backend",
    "ruby": "backend",
    "elixir": "backend",
    "haskell": "backend",
    "r": "data-analyst",
    "javascript": "frontend",
    "typescript": "frontend",
    "dart": "frontend",
    "swift": "frontend",
    "lua": "backend",
    "shell": "devops",
}


def suggest_agent_for_language(language: str | None) -> str:
    """Best-guess agent name to hand a failed smoke test back to."""
    if not language:
        return "backend"
    return _AGENT_FOR_LANGUAGE.get(language, "backend")
