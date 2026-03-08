"""Tests for v1.0.0 — General Availability."""

import json

import pytest
from agent_orchestrator.core.config_manager import (
    AgentConfigEntry,
    ConfigManager,
    OrchestratorConfiguration,
    ProviderConfigEntry,
)
from agent_orchestrator.core.project import ProjectConfig, ProjectManager
from agent_orchestrator.core.users import UserManager, UserRole, ROLE_PERMISSIONS
from agent_orchestrator.core.provider_presets import (
    ProviderPreset,
    ProviderPresetEntry,
    ProviderPresetManager,
)
from agent_orchestrator.core.migration import MigrationManager
from agent_orchestrator.core.api import (
    API_PREFIX,
    APIEndpoint,
    APIRegistry,
    APIResponse,
    HTTPMethod,
)


# --- ConfigManager ---


class TestConfigManager:
    def test_default_config(self):
        mgr = ConfigManager()
        assert mgr.config.version == "1.0.0"
        assert mgr.config.agents == []
        assert mgr.config.providers == []

    def test_update_saves_history(self):
        mgr = ConfigManager()
        new_cfg = OrchestratorConfiguration(version="1.0.0", routing_strategy="fixed")
        mgr.update(new_cfg)
        assert mgr.config.routing_strategy == "fixed"
        assert len(mgr.get_history()) == 1
        assert mgr.config.updated_at > 0

    def test_rollback(self):
        mgr = ConfigManager()
        original = mgr.config
        mgr.update(OrchestratorConfiguration(routing_strategy="fixed"))
        rolled = mgr.rollback()
        assert rolled is not None
        assert mgr.config.routing_strategy == original.routing_strategy

    def test_rollback_empty_history(self):
        mgr = ConfigManager()
        assert mgr.rollback() is None

    def test_add_and_get_agent(self):
        mgr = ConfigManager()
        mgr.add_agent(AgentConfigEntry("backend", "Backend dev", "ollama"))
        assert mgr.get_agent("backend") is not None
        assert mgr.get_agent("backend").role == "Backend dev"
        assert mgr.get_agent("nonexistent") is None

    def test_remove_agent(self):
        mgr = ConfigManager()
        mgr.add_agent(AgentConfigEntry("backend", "Backend dev", "ollama"))
        assert mgr.remove_agent("backend") is True
        assert mgr.remove_agent("backend") is False
        assert mgr.get_agent("backend") is None

    def test_add_and_get_provider(self):
        mgr = ConfigManager()
        mgr.add_provider(ProviderConfigEntry("ollama", "ollama", "qwen2.5-coder:7b"))
        assert mgr.get_provider("ollama") is not None
        assert mgr.get_provider("ollama").model == "qwen2.5-coder:7b"

    def test_remove_provider(self):
        mgr = ConfigManager()
        mgr.add_provider(ProviderConfigEntry("ollama", "ollama", "qwen2.5-coder:7b"))
        assert mgr.remove_provider("ollama") is True
        assert mgr.remove_provider("ollama") is False

    def test_validate_valid_config(self):
        mgr = ConfigManager()
        mgr.add_provider(ProviderConfigEntry("ollama", "ollama", "qwen"))
        mgr.add_agent(AgentConfigEntry("dev", "Developer", "ollama"))
        errors = mgr.validate()
        assert errors == []

    def test_validate_duplicate_agents(self):
        cfg = OrchestratorConfiguration(
            agents=[
                AgentConfigEntry("dev", "a", "ollama"),
                AgentConfigEntry("dev", "b", "ollama"),
            ]
        )
        mgr = ConfigManager()
        errors = mgr.validate(cfg)
        assert any("Duplicate agent" in e for e in errors)

    def test_validate_unknown_provider_ref(self):
        cfg = OrchestratorConfiguration(
            agents=[AgentConfigEntry("dev", "a", "nonexistent")],
            providers=[],
        )
        mgr = ConfigManager()
        errors = mgr.validate(cfg)
        assert any("unknown provider" in e for e in errors)

    def test_validate_unknown_routing_strategy(self):
        cfg = OrchestratorConfiguration(routing_strategy="magic")
        mgr = ConfigManager()
        errors = mgr.validate(cfg)
        assert any("routing strategy" in e.lower() for e in errors)

    def test_validate_unknown_provider_type(self):
        cfg = OrchestratorConfiguration(
            providers=[ProviderConfigEntry("x", "unknown_type", "model")]
        )
        mgr = ConfigManager()
        errors = mgr.validate(cfg)
        assert any("unknown type" in e for e in errors)

    def test_export_import_json(self):
        mgr = ConfigManager()
        mgr.add_provider(ProviderConfigEntry("ollama", "ollama", "qwen"))
        mgr.add_agent(AgentConfigEntry("dev", "Developer", "ollama"))
        json_str = mgr.export_json()
        parsed = json.loads(json_str)
        assert parsed["version"] == "1.0.0"
        assert len(parsed["agents"]) == 1
        assert len(parsed["providers"]) == 1

        imported = mgr.import_json(json_str)
        assert imported.agents[0].name == "dev"
        assert imported.providers[0].key == "ollama"


