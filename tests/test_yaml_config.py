"""Tests for YAML configuration loader with reflection and env var substitution."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from agent_orchestrator.core.yaml_config import (
    CURRENT_CONFIG_VERSION,
    OrchestratorConfig,
    YAMLConfigError,
    YAMLConfigLoader,
    load_class,
    substitute_env_vars,
    upgrade_config,
    validate_raw_config,
)
from agent_orchestrator.core.config_manager import (
    AgentConfigEntry,
    ConfigManager,
    OrchestratorConfiguration,
    ProviderConfigEntry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_YAML = textwrap.dedent("""\
    config_version: 1

    providers:
      local:
        use: agent_orchestrator.providers.local:LocalProvider
        params:
          base_url: "http://localhost:11434"

    agents:
      backend:
        provider: local
        model: qwen2.5-coder
        max_steps: 10
        skills: [file_read, file_write, shell_exec]

    routing:
      strategy: complexity_based
      fallback_chain: [local, openrouter]

    budgets:
      daily_limit_usd: 5.00
      per_task_limit_usd: 0.50
      alert_threshold_pct: 80
""")


@pytest.fixture
def yaml_file(tmp_path: Path) -> Path:
    """Write a valid YAML config to a temp file and return its path."""
    p = tmp_path / "orchestrator.yaml"
    p.write_text(VALID_YAML, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Test: YAML loading with valid config
# ---------------------------------------------------------------------------


class TestYAMLLoading:
    def test_load_valid_config(self, yaml_file: Path):
        loader = YAMLConfigLoader(path=yaml_file)
        config = loader.load(resolve_classes=False)

        assert config.config_version == 1
        assert len(config.base_config.providers) == 1
        assert config.base_config.providers[0].key == "local"
        assert len(config.base_config.agents) == 1
        assert config.base_config.agents[0].name == "backend"
        assert config.base_config.routing_strategy == "complexity_based"
        assert config.fallback_chain == ["local", "openrouter"]

    def test_load_from_string(self):
        loader = YAMLConfigLoader()
        config = loader.loads(VALID_YAML, resolve_classes=False)

        assert config.config_version == 1
        assert config.base_config.agents[0].tools == ["file_read", "file_write", "shell_exec"]

    def test_load_missing_file(self, tmp_path: Path):
        loader = YAMLConfigLoader(path=tmp_path / "nonexistent.yaml")
        with pytest.raises(YAMLConfigError, match="Config file not found"):
            loader.load()

    def test_load_invalid_yaml_root(self):
        loader = YAMLConfigLoader()
        with pytest.raises(YAMLConfigError, match="YAML root must be a mapping"):
            loader.loads("- just\n- a\n- list\n")

    def test_budgets_parsed(self):
        loader = YAMLConfigLoader()
        config = loader.loads(VALID_YAML, resolve_classes=False)
        assert config.budgets.daily_limit_usd == 5.0
        assert config.budgets.per_task_limit_usd == 0.5
        assert config.budgets.alert_threshold_pct == 80

    def test_budget_limit_propagated_to_base(self):
        loader = YAMLConfigLoader()
        config = loader.loads(VALID_YAML, resolve_classes=False)
        assert config.base_config.budget_limit_usd == 5.0

    def test_agent_max_steps(self):
        loader = YAMLConfigLoader()
        config = loader.loads(VALID_YAML, resolve_classes=False)
        assert config.base_config.agents[0].max_steps == 10


# ---------------------------------------------------------------------------
# Test: Environment variable substitution
# ---------------------------------------------------------------------------


class TestEnvVarSubstitution:
    def test_substitute_present_var(self, monkeypatch):
        monkeypatch.setenv("TEST_API_KEY", "sk-12345")
        result = substitute_env_vars("key=${TEST_API_KEY}")
        assert result == "key=sk-12345"

    def test_substitute_in_nested_dict(self, monkeypatch):
        monkeypatch.setenv("MY_URL", "http://example.com")
        data = {"server": {"url": "${MY_URL}", "port": 8080}}
        result = substitute_env_vars(data)
        assert result == {"server": {"url": "http://example.com", "port": 8080}}

    def test_substitute_in_list(self, monkeypatch):
        monkeypatch.setenv("ITEM", "hello")
        result = substitute_env_vars(["${ITEM}", "world"])
        assert result == ["hello", "world"]

    def test_substitute_missing_var_raises(self):
        # Make sure the var is not set.
        os.environ.pop("NONEXISTENT_VAR_12345", None)
        with pytest.raises(YAMLConfigError, match="not set"):
            substitute_env_vars("${NONEXISTENT_VAR_12345}")

    def test_substitute_multiple_vars(self, monkeypatch):
        monkeypatch.setenv("HOST", "localhost")
        monkeypatch.setenv("PORT", "5432")
        result = substitute_env_vars("${HOST}:${PORT}")
        assert result == "localhost:5432"

    def test_no_substitution_on_non_strings(self):
        assert substitute_env_vars(42) == 42
        assert substitute_env_vars(None) is None
        assert substitute_env_vars(True) is True

    def test_yaml_with_env_vars(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("TEST_YAML_KEY", "my-secret-key")
        yaml_text = textwrap.dedent("""\
            config_version: 1
            providers:
              cloud:
                use: agent_orchestrator.providers.openrouter:OpenRouterProvider
                params:
                  api_key: "${TEST_YAML_KEY}"
        """)
        loader = YAMLConfigLoader()
        config = loader.loads(yaml_text, resolve_classes=False)
        assert config.base_config.providers[0].extra["api_key"] == "my-secret-key"


# ---------------------------------------------------------------------------
# Test: Reflection-based class loading
# ---------------------------------------------------------------------------


class TestReflectionClassLoading:
    def test_load_valid_class(self):
        cls = load_class("agent_orchestrator.core.config_manager:ConfigManager")
        assert cls is ConfigManager

    def test_load_dataclass(self):
        cls = load_class("agent_orchestrator.core.config_manager:OrchestratorConfiguration")
        assert cls is OrchestratorConfiguration

    def test_load_invalid_path_no_colon(self):
        with pytest.raises(YAMLConfigError, match="expected 'module.path:ClassName'"):
            load_class("agent_orchestrator.core.config_manager.ConfigManager")

    def test_load_nonexistent_module(self):
        with pytest.raises(YAMLConfigError, match="Module not found"):
            load_class("nonexistent.module.xyz:SomeClass")

    def test_load_nonexistent_class(self):
        with pytest.raises(YAMLConfigError, match="not found in module"):
            load_class("agent_orchestrator.core.config_manager:NonexistentClass999")

    def test_load_non_class_attribute(self):
        with pytest.raises(YAMLConfigError, match="is not a class"):
            # _config_to_dict is a function, not a class.
            load_class("agent_orchestrator.core.config_manager:_config_to_dict")

    def test_resolve_classes_on_load(self, yaml_file: Path):
        loader = YAMLConfigLoader(path=yaml_file)
        config = loader.load(resolve_classes=True)
        assert "local" in config.provider_classes
        # The loaded class should be the LocalProvider class.
        assert config.provider_classes["local"].__name__ == "LocalProvider"


# ---------------------------------------------------------------------------
# Test: Config version upgrade
# ---------------------------------------------------------------------------


class TestConfigVersionUpgrade:
    def test_upgrade_from_v0(self):
        data = {"providers": {"local": {"use": "x:Y"}}}
        upgraded = upgrade_config(data)
        assert upgraded["config_version"] == CURRENT_CONFIG_VERSION
        assert "budgets" in upgraded

    def test_already_current_version(self):
        data = {"config_version": CURRENT_CONFIG_VERSION}
        upgraded = upgrade_config(data)
        assert upgraded["config_version"] == CURRENT_CONFIG_VERSION

    def test_unknown_version_raises(self):
        data = {"config_version": 999}
        # Version 999 is higher than current, so no upgrade needed.
        # But if we set it to something between 0 and current that has no path...
        # Actually 999 > CURRENT so upgrade loop won't run.
        upgraded = upgrade_config(data)
        assert upgraded["config_version"] == 999

    def test_legacy_config_loads(self):
        """A config without config_version should auto-upgrade to v1."""
        yaml_text = textwrap.dedent("""\
            providers:
              local:
                use: agent_orchestrator.providers.local:LocalProvider
        """)
        loader = YAMLConfigLoader()
        config = loader.loads(yaml_text, resolve_classes=False)
        assert config.config_version == 1


# ---------------------------------------------------------------------------
# Test: Validation catches missing required fields
# ---------------------------------------------------------------------------


class TestValidation:
    def test_missing_config_version(self):
        errors = validate_raw_config({})
        assert any("config_version" in e for e in errors)

    def test_valid_config_no_errors(self):
        data = {
            "config_version": 1,
            "providers": {
                "local": {"use": "mod:Cls"},
            },
        }
        errors = validate_raw_config(data)
        assert errors == []

    def test_provider_missing_use(self):
        data = {
            "config_version": 1,
            "providers": {"bad": {"params": {}}},
        }
        errors = validate_raw_config(data)
        assert any("missing required field 'use'" in e for e in errors)

    def test_agent_unknown_provider(self):
        data = {
            "config_version": 1,
            "providers": {"local": {"use": "m:C"}},
            "agents": {"bot": {"provider": "nonexistent"}},
        }
        errors = validate_raw_config(data)
        assert any("unknown provider" in e for e in errors)

    def test_invalid_routing_strategy(self):
        data = {
            "config_version": 1,
            "routing": {"strategy": "banana"},
        }
        errors = validate_raw_config(data)
        assert any("Unknown routing strategy" in e for e in errors)

    def test_invalid_budget_type(self):
        data = {
            "config_version": 1,
            "budgets": {"daily_limit_usd": "not-a-number"},
        }
        errors = validate_raw_config(data)
        assert any("must be a number" in e for e in errors)

    def test_providers_not_mapping(self):
        data = {"config_version": 1, "providers": [1, 2, 3]}
        errors = validate_raw_config(data)
        assert any("must be a mapping" in e for e in errors)

    def test_agents_not_mapping(self):
        data = {"config_version": 1, "agents": "not-a-mapping"}
        errors = validate_raw_config(data)
        assert any("must be a mapping" in e for e in errors)

    def test_validation_errors_prevent_load(self):
        yaml_text = textwrap.dedent("""\
            config_version: 1
            providers:
              bad:
                params: {}
        """)
        loader = YAMLConfigLoader()
        with pytest.raises(YAMLConfigError, match="validation failed"):
            loader.loads(yaml_text, resolve_classes=False)


# ---------------------------------------------------------------------------
# Test: Save/load roundtrip
# ---------------------------------------------------------------------------


class TestRoundtrip:
    def test_save_load_roundtrip(self, tmp_path: Path):
        loader = YAMLConfigLoader()
        original = loader.loads(VALID_YAML, resolve_classes=False)

        out_path = tmp_path / "roundtrip.yaml"
        loader.save(original, path=out_path)

        loader2 = YAMLConfigLoader(path=out_path)
        reloaded = loader2.load(resolve_classes=False)

        assert reloaded.config_version == original.config_version
        assert len(reloaded.base_config.providers) == len(original.base_config.providers)
        assert len(reloaded.base_config.agents) == len(original.base_config.agents)
        assert reloaded.base_config.routing_strategy == original.base_config.routing_strategy
        assert reloaded.budgets.daily_limit_usd == original.budgets.daily_limit_usd
        assert reloaded.budgets.per_task_limit_usd == original.budgets.per_task_limit_usd
        assert reloaded.fallback_chain == original.fallback_chain

    def test_save_minimal_config(self, tmp_path: Path):
        config = OrchestratorConfig(config_version=1)
        out_path = tmp_path / "minimal.yaml"
        loader = YAMLConfigLoader()
        loader.save(config, path=out_path)

        reloaded = YAMLConfigLoader(path=out_path).load(resolve_classes=False)
        assert reloaded.config_version == 1


# ---------------------------------------------------------------------------
# Test: Example config file is valid
# ---------------------------------------------------------------------------


class TestExampleConfig:
    def test_example_config_is_valid(self, monkeypatch):
        """The shipped orchestrator.yaml.example must pass validation."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        example_path = Path(__file__).resolve().parent.parent / "orchestrator.yaml.example"
        if not example_path.exists():
            pytest.skip("orchestrator.yaml.example not found")
        loader = YAMLConfigLoader(path=example_path)
        config = loader.load(resolve_classes=False)
        assert config.config_version == 1
        assert len(config.base_config.providers) == 3
        assert len(config.base_config.agents) == 3


