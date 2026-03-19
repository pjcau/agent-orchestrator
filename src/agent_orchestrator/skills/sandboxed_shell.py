"""Sandboxed shell execution skill.

Wraps shell command execution inside a Sandbox (Docker or local) for
secure, isolated code execution by agents. Drop-in replacement for
ShellExecSkill when isolation is required.
"""

from __future__ import annotations

from ..core.sandbox import Sandbox, SandboxError
from ..core.skill import Skill, SkillResult


class SandboxedShellSkill(Skill):
    """Execute shell commands inside a sandbox.

    Unlike ShellExecSkill which runs commands directly on the host,
    this skill runs them inside a Docker container (or local subprocess
    in test mode) with resource limits and network isolation.

    The sandbox lifecycle is managed externally — pass a started Sandbox
    instance. This allows sharing a single container across multiple
    skill invocations within one agent session.

    Usage::

        sandbox = Sandbox(SandboxConfig(type=SandboxType.LOCAL))
        await sandbox.start()
        skill = SandboxedShellSkill(sandbox=sandbox)
        result = await skill.execute({"command": "echo hello"})
        await sandbox.stop()
    """

    def __init__(
        self,
        sandbox: Sandbox,
        allowed_commands: list[str] | None = None,
        timeout: float | None = None,
    ) -> None:
        self._sandbox = sandbox
        self._allowed_commands = allowed_commands
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "sandboxed_shell"

    @property
    def description(self) -> str:
        return "Execute a shell command inside an isolated sandbox"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute inside the sandbox",
                },
            },
            "required": ["command"],
        }

    async def execute(self, params: dict) -> SkillResult:
        command = params.get("command", "")
        if not command:
            return SkillResult(success=False, output=None, error="No command provided")

        # Check allowed commands
        if self._allowed_commands:
            cmd_name = command.split()[0] if command.split() else ""
            if cmd_name not in self._allowed_commands:
                return SkillResult(
                    success=False,
                    output=None,
                    error=f"Command not allowed: {cmd_name}",
                )

        if not self._sandbox.is_running:
            return SkillResult(
                success=False,
                output=None,
                error="Sandbox is not running",
            )

        try:
            timeout_int = int(self._timeout) if self._timeout else None
            result = await self._sandbox.execute(command, timeout=timeout_int)

            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR:\n{result.stderr}"

            if result.timed_out:
                return SkillResult(
                    success=False,
                    output=output,
                    error=f"Command timed out after {result.duration_seconds:.1f}s",
                )

            return SkillResult(
                success=result.exit_code == 0,
                output=output,
                error=(f"Exit code: {result.exit_code}" if result.exit_code != 0 else None),
            )
        except SandboxError as e:
            return SkillResult(success=False, output=None, error=str(e))