# --- ProjectManager ---


class TestProjectManager:
    def test_create_and_get(self):
        pm = ProjectManager()
        p = pm.create(ProjectConfig("p1", "My Project", "/home/user/project"))
        assert p.project_id == "p1"
        assert pm.get("p1") is not None
        assert pm.get("p1").name == "My Project"

    def test_create_duplicate_raises(self):
        pm = ProjectManager()
        pm.create(ProjectConfig("p1", "A", "/a"))
        with pytest.raises(ValueError, match="already exists"):
            pm.create(ProjectConfig("p1", "B", "/b"))

    def test_first_project_becomes_current(self):
        pm = ProjectManager()
        pm.create(ProjectConfig("p1", "A", "/a"))
        assert pm.current_id == "p1"
        assert pm.current is not None
        assert pm.current.name == "A"

    def test_list_projects(self):
        pm = ProjectManager()
        pm.create(ProjectConfig("p1", "A", "/a"))
        pm.create(ProjectConfig("p2", "B", "/b"))
        assert len(pm.list_projects()) == 2

    def test_list_active_only(self):
        pm = ProjectManager()
        pm.create(ProjectConfig("p1", "A", "/a"))
        pm.create(ProjectConfig("p2", "B", "/b"))
        pm.archive("p1")
        active = pm.list_projects(active_only=True)
        assert len(active) == 1
        assert active[0].project_id == "p2"

    def test_update(self):
        pm = ProjectManager()
        pm.create(ProjectConfig("p1", "A", "/a"))
        pm.update(ProjectConfig("p1", "Updated", "/b"))
        assert pm.get("p1").name == "Updated"

    def test_update_nonexistent_raises(self):
        pm = ProjectManager()
        with pytest.raises(KeyError):
            pm.update(ProjectConfig("x", "X", "/x"))

    def test_delete(self):
        pm = ProjectManager()
        pm.create(ProjectConfig("p1", "A", "/a"))
        assert pm.delete("p1") is True
        assert pm.get("p1") is None
        assert pm.delete("p1") is False

    def test_delete_current_switches(self):
        pm = ProjectManager()
        pm.create(ProjectConfig("p1", "A", "/a"))
        pm.create(ProjectConfig("p2", "B", "/b"))
        pm.set_current("p1")
        pm.delete("p1")
        assert pm.current_id == "p2"

    def test_set_current(self):
        pm = ProjectManager()
        pm.create(ProjectConfig("p1", "A", "/a"))
        pm.create(ProjectConfig("p2", "B", "/b"))
        pm.set_current("p2")
        assert pm.current_id == "p2"

    def test_set_current_nonexistent_raises(self):
        pm = ProjectManager()
        with pytest.raises(KeyError):
            pm.set_current("x")

    def test_archive_and_unarchive(self):
        pm = ProjectManager()
        pm.create(ProjectConfig("p1", "A", "/a"))
        assert pm.archive("p1") is True
        assert pm.get("p1").active is False
        assert pm.unarchive("p1") is True
        assert pm.get("p1").active is True
        assert pm.archive("nonexistent") is False

    def test_get_status(self):
        pm = ProjectManager()
        pm.create(ProjectConfig("p1", "A", "/a"))
        pm.create(ProjectConfig("p2", "B", "/b"))
        pm.archive("p2")
        status = pm.get_status()
        assert status["total_projects"] == 2
        assert status["active_projects"] == 1
        assert status["current_project"] == "p1"

    def test_empty_manager(self):
        pm = ProjectManager()
        assert pm.current is None
        assert pm.current_id is None
        assert pm.list_projects() == []


