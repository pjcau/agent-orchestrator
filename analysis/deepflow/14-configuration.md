# 14 - Configuration

## Config Files

| File | Purpose | Location |
|------|---------|----------|
| `config.yaml` | Main app config | Project root |
| `extensions_config.json` | MCP servers + skills state | Project root |
| `.env` | API keys and secrets | Project root |

## Config Resolution Priority

### config.yaml
1. Explicit `config_path` argument
2. `DEER_FLOW_CONFIG_PATH` env var
3. `config.yaml` in CWD
4. `config.yaml` in parent directory

### extensions_config.json
Same pattern with `DEER_FLOW_EXTENSIONS_CONFIG_PATH`.

## config.yaml Sections

```yaml
config_version: 1          # Schema versioning

models:                     # LLM model definitions
  - name: gpt-4
    use: langchain_openai:ChatOpenAI
    model: gpt-4
    api_key: $OPENAI_API_KEY
    supports_thinking: false
    supports_vision: true

tool_groups:                # Logical tool groupings
  - name: web
  - name: file:read
  - name: file:write
  - name: bash

tools:                      # Tool definitions
  - name: web_search
    group: web
    use: deerflow.community.tavily.tools:web_search_tool

sandbox:                    # Execution environment
  use: deerflow.sandbox.local:LocalSandboxProvider

skills:                     # Skills directory config
  container_path: /mnt/skills

title:                      # Auto-title generation
  enabled: true
  max_words: 6

summarization:              # Context reduction
  enabled: true
  trigger:
    - type: tokens
      value: 15564

memory:                     # Long-term memory
  enabled: true
  max_facts: 100

checkpointer:               # State persistence
  type: sqlite

channels:                   # IM integrations
  telegram:
    enabled: true
```

## Config Versioning

```yaml
config_version: 1
```

On startup, `AppConfig.from_file()` compares user version vs example version:
- Emits warning if outdated
- `make config-upgrade` auto-merges missing fields
- Missing `config_version` = version 0

## Environment Variable Resolution

Any config value starting with `$` is resolved:
```python
# In config loading
if isinstance(value, str) and value.startswith("$"):
    value = os.environ.get(value[1:])
```

## Pydantic Validation

`AppConfig` uses Pydantic models:
```python
class AppConfig(BaseModel):
    models: list[ModelConfig]
    sandbox: SandboxConfig
    tools: list[ToolConfig]
    skills: SkillsConfig
    extensions: ExtensionsConfig
    checkpointer: CheckpointerConfig | None
```

## Key Insight

DeerFlow's YAML config is more user-friendly than our Python-based configuration. The `use` reflection pattern allows swapping implementations without changing code. Config versioning with auto-upgrade is a nice touch we should consider.