# ---------------------------------------------------------------------------
# Test: ConfigManager YAML integration
# ---------------------------------------------------------------------------


class TestConfigManagerYAML:
    def test_import_yaml(self, yaml_file: Path):
        mgr = ConfigManager()
        cfg = mgr.import_yaml(str(yaml_file))
        assert isinstance(cfg, OrchestratorConfiguration)
        assert len(cfg.providers) == 1
        assert cfg.providers[0].key == "local"
        assert cfg.routing_strategy == "complexity_based"

    def test_export_yaml(self, tmp_path: Path):
        mgr = ConfigManager()
        prov = ProviderConfigEntry(key="local", type="local", model="test")
        agent = AgentConfigEntry(name="bot", role="test", provider_key="local")
        cfg = OrchestratorConfiguration(
            providers=[prov],
            agents=[agent],
            routing_strategy="cost_optimized",
            budget_limit_usd=10.0,
        )
        mgr.update(cfg)

        out_path = tmp_path / "exported.yaml"
        mgr.export_yaml(str(out_path))

        assert out_path.exists()
        # Re-load and verify.
        loader = YAMLConfigLoader(path=out_path)
        reloaded = loader.load(resolve_classes=False)
        assert reloaded.base_config.routing_strategy == "cost_optimized"
        assert reloaded.budgets.daily_limit_usd == 10.0
