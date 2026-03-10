## Research Scout: improvements from [IgorWarzocha/opencode-planning-toolkit](https://github.com/IgorWarzocha/opencode-planning-toolkit)

Analyzed [IgorWarzocha/opencode-planning-toolkit](https://github.com/IgorWarzocha/opencode-planning-toolkit) and found **3** actionable improvement(s) for the orchestrator.

### 1. Persistent cross-session plan tracking with status lifecycle

**Component:** `orchestrator`
**File:** `src/agent_orchestrator/core/orchestrator.py`

The OpenCode Planning Toolkit stores plans as persistent documents with a clear lifecycle (active → done) and metadata frontmatter. Our Orchestrator decomposes tasks into TaskAssignments but all state is lost when the process ends. Adding a PlanManager that persists plans to BaseStore with status tracking enables resuming interrupted workflows across sessions.

```python
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .store import BaseStore


class PlanStatus(str, Enum):
    ACTIVE = "active"
    DONE = "done"
    STALLED = "stalled"


@dataclass
class Plan:
    name: str
    description: str
    status: PlanStatus = PlanStatus.ACTIVE
    steps: list[dict[str, Any]] = field(default_factory=list)
    linked_specs: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PlanManager:
    """Persistent plan tracking across sessions, inspired by opencode-planning-toolkit."""

    NAMESPACE = ("plans",)

    def __init__(self, store: BaseStore) -> None:
        self._store = store

    async def create_plan(
        self, name: str, description: str, steps: list[dict[str, Any]]
    ) -> Plan:
        plan = Plan(name=name, description=description, steps=steps)
        await self._store.aput(self.NAMESPACE, name, {
            "name": plan.name,
            "description": plan.description,
            "status": plan.status.value,
            "steps": plan.steps,
            "linked_specs": plan.linked_specs,
            "created_at": plan.created_at,
            "updated_at": plan.updated_at,
        })
        return plan

    async def get_plan(self, name: str) -> Plan | None:
        item = await self._store.aget(self.NAMESPACE, name)
        if item is None:
            return None
        v = item.value
        plan = Plan(
            name=v["name"], description=v["description"],
            status=PlanStatus(v["status"]), steps=v["steps"],
            linked_specs=v.get("linked_specs", []),
            created_at=v["created_at"], updated_at=v["updated_at"],
        )
        return plan

    async def mark_done(self, name: str) -> Plan | None:
        plan = await self.get_plan(name)
        if plan is None:
            return None
        plan.status = PlanStatus.DONE
        plan.updated_at = datetime.now(timezone.utc).isoformat()
        await self._store.aput(self.NAMESPACE, name, {
            "name": plan.name, "description": plan.description,
            "status": plan.status.value, "steps": plan.steps,
            "linked_specs": plan.linked_specs,
            "created_at": plan.created_at, "updated_at": plan.updated_at,
        })
        return plan

    async def list_active(self) -> list[Plan]:
        items = await self._store.asearch(self.NAMESPACE, filter={"status": {"$eq": "active"}})
        return [
            Plan(
                name=i.value["name"], description=i.value["description"],
                status=PlanStatus(i.value["status"]), steps=i.value["steps"],
                linked_specs=i.value.get("linked_specs", []),
                created_at=i.value["created_at"], updated_at=i.value["updated_at"],
            )
            for i in items
        ]

    async def link_spec(self, plan_name: str, spec_name: str) -> Plan | None:
        plan = await self.get_plan(plan_name)
        if plan is None:
            return None
        if spec_name not in plan.linked_specs:
            plan.linked_specs.append(spec_name)
            plan.updated_at = datetime.now(timezone.utc).isoformat()
            await self._store.aput(self.NAMESPACE, plan_name, {
                "name": plan.name, "description": plan.description,
                "status": plan.status.value, "steps": plan.steps,
                "linked_specs": plan.linked_specs,
                "created_at": plan.created_at, "updated_at": plan.updated_at,
            })
        return plan
```

**Benefit:** Enables resuming interrupted multi-step workflows across sessions instead of losing all orchestration state when the process ends.

### 2. Skill bundling with metadata and auto-discovery

**Component:** `skill`
**File:** `src/agent_orchestrator/core/skill.py`

The OpenCode Planning Toolkit bundles a skill that auto-loads with the plugin and includes workflow instructions. Our SkillRegistry requires manual registration with no metadata (author, version, tags, category). Adding a SkillBundle that groups related skills with auto-registration and descriptive metadata enables plugin-style skill distribution.

```python
@dataclass
class SkillMetadata:
    """Rich metadata for skill discovery, inspired by opencode-planning-toolkit bundled skills."""
    author: str = ""
    version: str = "1.0.0"
    tags: list[str] = field(default_factory=list)
    category: str = "general"
    workflow_hint: str = ""  # Guides agents on when/how to use this skill


@dataclass
class SkillBundle:
    """Group of related skills that auto-register together."""
    name: str
    description: str
    skills: list[Skill] = field(default_factory=list)
    metadata: SkillMetadata = field(default_factory=SkillMetadata)

    def register_all(self, registry: "SkillRegistry") -> None:
        for skill in self.skills:
            registry.register(skill)


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._bundles: dict[str, SkillBundle] = {}

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def register_bundle(self, bundle: SkillBundle) -> None:
        """Register a bundle — all its skills are auto-registered."""
        self._bundles[bundle.name] = bundle
        bundle.register_all(self)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_skills(self) -> list[str]:
        return list(self._skills.keys())

    def list_bundles(self) -> list[str]:
        return list(self._bundles.keys())

    def get_by_tag(self, tag: str) -> list[Skill]:
        """Find skills by tag for agent-driven discovery."""
        results = []
        for bundle in self._bundles.values():
            if tag in bundle.metadata.tags:
                results.extend(bundle.skills)
        return results

    def get_workflow_hints(self) -> dict[str, str]:
        """Return workflow hints for all bundles (injected into agent system prompts)."""
        return {
            b.name: b.metadata.workflow_hint
            for b in self._bundles.values()
            if b.metadata.workflow_hint
        }
```

**Benefit:** Enables plugin-style skill distribution where related skills auto-register together and agents receive workflow hints about when to use them.

### 3. Linkable reusable specs for graph templates

**Component:** `graph`
**File:** `src/agent_orchestrator/core/graph_templates.py`

The OpenCode Planning Toolkit lets plans link to reusable specs (repo-level standards, feature requirements) that get expanded inline when reading a plan. Our GraphTemplateStore has versioned templates but no way to attach reusable constraint documents. Adding a SpecStore that templates can reference ensures consistent standards across graph executions.

```python
@dataclass
class Spec:
    """Reusable specification document, inspired by opencode-planning-toolkit specs."""
    name: str
    content: str
    scope: str = "repo"  # 'repo' (global) or 'feature' (plan-specific)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SpecStore:
    """Registry of reusable specs that can be linked to graph templates."""

    def __init__(self) -> None:
        self._specs: dict[str, Spec] = {}

    def save(self, spec: Spec) -> None:
        self._specs[spec.name] = spec

    def get(self, name: str) -> Spec | None:
        return self._specs.get(name)

    def list_specs(self, scope: str | None = None) -> list[Spec]:
        specs = list(self._specs.values())
        if scope:
            specs = [s for s in specs if s.scope == scope]
        return specs

    def delete(self, name: str) -> bool:
        return self._specs.pop(name, None) is not None


@dataclass
class GraphTemplate:
    name: str
    description: str
    version: int
    nodes: list[NodeTemplate]
    edges: list[EdgeTemplate]
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)
    linked_specs: list[str] = field(default_factory=list)  # NEW: spec names


class GraphTemplateStore:
    def __init__(self, spec_store: SpecStore | None = None) -> None:
        self._templates: dict[str, list[GraphTemplate]] = {}
        self._spec_store = spec_store or SpecStore()

    def resolve_specs(self, template_name: str, version: int | None = None) -> list[Spec]:
        """Expand all linked spec names into full Spec objects (inline expansion)."""
        tmpl = self.get(template_name, version)
        if tmpl is None:
            return []
        resolved = []
        for spec_name in tmpl.linked_specs:
            spec = self._spec_store.get(spec_name)
            if spec:
                resolved.append(spec)
        return resolved

    def link_spec(self, template_name: str, spec_name: str) -> bool:
        """Link a spec to the latest version of a template."""
        tmpl = self.get(template_name)
        if tmpl is None or self._spec_store.get(spec_name) is None:
            return False
        if spec_name not in tmpl.linked_specs:
            tmpl.linked_specs.append(spec_name)
        return True
```

**Benefit:** Ensures graph templates can reference shared standards and requirements that get resolved at build time, promoting consistency across workflows.