# --- UserManager ---


class TestUserManager:
    def test_create_user(self):
        um = UserManager()
        user = um.create_user("u1", "alice", "password123")
        assert user.user_id == "u1"
        assert user.username == "alice"
        assert user.role == UserRole.DEVELOPER
        assert len(user.api_key) == 64  # hex token
        assert user.active is True

    def test_create_duplicate_id_raises(self):
        um = UserManager()
        um.create_user("u1", "alice", "pw")
        with pytest.raises(ValueError, match="already exists"):
            um.create_user("u1", "bob", "pw")

    def test_create_duplicate_username_raises(self):
        um = UserManager()
        um.create_user("u1", "alice", "pw")
        with pytest.raises(ValueError, match="already taken"):
            um.create_user("u2", "alice", "pw")

    def test_get_user(self):
        um = UserManager()
        um.create_user("u1", "alice", "pw")
        assert um.get_user("u1") is not None
        assert um.get_user("u1").username == "alice"
        assert um.get_user("nonexistent") is None

    def test_get_by_username(self):
        um = UserManager()
        um.create_user("u1", "alice", "pw")
        assert um.get_by_username("alice") is not None
        assert um.get_by_username("bob") is None

    def test_get_by_api_key(self):
        um = UserManager()
        user = um.create_user("u1", "alice", "pw")
        found = um.get_by_api_key(user.api_key)
        assert found is not None
        assert found.user_id == "u1"
        assert um.get_by_api_key("invalid") is None

    def test_authenticate_success(self):
        um = UserManager()
        um.create_user("u1", "alice", "secret")
        user = um.authenticate("alice", "secret")
        assert user is not None
        assert user.user_id == "u1"

    def test_authenticate_wrong_password(self):
        um = UserManager()
        um.create_user("u1", "alice", "secret")
        assert um.authenticate("alice", "wrong") is None

    def test_authenticate_nonexistent(self):
        um = UserManager()
        assert um.authenticate("ghost", "pw") is None

    def test_authenticate_inactive(self):
        um = UserManager()
        um.create_user("u1", "alice", "pw")
        um.deactivate("u1")
        assert um.authenticate("alice", "pw") is None

    def test_list_users(self):
        um = UserManager()
        um.create_user("u1", "alice", "pw")
        um.create_user("u2", "bob", "pw")
        assert len(um.list_users()) == 2

    def test_list_users_active_only(self):
        um = UserManager()
        um.create_user("u1", "alice", "pw")
        um.create_user("u2", "bob", "pw")
        um.deactivate("u2")
        active = um.list_users(active_only=True)
        assert len(active) == 1

    def test_update_role(self):
        um = UserManager()
        um.create_user("u1", "alice", "pw")
        assert um.update_role("u1", UserRole.ADMIN) is True
        assert um.get_user("u1").role == UserRole.ADMIN
        assert um.update_role("nonexistent", UserRole.ADMIN) is False

    def test_deactivate_and_activate(self):
        um = UserManager()
        um.create_user("u1", "alice", "pw")
        assert um.deactivate("u1") is True
        assert um.get_user("u1").active is False
        assert um.activate("u1") is True
        assert um.get_user("u1").active is True
        assert um.deactivate("nonexistent") is False

    def test_regenerate_api_key(self):
        um = UserManager()
        user = um.create_user("u1", "alice", "pw")
        old_key = user.api_key
        new_key = um.regenerate_api_key("u1")
        assert new_key is not None
        assert new_key != old_key
        assert um.get_by_api_key(old_key) is None
        assert um.get_by_api_key(new_key) is not None
        assert um.regenerate_api_key("nonexistent") is None

    def test_delete_user(self):
        um = UserManager()
        user = um.create_user("u1", "alice", "pw")
        api_key = user.api_key
        assert um.delete_user("u1") is True
        assert um.get_user("u1") is None
        assert um.get_by_api_key(api_key) is None
        assert um.delete_user("u1") is False

    def test_has_permission_admin(self):
        um = UserManager()
        um.create_user("u1", "alice", "pw", role=UserRole.ADMIN)
        assert um.has_permission("u1", "config.write") is True
        assert um.has_permission("u1", "users.write") is True

    def test_has_permission_viewer(self):
        um = UserManager()
        um.create_user("u1", "viewer", "pw", role=UserRole.VIEWER)
        assert um.has_permission("u1", "dashboard.read") is True
        assert um.has_permission("u1", "agents.write") is False
        assert um.has_permission("u1", "agents.execute") is False

    def test_has_permission_inactive(self):
        um = UserManager()
        um.create_user("u1", "alice", "pw", role=UserRole.ADMIN)
        um.deactivate("u1")
        assert um.has_permission("u1", "config.read") is False

    def test_check_permission_raises(self):
        um = UserManager()
        um.create_user("u1", "viewer", "pw", role=UserRole.VIEWER)
        with pytest.raises(PermissionError):
            um.check_permission("u1", "config.write")

    def test_role_permissions_complete(self):
        # Admin should have all permissions that developer and viewer have
        assert ROLE_PERMISSIONS[UserRole.VIEWER].issubset(ROLE_PERMISSIONS[UserRole.DEVELOPER])
        assert ROLE_PERMISSIONS[UserRole.DEVELOPER].issubset(ROLE_PERMISSIONS[UserRole.ADMIN])


