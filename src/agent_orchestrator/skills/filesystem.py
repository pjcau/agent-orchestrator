"""Filesystem skills — read, write, search files."""

from __future__ import annotations

import glob as glob_module
from pathlib import Path

from ..core.skill import Skill, SkillResult


def _confine(cwd: Path | None, raw: str) -> Path:
    """Resolve a user-supplied path under cwd. Absolute paths that escape cwd
    are remapped under it (treat cwd as a chroot-like root). This prevents
    agents from writing outside the session working directory."""
    if cwd is None:
        return Path(raw)
    p = Path(raw)
    if p.is_absolute():
        cwd_r = cwd.resolve()
        try:
            p.resolve().relative_to(cwd_r)
            return p
        except ValueError:
            return cwd / p.relative_to(p.anchor)
    return cwd / p


class FileReadSkill(Skill):
    def __init__(self, working_directory: str | Path | None = None):
        self._cwd = Path(working_directory) if working_directory else None

    @property
    def name(self) -> str:
        return "file_read"

    @property
    def description(self) -> str:
        return "Read the contents of a file"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file"},
            },
            "required": ["file_path"],
        }

    async def execute(self, params: dict) -> SkillResult:
        path = _confine(self._cwd, params["file_path"])
        if not path.exists():
            return SkillResult(success=False, output=None, error=f"File not found: {path}")
        if not path.is_file():
            return SkillResult(success=False, output=None, error=f"Not a file: {path}")
        content = path.read_text(encoding="utf-8", errors="replace")
        return SkillResult(success=True, output=content)


class FileWriteSkill(Skill):
    def __init__(self, working_directory: str | Path | None = None):
        self._cwd = Path(working_directory) if working_directory else None

    @property
    def name(self) -> str:
        return "file_write"

    @property
    def description(self) -> str:
        return "Write content to a file (creates parent directories)"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to write"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["file_path", "content"],
        }

    async def execute(self, params: dict) -> SkillResult:
        path = _confine(self._cwd, params["file_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(params["content"], encoding="utf-8")
        return SkillResult(success=True, output=f"Written {len(params['content'])} chars to {path}")


class GlobSkill(Skill):
    def __init__(self, working_directory: str | Path | None = None):
        self._cwd = Path(working_directory) if working_directory else None

    @property
    def name(self) -> str:
        return "glob_search"

    @property
    def description(self) -> str:
        return "Search for files matching a glob pattern"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern (e.g. '**/*.py')"},
                "directory": {"type": "string", "description": "Base directory", "default": "."},
            },
            "required": ["pattern"],
        }

    async def execute(self, params: dict) -> SkillResult:
        base = _confine(self._cwd, params.get("directory", "."))
        matches = sorted(glob_module.glob(params["pattern"], root_dir=str(base), recursive=True))
        return SkillResult(
            success=True, output="\n".join(matches) if matches else "No matches found"
        )
