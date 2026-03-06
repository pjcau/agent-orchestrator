"""Tests for v0.8.0 — External Integrations."""

import pytest
from agent_orchestrator.core.plugins import PluginLoader, PluginManifest
from agent_orchestrator.core.webhook import WebhookConfig, WebhookRegistry
from agent_orchestrator.core.mcp_server import MCPResource, MCPServerRegistry, MCPTool
from agent_orchestrator.core.offline import OfflineConfig, OfflineManager
from agent_orchestrator.core.skill import SkillRegistry
from agent_orchestrator.skills.webhook_skill import WebhookSkill


# --- PluginLoader ---


class TestPluginLoader:
    def test_register_and_get(self):
        loader = PluginLoader()
        manifest = PluginManifest(
            name="my-skill",
            version="1.0.0",
            plugin_type="skill",
            description="A custom skill",
        )
        loader.register(manifest)
        assert loader.get_manifest("my-skill") is not None
        assert loader.get_manifest("my-skill").version == "1.0.0"

    def test_load_from_dict(self):
        loader = PluginLoader()
        manifest = loader.load_from_dict({
            "name": "test-plugin",
            "version": "2.0",
            "plugin_type": "provider",
            "description": "Test",
            "author": "tester",
        })
        assert manifest.name == "test-plugin"
        assert loader.get_manifest("test-plugin") is not None

    def test_list_plugins_all(self):
        loader = PluginLoader()
        loader.register(PluginManifest("a", "1.0", "skill"))
        loader.register(PluginManifest("b", "1.0", "provider"))
        loader.register(PluginManifest("c", "1.0", "skill"))
        assert len(loader.list_plugins()) == 3

    def test_list_plugins_filtered(self):
        loader = PluginLoader()
        loader.register(PluginManifest("a", "1.0", "skill"))
        loader.register(PluginManifest("b", "1.0", "provider"))
        skills = loader.list_plugins(plugin_type="skill")
        assert len(skills) == 1
        assert skills[0].name == "a"

    def test_unregister(self):
        loader = PluginLoader()
        loader.register(PluginManifest("x", "1.0", "skill"))
        assert loader.unregister("x") is True
        assert loader.get_manifest("x") is None
        assert loader.unregister("x") is False

    def test_register_skill_instance(self):
        loader = PluginLoader()
        loader.register_skill_instance("my-skill", "fake-instance")
        skills = loader.get_loaded_skills()
        assert "my-skill" in skills

    def test_register_provider_instance(self):
        loader = PluginLoader()
        loader.register_provider_instance("my-provider", "fake-provider")
        providers = loader.get_loaded_providers()
        assert "my-provider" in providers

    def test_unregister_cleans_instances(self):
        loader = PluginLoader()
        loader.register(PluginManifest("x", "1.0", "skill"))
        loader.register_skill_instance("x", "instance")
        loader.unregister("x")
        assert "x" not in loader.get_loaded_skills()

    def test_to_dict(self):
        loader = PluginLoader()
        loader.register(PluginManifest("a", "1.0", "skill", description="test"))
        exported = loader.to_dict()
        assert len(exported) == 1
        assert exported[0]["name"] == "a"
        assert exported[0]["description"] == "test"


# --- WebhookRegistry ---


