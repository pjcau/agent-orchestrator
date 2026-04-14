"""Tests for FileWriteSkill/FileReadSkill/GlobSkill path confinement.

Regression: absolute paths used to bypass `working_directory`, letting
agents write files outside the session directory (polluting the host project).
Paths are now confined under the working directory chroot-style.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agent_orchestrator.skills.filesystem import FileReadSkill, FileWriteSkill, GlobSkill


def test_file_write_confines_absolute_path_outside_cwd(tmp_path: Path) -> None:
    cwd = tmp_path / "session"
    cwd.mkdir()
    outside = tmp_path / "host_project" / "backend"

    skill = FileWriteSkill(working_directory=cwd)
    res = asyncio.run(
        skill.execute({"file_path": str(outside / "main.py"), "content": "print('x')"})
    )

    assert res.success
    assert not outside.exists(), "absolute path escaping cwd must not be honoured"
    # File must live under cwd (exact path is cwd + absolute-path-with-anchor-stripped)
    matches = list(cwd.rglob("main.py"))
    assert matches, "confined file must exist somewhere under cwd"
    assert matches[0].read_text() == "print('x')"
    assert cwd in matches[0].parents


def test_file_write_accepts_absolute_path_inside_cwd(tmp_path: Path) -> None:
    cwd = tmp_path / "session"
    cwd.mkdir()
    inside = cwd / "backend" / "main.py"

    skill = FileWriteSkill(working_directory=cwd)
    res = asyncio.run(skill.execute({"file_path": str(inside), "content": "ok"}))

    assert res.success
    assert inside.exists()


def test_file_write_relative_path_under_cwd(tmp_path: Path) -> None:
    cwd = tmp_path / "session"
    cwd.mkdir()

    skill = FileWriteSkill(working_directory=cwd)
    res = asyncio.run(skill.execute({"file_path": "backend/main.py", "content": "ok"}))

    assert res.success
    assert (cwd / "backend" / "main.py").exists()


def test_file_read_confines_absolute_path_outside_cwd(tmp_path: Path) -> None:
    cwd = tmp_path / "session"
    cwd.mkdir()
    target = cwd / "workspace_probe" / "secret.txt"
    target.parent.mkdir(parents=True)
    target.write_text("confined-content")

    skill = FileReadSkill(working_directory=cwd)
    # Agent tries to read /workspace_probe/secret.txt (absolute, escapes cwd)
    res = asyncio.run(skill.execute({"file_path": "/workspace_probe/secret.txt"}))

    assert res.success
    assert res.output == "confined-content"


def test_glob_confines_absolute_directory(tmp_path: Path) -> None:
    cwd = tmp_path / "session"
    cwd.mkdir()
    (cwd / "etc").mkdir()
    (cwd / "etc" / "a.conf").write_text("")

    skill = GlobSkill(working_directory=cwd)
    # agent tries to scan /etc — remaps to cwd / etc
    res = asyncio.run(skill.execute({"pattern": "*.conf", "directory": "/etc"}))

    assert res.success
    assert "a.conf" in res.output
