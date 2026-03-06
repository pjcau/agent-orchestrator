"""GitHub integration skill — create PRs, review code, manage issues via the gh CLI."""

from __future__ import annotations

import asyncio
import json

from ..core.skill import Skill, SkillResult


class GitHubSkill(Skill):
    """GitHub integration using the ``gh`` CLI. No extra Python dependencies."""

    @property
    def name(self) -> str:
        return "github"

    @property
    def description(self) -> str:
        return "GitHub integration: create PRs, review code, manage issues"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "create_pr",
                        "list_issues",
                        "get_issue",
                        "add_comment",
                        "list_prs",
                        "get_pr",
                    ],
                },
                "title": {"type": "string"},
                "body": {"type": "string"},
                "branch": {"type": "string"},
                "base": {"type": "string", "default": "main"},
                "number": {"type": "integer"},
                "repo": {"type": "string"},
            },
            "required": ["action"],
        }

    async def execute(self, params: dict) -> SkillResult:
        action = params.get("action")
        if action == "create_pr":
            return await self._create_pr(params)
        if action == "list_issues":
            return await self._list_issues(params)
        if action == "get_issue":
            return await self._get_issue(params)
        if action == "add_comment":
            return await self._add_comment(params)
        if action == "list_prs":
            return await self._list_prs(params)
        if action == "get_pr":
            return await self._get_pr(params)
        return SkillResult(success=False, output=None, error=f"Unknown action: {action}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_gh(self, *args: str) -> tuple[str, str, int]:
        """Run a ``gh`` command, return (stdout, stderr, returncode)."""
        proc = await asyncio.create_subprocess_exec(
            "gh",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        return (
            stdout_bytes.decode(errors="replace").strip(),
            stderr_bytes.decode(errors="replace").strip(),
            proc.returncode or 0,
        )

    def _repo_args(self, params: dict) -> list[str]:
        """Return ``["-R", "<repo>"]`` when params contains a ``repo`` key."""
        repo = params.get("repo")
        return ["-R", repo] if repo else []

    # ------------------------------------------------------------------
    # Action implementations
    # ------------------------------------------------------------------

    async def _create_pr(self, params: dict) -> SkillResult:
        args = ["pr", "create", "--json", "number,url,title,state"]
        title = params.get("title", "")
        if title:
            args += ["--title", title]
        body = params.get("body", "")
        if body:
            args += ["--body", body]
        branch = params.get("branch", "")
        if branch:
            args += ["--head", branch]
        base = params.get("base", "main")
        args += ["--base", base]
        args += self._repo_args(params)

        stdout, stderr, rc = await self._run_gh(*args)
        if rc != 0:
            return SkillResult(success=False, output=None, error=stderr)
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            data = stdout
        return SkillResult(success=True, output=data)

    async def _list_issues(self, params: dict) -> SkillResult:
        args = ["issue", "list", "--json", "number,title,state,url"] + self._repo_args(params)
        stdout, stderr, rc = await self._run_gh(*args)
        if rc != 0:
            return SkillResult(success=False, output=None, error=stderr)
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            data = stdout
        return SkillResult(success=True, output=data)

    async def _get_issue(self, params: dict) -> SkillResult:
        number = params.get("number")
        if number is None:
            return SkillResult(success=False, output=None, error="'number' is required for get_issue")
        args = (
            ["issue", "view", str(number), "--json", "number,title,body,state,url"]
            + self._repo_args(params)
        )
        stdout, stderr, rc = await self._run_gh(*args)
        if rc != 0:
            return SkillResult(success=False, output=None, error=stderr)
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            data = stdout
        return SkillResult(success=True, output=data)

    async def _add_comment(self, params: dict) -> SkillResult:
        number = params.get("number")
        body = params.get("body", "")
        if number is None:
            return SkillResult(success=False, output=None, error="'number' is required for add_comment")
        if not body:
            return SkillResult(success=False, output=None, error="'body' is required for add_comment")
        args = (
            ["issue", "comment", str(number), "--body", body]
            + self._repo_args(params)
        )
        stdout, stderr, rc = await self._run_gh(*args)
        if rc != 0:
            return SkillResult(success=False, output=None, error=stderr)
        return SkillResult(success=True, output=stdout or "Comment added")

    async def _list_prs(self, params: dict) -> SkillResult:
        args = ["pr", "list", "--json", "number,title,state,url"] + self._repo_args(params)
        stdout, stderr, rc = await self._run_gh(*args)
        if rc != 0:
            return SkillResult(success=False, output=None, error=stderr)
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            data = stdout
        return SkillResult(success=True, output=data)

    async def _get_pr(self, params: dict) -> SkillResult:
        number = params.get("number")
        if number is None:
            return SkillResult(success=False, output=None, error="'number' is required for get_pr")
        args = (
            ["pr", "view", str(number), "--json", "number,title,body,state,url"]
            + self._repo_args(params)
        )
        stdout, stderr, rc = await self._run_gh(*args)
        if rc != 0:
            return SkillResult(success=False, output=None, error=stderr)
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            data = stdout
        return SkillResult(success=True, output=data)