class TestWebhookRegistry:
    def test_register_and_get(self):
        reg = WebhookRegistry()
        config = WebhookConfig(webhook_id="w1", name="deploy", path="/hooks/deploy")
        reg.register(config)
        assert reg.get("w1") is not None
        assert reg.get("w1").path == "/hooks/deploy"

    def test_get_by_path(self):
        reg = WebhookRegistry()
        reg.register(WebhookConfig("w1", "test", "/hooks/test"))
        result = reg.get_by_path("/hooks/test")
        assert result is not None
        assert result.webhook_id == "w1"

    def test_get_by_path_not_found(self):
        reg = WebhookRegistry()
        assert reg.get_by_path("/nonexistent") is None

    def test_unregister(self):
        reg = WebhookRegistry()
        reg.register(WebhookConfig("w1", "test", "/hooks/test"))
        assert reg.unregister("w1") is True
        assert reg.get("w1") is None
        assert reg.unregister("w1") is False

    def test_list_webhooks(self):
        reg = WebhookRegistry()
        reg.register(WebhookConfig("w1", "a", "/a"))
        reg.register(WebhookConfig("w2", "b", "/b"))
        assert len(reg.list_webhooks()) == 2

    def test_receive_event(self):
        reg = WebhookRegistry()
        reg.register(WebhookConfig("w1", "test", "/test"))
        event = reg.receive("w1", {"action": "push"}, {"X-Event": "push"})
        assert event.webhook_id == "w1"
        assert event.payload["action"] == "push"
        assert event.processed is False

    def test_get_events_filtered(self):
        reg = WebhookRegistry()
        reg.register(WebhookConfig("w1", "a", "/a"))
        reg.register(WebhookConfig("w2", "b", "/b"))
        reg.receive("w1", {}, {})
        reg.receive("w2", {}, {})
        reg.receive("w1", {}, {})
        events = reg.get_events(webhook_id="w1")
        assert len(events) == 2

    def test_get_events_by_processed(self):
        reg = WebhookRegistry()
        reg.register(WebhookConfig("w1", "a", "/a"))
        reg.receive("w1", {}, {})
        reg.receive("w1", {}, {})
        reg.mark_processed(0, "done")
        unprocessed = reg.get_events(processed=False)
        assert len(unprocessed) == 1

    def test_validate_signature_no_secret(self):
        reg = WebhookRegistry()
        reg.register(WebhookConfig("w1", "test", "/test", secret=None))
        assert reg.validate_signature("w1", b"payload", "any-sig") is True

    def test_validate_signature_correct(self):
        import hashlib
        import hmac

        secret = "my-secret"
        payload = b'{"action": "push"}'
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        reg = WebhookRegistry()
        reg.register(WebhookConfig("w1", "test", "/test", secret=secret))
        assert reg.validate_signature("w1", payload, expected) is True

    def test_validate_signature_wrong(self):
        reg = WebhookRegistry()
        reg.register(WebhookConfig("w1", "test", "/test", secret="secret"))
        assert reg.validate_signature("w1", b"payload", "wrong-signature") is False

    def test_validate_signature_unknown_webhook(self):
        reg = WebhookRegistry()
        assert reg.validate_signature("unknown", b"payload", "sig") is False

    def test_mark_processed(self):
        reg = WebhookRegistry()
        reg.register(WebhookConfig("w1", "test", "/test"))
        reg.receive("w1", {}, {})
        reg.mark_processed(0, "success")
        events = reg.get_events()
        assert events[0].processed is True
        assert events[0].result == "success"


# --- MCPServerRegistry ---


class TestMCPServerRegistry:
    def test_register_and_list_tools(self):
        reg = MCPServerRegistry()
        tool = MCPTool(
            name="test_tool",
            description="A test tool",
            input_schema={"type": "object"},
            handler="test.handler",
        )
        reg.register_tool(tool)
        assert len(reg.list_tools()) == 1
        assert reg.get_tool("test_tool") is not None

    def test_register_and_list_resources(self):
        reg = MCPServerRegistry()
        resource = MCPResource(
            uri="file:///docs/readme",
            name="readme",
            description="Project README",
        )
        reg.register_resource(resource)
        assert len(reg.list_resources()) == 1
        assert reg.get_resource("file:///docs/readme") is not None

    def test_unregister_tool(self):
        reg = MCPServerRegistry()
        reg.register_tool(MCPTool("t1", "test", {}, "h"))
        assert reg.unregister_tool("t1") is True
        assert reg.get_tool("t1") is None
        assert reg.unregister_tool("t1") is False

    def test_unregister_resource(self):
        reg = MCPServerRegistry()
        reg.register_resource(MCPResource("uri://test", "test", "desc"))
        assert reg.unregister_resource("uri://test") is True
        assert reg.unregister_resource("uri://test") is False

    def test_export_manifest(self):
        reg = MCPServerRegistry(server_name="test-server", version="1.0")
        reg.register_tool(MCPTool("t1", "desc", {"type": "object"}, "handler"))
        reg.register_resource(MCPResource("uri://r", "r", "res"))
        manifest = reg.export_manifest()
        assert manifest["name"] == "test-server"
        assert manifest["version"] == "1.0"
        assert len(manifest["tools"]) == 1
        assert len(manifest["resources"]) == 1

    def test_register_agent_tools(self):
        reg = MCPServerRegistry()
        reg.register_agent_tools({
            "backend": {"role": "Backend developer"},
            "frontend": {"role": "Frontend developer"},
        })
        tools = reg.list_tools()
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert "agent_run_backend" in names
        assert "agent_run_frontend" in names

    def test_register_skill_tools(self):
        from agent_orchestrator.skills.webhook_skill import WebhookSkill

        skill_reg = SkillRegistry()
        ws = WebhookSkill()
        skill_reg.register(ws)

        mcp = MCPServerRegistry()
        mcp.register_skill_tools(["webhook_send"], skill_reg)
        tools = mcp.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "skill_webhook_send"