# --- ProviderPresetManager ---


class TestProviderPresetManager:
    def test_builtin_presets_exist(self):
        pm = ProviderPresetManager()
        names = pm.get_builtin_names()
        assert "local_only" in names
        assert "cloud_only" in names
        assert "hybrid" in names
        assert "high_quality" in names

    def test_list_presets(self):
        pm = ProviderPresetManager()
        presets = pm.list_presets()
        assert len(presets) >= 4

    def test_get_preset(self):
        pm = ProviderPresetManager()
        preset = pm.get("hybrid")
        assert preset is not None
        assert len(preset.providers) == 2
        assert preset.routing_strategy == "local_first"

    def test_activate_preset(self):
        pm = ProviderPresetManager()
        pm.activate("local_only")
        assert pm.active_name == "local_only"
        assert pm.active is not None
        assert pm.active.offline_mode is True

    def test_activate_nonexistent_raises(self):
        pm = ProviderPresetManager()
        with pytest.raises(KeyError):
            pm.activate("nonexistent")

    def test_add_custom_preset(self):
        pm = ProviderPresetManager()
        custom = ProviderPreset(
            name="custom_test",
            description="Test preset",
            providers=[ProviderPresetEntry("test", "ollama", "test-model")],
        )
        pm.add_custom(custom)
        assert pm.get("custom_test") is not None

    def test_remove_custom_preset(self):
        pm = ProviderPresetManager()
        pm.add_custom(ProviderPreset("custom", "Test", []))
        assert pm.remove("custom") is True
        assert pm.get("custom") is None

    def test_cannot_remove_builtin(self):
        pm = ProviderPresetManager()
        assert pm.remove("local_only") is False
        assert pm.get("local_only") is not None

    def test_get_provider_configs(self):
        pm = ProviderPresetManager()
        pm.activate("hybrid")
        configs = pm.get_provider_configs()
        assert len(configs) == 2
        assert configs[0]["key"] == "ollama"

    def test_get_default_provider_key(self):
        pm = ProviderPresetManager()
        pm.activate("hybrid")
        key = pm.get_default_provider_key()
        assert key == "ollama"

    def test_no_active_preset(self):
        pm = ProviderPresetManager()
        assert pm.active is None
        assert pm.active_name is None
        assert pm.get_provider_configs() == []
        assert pm.get_default_provider_key() is None

    def test_local_only_preset_offline(self):
        pm = ProviderPresetManager()
        preset = pm.get("local_only")
        assert preset.offline_mode is True
        assert len(preset.providers) == 1
        assert preset.providers[0].type == "ollama"

    def test_high_quality_preset(self):
        pm = ProviderPresetManager()
        preset = pm.get("high_quality")
        assert preset.routing_strategy == "capability_based"
        types = {p.type for p in preset.providers}
        assert "anthropic" in types


