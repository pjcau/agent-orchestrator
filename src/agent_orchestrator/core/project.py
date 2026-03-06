"""Multi-project support — manage multiple codebases from one orchestrator."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProjectConfig:
    """Configuration for a single project."""
    project_id: str
    name: str
    root_path: str
    description: str = ""
    active: bool = True
    created_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class ProjectManager:
    """Manage multiple projects within a single orchestrator instance.

    Each project has its own root path and configuration.
    Only one project can be the "current" project at a time.
    """

    def __init__(self) -> None:
        self._projects: dict[str, ProjectConfig] = {}
        self._current_id: str | None = None

    def create(self, project: ProjectConfig) -> ProjectConfig:
        """Register a new project."""
        if project.project_id in self._projects:
            raise ValueError(f"Project '{project.project_id}' already exists")
        if not project.created_at:
            project.created_at = time.time()
        self._projects[project.project_id] = project
        if self._current_id is None:
            self._current_id = project.project_id
        return project

    def get(self, project_id: str) -> ProjectConfig | None:
        """Get a project by ID."""
        return self._projects.get(project_id)

    def list_projects(self, active_only: bool = False) -> list[ProjectConfig]:
        """List all projects, optionally filtering to active ones."""
        projects = list(self._projects.values())
        if active_only:
            projects = [p for p in projects if p.active]
        return projects

    def update(self, project: ProjectConfig) -> ProjectConfig:
        """Update an existing project."""
        if project.project_id not in self._projects:
            raise KeyError(f"Project '{project.project_id}' not found")
        self._projects[project.project_id] = project
        return project

    def delete(self, project_id: str) -> bool:
        """Delete a project. Returns True if it existed."""
        if project_id not in self._projects:
            return False
        del self._projects[project_id]
        if self._current_id == project_id:
            self._current_id = next(iter(self._projects), None)
        return True

    def set_current(self, project_id: str) -> None:
        """Set the current active project."""
        if project_id not in self._projects:
            raise KeyError(f"Project '{project_id}' not found")
        self._current_id = project_id

    @property
    def current(self) -> ProjectConfig | None:
        """The currently active project."""
        if self._current_id is None:
            return None
        return self._projects.get(self._current_id)

    @property
    def current_id(self) -> str | None:
        """ID of the currently active project."""
        return self._current_id

    def archive(self, project_id: str) -> bool:
        """Mark a project as inactive (archived). Returns True if found."""
        project = self._projects.get(project_id)
        if project is None:
            return False
        project.active = False
        return True

    def unarchive(self, project_id: str) -> bool:
        """Mark an archived project as active again. Returns True if found."""
        project = self._projects.get(project_id)
        if project is None:
            return False
        project.active = True
        return True

    def get_status(self) -> dict[str, Any]:
        """Return a status summary."""
        return {
            "total_projects": len(self._projects),
            "active_projects": sum(1 for p in self._projects.values() if p.active),
            "current_project": self._current_id,
        }