# --- OfflineManager ---


class TestOfflineManager:
    def test_default_is_online(self):
        mgr = OfflineManager()
        assert mgr.is_offline is False

    def test_enable_offline(self):
        mgr = OfflineManager()
        mgr.enable()
        assert mgr.is_offline is True

    def test_disable_offline(self):
        mgr = OfflineManager(OfflineConfig(enabled=True))
        assert mgr.is_offline is True
        mgr.disable()
        assert mgr.is_offline is False

    def test_filter_providers_online(self):
        mgr = OfflineManager()
        providers = {"ollama": "local", "openrouter": "cloud"}
        filtered = mgr.filter_providers(providers)
        assert len(filtered) == 2

    def test_filter_providers_offline(self):
        mgr = OfflineManager(OfflineConfig(enabled=True, local_provider_keys=["ollama"]))
        providers = {"ollama": "local", "openrouter": "cloud"}
        filtered = mgr.filter_providers(providers)
        assert len(filtered) == 1
        assert "ollama" in filtered

    def test_is_provider_allowed_online(self):
        mgr = OfflineManager()
        assert mgr.is_provider_allowed("openrouter") is True

    def test_is_provider_allowed_offline(self):
        mgr = OfflineManager(OfflineConfig(enabled=True, local_provider_keys=["local"]))
        assert mgr.is_provider_allowed("local") is True
        assert mgr.is_provider_allowed("openrouter") is False

    def test_get_status(self):
        mgr = OfflineManager(OfflineConfig(enabled=True))
        status = mgr.get_status()
        assert status["is_offline"] is True
        assert "local_provider_keys" in status


# --- WebhookSkill ---


class TestWebhookSkill:
    @pytest.mark.asyncio
    async def test_send_webhook(self):
        skill = WebhookSkill()
        result = await skill.execute({
            "url": "https://hooks.example.com/notify",
            "payload": {"event": "deploy", "status": "success"},
        })
        assert result.success is True
        assert result.output["status"] == "queued"
        assert result.output["url"] == "https://hooks.example.com/notify"

    @pytest.mark.asyncio
    async def test_send_with_method_and_headers(self):
        skill = WebhookSkill()
        result = await skill.execute({
            "url": "https://api.example.com",
            "method": "PUT",
            "payload": {"data": "test"},
            "headers": {"Authorization": "Bearer token"},
        })
        assert result.success is True
        assert result.output["method"] == "PUT"
        assert result.output["headers"]["Authorization"] == "Bearer token"

    @pytest.mark.asyncio
    async def test_get_sent_history(self):
        skill = WebhookSkill()
        await skill.execute({"url": "https://a.com", "payload": {}})
        await skill.execute({"url": "https://b.com", "payload": {}})
        sent = skill.get_sent()
        assert len(sent) == 2
        assert sent[0]["url"] == "https://a.com"
        assert sent[1]["url"] == "https://b.com"

    @pytest.mark.asyncio
    async def test_default_method_is_post(self):
        skill = WebhookSkill()
        result = await skill.execute({"url": "https://x.com", "payload": {}})
        assert result.output["method"] == "POST"

    def test_skill_properties(self):
        skill = WebhookSkill()
        assert skill.name == "webhook_send"
        assert "webhook" in skill.description.lower()
        assert "url" in skill.parameters["properties"]
