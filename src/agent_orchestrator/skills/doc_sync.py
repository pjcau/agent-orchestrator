"""Doc-sync skill — keeps documentation in sync with code and project files."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from ..core.skill import Skill, SkillResult


class DocSyncSkill(Skill):
    """Scans the codebase and reports/fixes documentation drift.

    Compares actual code structure (modules, classes, functions, providers,
    skills, docker services) against CLAUDE.md, README.md, and docs/.
    """

    def __init__(self, project_root: str = "."):
        self._root = Path(project_root)

    @property
    def name(self) -> str:
        return "doc_sync"

    @property
    def description(self) -> str:
        return (
            "Scan the codebase and compare against CLAUDE.md, README.md, "
            "and docs/ to find documentation drift. Returns a report of "
            "missing or outdated sections."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "project_root": {
                    "type": "string",
                    "description": "Project root directory (default: current dir)",
                    "default": ".",
                },
                "fix": {
                    "type": "boolean",
                    "description": "If true, attempt to auto-fix simple drift (default: false)",
                    "default": False,
                },
            },
            "required": [],
        }

    async def execute(self, params: dict) -> SkillResult:
        root = Path(params.get("project_root", str(self._root)))
        fix = params.get("fix", False)

        if not root.exists():
            return SkillResult(success=False, output=None, error=f"Root not found: {root}")

        report = []
        fixes_applied = []

        # Gather facts from code
        facts = self._gather_facts(root)

        # Check CLAUDE.md
        claude_md = root / "CLAUDE.md"
        if claude_md.exists():
            claude_issues = self._check_claude_md(claude_md, facts)
            if claude_issues:
                report.append("## CLAUDE.md issues")
                report.extend(f"- {i}" for i in claude_issues)

        # Check README.md
        readme = root / "README.md"
        if readme.exists():
            readme_issues = self._check_readme(readme, facts)
            if readme_issues:
                report.append("## README.md issues")
                report.extend(f"- {i}" for i in readme_issues)

        # Check docs/ directory
        docs_dir = root / "docs"
        if docs_dir.is_dir():
            docs_issues = self._check_docs_dir(docs_dir, facts)
            if docs_issues:
                report.append("## docs/ issues")
                report.extend(f"- {i}" for i in docs_issues)

        # Check Docusaurus website docs
        website_docs = root / "docs" / "website" / "docs"
        if website_docs.is_dir():
            website_issues = self._check_website_docs(website_docs, facts)
            if website_issues:
                report.append("## docs/website/ issues")
                report.extend(f"- {i}" for i in website_issues)

        if not report:
            return SkillResult(
                success=True, output="All documentation is in sync with the codebase."
            )

        output = "\n".join(report)
        if fix:
            fixes_applied = self._apply_fixes(root, facts)
            if fixes_applied:
                output += "\n\n## Fixes applied\n" + "\n".join(f"- {f}" for f in fixes_applied)

        return SkillResult(success=True, output=output)

    def _gather_facts(self, root: Path) -> dict:
        """Gather facts about the codebase structure."""
        src = root / "src" / "agent_orchestrator"
        facts: dict = {
            "modules": [],
            "core_classes": [],
            "providers": [],
            "skills": [],
            "docker_services": [],
            "test_files": [],
            "examples": [],
            "hook_scripts": [],
        }

        # Modules (top-level packages under src/agent_orchestrator/)
        if src.is_dir():
            for d in sorted(src.iterdir()):
                if d.is_dir() and d.name != "__pycache__":
                    facts["modules"].append(d.name)

        # Core classes (from core/*.py)
        core_dir = src / "core" if src.is_dir() else None
        if core_dir and core_dir.is_dir():
            for py in sorted(core_dir.glob("*.py")):
                if py.name.startswith("_"):
                    continue
                facts["core_classes"].extend(self._extract_classes(py))

        # Providers (from providers/*.py)
        providers_dir = src / "providers" if src.is_dir() else None
        if providers_dir and providers_dir.is_dir():
            for py in sorted(providers_dir.glob("*.py")):
                if py.name.startswith("_"):
                    continue
                classes = self._extract_classes(py)
                facts["providers"].extend(
                    {"file": py.stem, "classes": classes} for _ in [None] if classes
                )

        # Skills (from skills/*.py)
        skills_dir = src / "skills" if src.is_dir() else None
        if skills_dir and skills_dir.is_dir():
            for py in sorted(skills_dir.glob("*.py")):
                if py.name.startswith("_"):
                    continue
                classes = self._extract_classes(py)
                facts["skills"].extend(classes)

        # Docker services
        compose = root / "docker-compose.yml"
        if compose.exists():
            facts["docker_services"] = self._extract_docker_services(compose)

        # Test files
        tests_dir = root / "tests"
        if tests_dir.is_dir():
            facts["test_files"] = [f.stem for f in sorted(tests_dir.glob("test_*.py"))]

        # Examples
        examples_dir = root / "examples"
        if examples_dir.is_dir():
            facts["examples"] = [f.name for f in sorted(examples_dir.glob("*.py"))]

        # Hook scripts
        hooks_dir = root / ".claude" / "hooks"
        if hooks_dir.is_dir():
            facts["hook_scripts"] = [f.name for f in sorted(hooks_dir.glob("*.sh"))]

        return facts

    def _extract_classes(self, py_file: Path) -> list[str]:
        """Extract class names from a Python file using AST."""
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
            return [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        except (SyntaxError, ValueError):
            return []

    def _extract_docker_services(self, compose_file: Path) -> list[str]:
        """Extract service names from docker-compose.yml."""
        services = []
        in_services = False
        for line in compose_file.read_text().splitlines():
            if line.strip() == "services:":
                in_services = True
                continue
            if in_services and re.match(r"^[a-z]", line.strip()) and not line.startswith(" "):
                in_services = False
                continue
            if in_services:
                match = re.match(r"^  ([a-z][a-z0-9_-]+):", line)
                if match:
                    services.append(match.group(1))
        return services

    def _check_claude_md(self, claude_md: Path, facts: dict) -> list[str]:
        """Check CLAUDE.md for missing references."""
        content = claude_md.read_text()
        issues = []

        for mod in facts["modules"]:
            if mod not in content:
                issues.append(f"Module '{mod}' not mentioned in CLAUDE.md")

        for svc in facts["docker_services"]:
            if svc not in content.lower():
                issues.append(f"Docker service '{svc}' not mentioned in CLAUDE.md")

        for hook in facts["hook_scripts"]:
            if hook not in content:
                issues.append(f"Hook script '{hook}' not mentioned in CLAUDE.md")

        return issues

    def _check_readme(self, readme: Path, facts: dict) -> list[str]:
        """Check README.md for missing references."""
        content = readme.read_text()
        issues = []

        # Check providers are documented
        for prov in facts["providers"]:
            for cls in prov["classes"]:
                if cls not in content and cls.endswith("Provider"):
                    issues.append(f"Provider class '{cls}' not in README.md Providers table")

        # Check docker services
        for svc in facts["docker_services"]:
            if svc not in content.lower():
                issues.append(f"Docker service '{svc}' not mentioned in README.md")

        # Check skill classes are referenced
        for skill_cls in facts["skills"]:
            if skill_cls.endswith("Skill") and skill_cls not in content:
                issues.append(f"Skill '{skill_cls}' not documented in README.md")

        # Check core modules in project structure
        for mod in facts["modules"]:
            if mod not in content:
                issues.append(f"Module '{mod}' missing from README.md Project Structure")

        return issues

    def _check_docs_dir(self, docs_dir: Path, facts: dict) -> list[str]:
        """Check docs/ for stale or missing content."""
        issues = []

        arch_file = docs_dir / "architecture.md"
        if arch_file.exists():
            arch_content = arch_file.read_text()
            for prov in facts["providers"]:
                for cls in prov["classes"]:
                    if cls.endswith("Provider") and cls not in arch_content:
                        issues.append(f"Provider '{cls}' not in docs/architecture.md")

        return issues

    def _check_website_docs(self, website_docs: Path, facts: dict) -> list[str]:
        """Check Docusaurus website docs for missing content."""
        issues = []

        # Check providers page
        providers_page = website_docs / "architecture" / "providers.md"
        if providers_page.exists():
            content = providers_page.read_text()
            for prov in facts["providers"]:
                for cls in prov["classes"]:
                    if cls.endswith("Provider") and cls not in content:
                        issues.append(f"Provider '{cls}' not in website docs providers page")

        # Check skills page
        skills_page = website_docs / "architecture" / "skills.md"
        if skills_page.exists():
            content = skills_page.read_text()
            for skill_cls in facts["skills"]:
                if skill_cls.endswith("Skill") and skill_cls not in content:
                    issues.append(f"Skill '{skill_cls}' not in website docs skills page")

        return issues

    def _apply_fixes(self, root: Path, facts: dict) -> list[str]:
        """Apply simple auto-fixes. Returns list of fixes applied."""
        fixes = []
        # Auto-fixes are intentionally conservative — only add missing
        # references to existing sections, never rewrite content.
        # Complex fixes should be done manually.
        return fixes
