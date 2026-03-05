"""Shell execution skill."""

from __future__ import annotations

import asyncio
from ..core.skill import Skill, SkillResult


class ShellExecSkill(Skill):
    def __init__(self, timeout: float = 120.0, allowed_commands: list[str] | None = None):
        self._timeout = timeout
        self._allowed_commands = allowed_commands

    @property
    def name(self) -> str:
        return "shell_exec"

    @property
    def description(self) -> str:
        return "Execute a shell command and return stdout/stderr"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
            },
            "required": ["command"],
        }

    async def execute(self, params: dict) -> SkillResult:
        command = params["command"]

        if self._allowed_commands:
            cmd_name = command.split()[0] if command.split() else ""
            if cmd_name not in self._allowed_commands:
                return SkillResult(
                    success=False, output=None, error=f"Command not allowed: {cmd_name}"
                )

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
            output = stdout.decode(errors="replace")
            if stderr:
                output += f"\nSTDERR:\n{stderr.decode(errors='replace')}"

            return SkillResult(
                success=proc.returncode == 0,
                output=output,
                error=f"Exit code: {proc.returncode}" if proc.returncode != 0 else None,
            )
        except asyncio.TimeoutError:
            return SkillResult(
                success=False, output=None, error=f"Command timed out after {self._timeout}s"
            )