# --- MigrationManager ---


class TestMigrationManager:
    def test_supported_formats(self):
        mm = MigrationManager()
        fmts = mm.supported_formats
        assert "langgraph" in fmts
        assert "crewai" in fmts
        assert "autogen" in fmts

    def test_detect_langgraph(self):
        mm = MigrationManager()
        data = {"nodes": [{"name": "a"}], "edges": [["a", "b"]]}
        assert mm.detect_format(data) == "langgraph"

    def test_detect_crewai(self):
        mm = MigrationManager()
        data = {"agents": [{"name": "dev"}], "tasks": [{"description": "build"}]}
        assert mm.detect_format(data) == "crewai"

    def test_detect_autogen(self):
        mm = MigrationManager()
        data = {"agents": [{"name": "assistant", "llm_config": {"model": "gpt-4"}}]}
        assert mm.detect_format(data) == "autogen"

    def test_detect_unknown(self):
        mm = MigrationManager()
        assert mm.detect_format({"random": "data"}) is None

    def test_import_langgraph(self):
        mm = MigrationManager()
        data = {
            "name": "my_graph",
            "nodes": [
                {"name": "analyze", "type": "llm", "config": {"system": "Analyze"}},
                {"name": "report", "type": "custom"},
            ],
            "edges": [
                {"source": "__start__", "target": "analyze"},
                {"source": "analyze", "target": "report"},
                {"source": "report", "target": "__end__"},
            ],
        }
        result = mm.import_config(data, "langgraph")
        assert result.success is True
        assert result.nodes_imported == 2
        assert result.edges_imported == 3
        assert result.data["name"] == "my_graph"

    def test_import_langgraph_tuple_edges(self):
        mm = MigrationManager()
        data = {"nodes": ["a", "b"], "edges": [["a", "b"]]}
        result = mm.import_config(data)
        assert result.success is True
        assert result.edges_imported == 1

    def test_import_crewai(self):
        mm = MigrationManager()
        data = {
            "agents": [
                {"name": "Researcher", "goal": "Find info", "tools": ["search"]},
                {"role": "Writer", "backstory": "Expert writer"},
            ],
            "tasks": [
                {"description": "Research the topic", "agent": "researcher"},
            ],
        }
        result = mm.import_config(data, "crewai")
        assert result.success is True
        assert result.agents_imported == 2
        assert len(result.data["tasks"]) == 1

    def test_import_autogen(self):
        mm = MigrationManager()
        data = {
            "agents": [
                {
                    "name": "assistant",
                    "system_message": "You are a helpful assistant",
                    "llm_config": {"model": "gpt-4"},
                },
                {
                    "name": "user_proxy",
                    "type": "user_proxy",
                    "llm_config": {"model": "gpt-4"},
                },
            ],
        }
        result = mm.import_config(data, "autogen")
        assert result.success is True
        assert result.agents_imported == 2
        assert any("user_proxy" in w for w in result.warnings)

    def test_import_unknown_format(self):
        mm = MigrationManager()
        result = mm.import_config({"random": "data"})
        assert result.success is False
        assert "detect" in result.errors[0].lower()

    def test_import_unsupported_format(self):
        mm = MigrationManager()
        result = mm.import_config({}, source_format="unsupported")
        assert result.success is False

    def test_export_langgraph(self):
        mm = MigrationManager()
        graph_data = {
            "nodes": [{"name": "a", "type": "custom", "config": {}}],
            "edges": [{"source": "a", "target": "b", "condition": None}],
        }
        exported = mm.export_langgraph(graph_data)
        assert len(exported["nodes"]) == 1
        assert exported["nodes"][0]["id"] == "a"
        assert len(exported["edges"]) == 1

    def test_langgraph_unknown_node_type_warning(self):
        mm = MigrationManager()
        data = {
            "nodes": [{"name": "x", "type": "weird_type"}],
            "edges": [],
        }
        result = mm.import_config(data, "langgraph")
        assert result.success is True
        assert len(result.warnings) > 0

    def test_auto_detect_and_import(self):
        mm = MigrationManager()
        data = {"nodes": [{"name": "a"}], "edges": []}
        result = mm.import_config(data)
        assert result.success is True
        assert result.source_format == "langgraph"


