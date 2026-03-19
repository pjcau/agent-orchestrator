# Feature: YAML Configuration with Reflection

## Context

From DeerFlow analysis (analysis/deepflow/14-configuration.md, 29-learnings.md L10).
DeerFlow's `use: langchain_openai:ChatOpenAI` pattern lets users swap implementations without code changes. Our Python-based configuration requires code changes for every provider/tool swap.

## What to Build

Add YAML-based configuration with reflection-based class loading in `src/agent_orchestrator/core/yaml_config.py`:

### 1. YAML Config Format

```yaml
# orchestrator.yaml
config_version: 1

providers:
  local:
    use: agent_orchestrator.providers.local:LocalProvider
    params:
      base_url: "http://localhost:11434"
      default_model: "qwen2.5-coder"

  openrouter:
    use: agent_orchestrator.providers.openrouter:OpenRouterProvider
    params:
      api_key: "${OPENROUTER_API_KEY}"  # env var substitution
      default_model: "qwen/qwen3.5-plus"

agents:
  backend:
    provider: local
    model: qwen2.5-coder
    max_steps: 10
    skills: [file_read, file_write, shell_exec, glob_search, web_read]

  ai-engineer:
    provider: openrouter
    model: deepseek/deepseek-r1
    max_steps: 20
    skills: [file_read, file_write, shell_exec, glob_search, web_read, load_skill]

routing:
  strategy: complexity_based    # local_first, cost_optimized, complexity_based, etc.
  fallback_chain: [local, openrouter]

budgets:
  daily_limit_usd: 5.00
  per_task_limit_usd: 0.50
  alert_threshold_pct: 80
```

### 2. Reflection-Based Class Loading

```python
def load_class(class_path: str) -> type:
    """Load a class from a 'module.path:ClassName' string.

    Example: 'agent_orchestrator.providers.local:LocalProvider'
    → imports agent_orchestrator.providers.local, returns LocalProvider class
    """
    module_path, class_name = class_path.rsplit(":", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)
```

### 3. Environment Variable Substitution

```python
def resolve_env_vars(value: str) -> str:
    """Replace ${VAR_NAME} with os.environ['VAR_NAME']. Raise if missing."""
    import re
    def replacer(match):
        var_name = match.group(1)
        val = os.environ.get(var_name)
        if val is None:
            raise ValueError(f"Environment variable {var_name} not set")
        return val
    return re.sub(r'\$\{(\w+)\}', replacer, value)
```

### 4. Config Versioning

```python
CURRENT_CONFIG_VERSION = 1

def upgrade_config(config: dict) -> dict:
    """Auto-upgrade config from older versions to current."""
    version = config.get("config_version", 0)
    if version < 1:
        # v0 → v1: rename 'models' to 'providers'
        config["providers"] = config.pop("models", {})
        config["config_version"] = 1
    return config
```

### 5. Loader API

```python
class YAMLConfigLoader:
    def __init__(self, config_path: str = "orchestrator.yaml"):
        ...

    def load(self) -> OrchestratorConfig:
        """Load and validate YAML config, resolve env vars, upgrade if needed."""
        ...

    def validate(self, config: dict) -> list[str]:
        """Return list of validation errors (empty = valid)."""
        ...

    def save(self, config: OrchestratorConfig, path: str | None = None) -> None:
        """Save config back to YAML."""
        ...

@dataclass
class OrchestratorConfig:
    config_version: int
    providers: dict[str, ProviderConfig]
    agents: dict[str, AgentConfig]
    routing: RoutingConfig
    budgets: BudgetConfig
```

### 6. Integration

- **ConfigManager** (`src/agent_orchestrator/core/config_manager.py`): Add `load_yaml()` and `save_yaml()` methods alongside existing JSON support.
- **Dashboard startup**: Auto-detect `orchestrator.yaml` and load it if present. Fall back to existing JSON/Python config.

## Files to Modify

- **Create**: `src/agent_orchestrator/core/yaml_config.py`
- **Create**: `orchestrator.yaml.example` (example config with comments)
- **Modify**: `src/agent_orchestrator/core/config_manager.py` (add YAML support)
- **Modify**: `src/agent_orchestrator/dashboard/app.py` (auto-detect YAML on startup)

## Tests

- Test YAML loading with valid config
- Test env var substitution (present and missing vars)
- Test reflection class loading (valid and invalid paths)
- Test config version upgrade (v0 → v1)
- Test validation catches missing required fields
- Test validation catches invalid class paths
- Test save/load roundtrip
- Test fallback to JSON when no YAML present
- Test example config file is valid

## Acceptance Criteria

- [ ] YAML config format with `use:` reflection pattern
- [ ] Environment variable substitution (${VAR})
- [ ] Config versioning with auto-upgrade
- [ ] Validation with clear error messages
- [ ] Integration with existing ConfigManager
- [ ] Example config file with documentation
- [ ] All tests pass
- [ ] Existing tests still pass
