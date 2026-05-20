"""Syntax verifier — parses Python and JSON files in the workspace.

Python: uses `compile(source, name, "exec")` so we don't depend on a
particular interpreter being callable. JSON: `json.loads`. Both run in-process,
no subprocess overhead.

TypeScript / TSX are intentionally NOT parsed here — that would require `tsc`
on PATH, which we cannot assume. The (cheap) `EncodingVerifier` catches the
specific failure mode this project hits in practice (literal `\\n` strings).
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_orchestrator.core.verification_gate import VerifierFailure

_SKIP_DIR_NAMES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
}


class SyntaxVerifier:
    name = "syntax"
    cost_estimate_s = 1.0

    async def verify(self, workdir: Path) -> list[VerifierFailure]:
        failures: list[VerifierFailure] = []
        for path in _walk_relevant_files(workdir):
            rel = str(path.relative_to(workdir))
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                failures.append(
                    VerifierFailure(
                        verifier=self.name,
                        severity="warning",
                        category="unreadable",
                        message=f"could not read {rel}: {type(exc).__name__}",
                        file=rel,
                    )
                )
                continue

            if path.suffix == ".py":
                fail = _check_python(text, rel)
                if fail:
                    failures.append(fail)
            elif path.suffix == ".json":
                fail = _check_json(text, rel)
                if fail:
                    failures.append(fail)

        return failures


def _walk_relevant_files(workdir: Path):
    for path in workdir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in (".py", ".json"):
            continue
        if any(part in _SKIP_DIR_NAMES for part in path.relative_to(workdir).parts):
            continue
        yield path


def _check_python(text: str, rel: str) -> VerifierFailure | None:
    try:
        compile(text, rel, "exec")
    except SyntaxError as exc:
        return VerifierFailure(
            verifier="syntax",
            severity="error",
            category="py_syntax",
            message=f"{rel}: {exc.msg} (line {exc.lineno})",
            detail=str(exc),
            file=rel,
            exit_code=1,
        )
    return None


def _check_json(text: str, rel: str) -> VerifierFailure | None:
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        return VerifierFailure(
            verifier="syntax",
            severity="error",
            category="json_syntax",
            message=f"{rel}: {exc.msg} (line {exc.lineno}, col {exc.colno})",
            detail=str(exc),
            file=rel,
            exit_code=1,
        )
    return None
