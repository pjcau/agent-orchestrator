"""Encoding verifier — catches the literal `\\n` corruption mode.

When an LLM emits a JSON / source file as a single line where every newline
is the two-character sequence backslash+n instead of an actual newline,
`json.loads` rejects the file and `npm ci` / `pytest` cannot run. Observed
in `frontend/package.json` of the 2026-05-16 task-tracker learning-path run.

Heuristic: a file is suspect when (a) it has fewer than 3 real newlines,
AND (b) it contains at least 4 occurrences of the literal two-character
sequence `\\n`. The "literal" requirement is critical — many shell scripts
or regex strings contain `\\n` legitimately on multi-line files.
"""

from __future__ import annotations

from pathlib import Path

from agent_orchestrator.core.verification_gate import VerifierFailure

_LITERAL_NEWLINE = "\\n"  # two characters: backslash + n
_TEXT_SUFFIXES = {
    ".py",
    ".json",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".yml",
    ".yaml",
    ".md",
    ".html",
    ".css",
    ".sh",
    ".toml",
}
_SKIP_DIR_NAMES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
}


class EncodingVerifier:
    name = "encoding"
    cost_estimate_s = 1.0

    def __init__(
        self,
        *,
        min_real_newlines: int = 3,
        min_literal_count: int = 4,
    ) -> None:
        self._min_real = min_real_newlines
        self._min_literal = min_literal_count

    async def verify(self, workdir: Path) -> list[VerifierFailure]:
        failures: list[VerifierFailure] = []
        for path in self._walk(workdir):
            rel = str(path.relative_to(workdir))
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if self._looks_corrupt(text):
                literal_count = text.count(_LITERAL_NEWLINE)
                failures.append(
                    VerifierFailure(
                        verifier=self.name,
                        severity="error",
                        category="json_escape" if path.suffix == ".json" else "literal_newline",
                        message=(
                            f"{rel}: looks like a single-line file with {literal_count} "
                            f"literal '\\n' substrings — newlines may have been over-escaped"
                        ),
                        detail=text[:512],
                        file=rel,
                        exit_code=1,
                    )
                )
        return failures

    def _looks_corrupt(self, text: str) -> bool:
        real_newlines = text.count("\n")
        if real_newlines >= self._min_real:
            return False
        literal_count = text.count(_LITERAL_NEWLINE)
        return literal_count >= self._min_literal

    def _walk(self, workdir: Path):
        for path in workdir.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in _TEXT_SUFFIXES:
                continue
            if any(part in _SKIP_DIR_NAMES for part in path.relative_to(workdir).parts):
                continue
            yield path