# --- APIRegistry ---


class TestAPIRegistry:
    def test_default_endpoints_registered(self):
        reg = APIRegistry()
        endpoints = reg.list_endpoints()
        assert len(endpoints) > 20

    def test_list_by_tag(self):
        reg = APIRegistry()
        agent_eps = reg.list_endpoints(tag="agents")
        assert len(agent_eps) >= 4
        assert all("agents" in e.tags for e in agent_eps)

    def test_get_endpoint(self):
        reg = APIRegistry()
        ep = reg.get_endpoint("/agents", HTTPMethod.GET)
        assert ep is not None
        assert ep.summary == "List agents"

    def test_get_endpoint_not_found(self):
        reg = APIRegistry()
        assert reg.get_endpoint("/nonexistent", HTTPMethod.GET) is None

    def test_register_custom_endpoint(self):
        reg = APIRegistry()
        before = len(reg.list_endpoints())
        reg.register(
            APIEndpoint(
                path="/custom",
                method=HTTPMethod.GET,
                summary="Custom endpoint",
                tags=["custom"],
            )
        )
        assert len(reg.list_endpoints()) == before + 1
        assert reg.get_endpoint("/custom", HTTPMethod.GET) is not None

    def test_health_endpoint_no_auth(self):
        reg = APIRegistry()
        ep = reg.get_endpoint("/health", HTTPMethod.GET)
        assert ep is not None
        assert ep.auth_required is False

    def test_export_openapi_spec(self):
        reg = APIRegistry()
        spec = reg.export_openapi_spec()
        assert spec["openapi"] == "3.0.3"
        assert spec["info"]["title"] == "Agent Orchestrator API"
        assert spec["info"]["version"] == "1.0.0"
        assert len(spec["paths"]) > 0
        assert "apiKey" in spec["components"]["securitySchemes"]

    def test_export_openapi_paths(self):
        reg = APIRegistry()
        paths = reg.export_openapi_paths()
        agents_path = f"{API_PREFIX}/agents"
        assert agents_path in paths
        assert "get" in paths[agents_path]
        assert "post" in paths[agents_path]

    def test_api_response_model(self):
        resp = APIResponse(success=True, data={"key": "value"})
        assert resp.success is True
        assert resp.data["key"] == "value"
        assert resp.error is None

    def test_webhook_receive_no_auth(self):
        reg = APIRegistry()
        ep = reg.get_endpoint("/webhooks/{webhook_id}/receive", HTTPMethod.POST)
        assert ep is not None
        assert ep.auth_required is False

    def test_all_write_endpoints_require_auth(self):
        reg = APIRegistry()
        for ep in reg.list_endpoints():
            if ep.method in (HTTPMethod.POST, HTTPMethod.PUT, HTTPMethod.DELETE):
                if "receive" not in ep.path:
                    assert ep.auth_required is True, f"{ep.method} {ep.path} should require auth"
