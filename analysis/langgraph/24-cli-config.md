# LangGraph — Configuration Format (langgraph.json)

## Config Schema

```python
class Config(TypedDict, total=False):
    python_version: str                # "3.11", "3.12", "3.13"
    node_version: str | None           # "20" for JS projects
    dependencies: list[str]            # [".", "langchain_openai", "./local_pkg"]
    graphs: dict[str, str | GraphDef]  # {"my_graph": "./file.py:variable"}
    env: dict[str, str] | str          # Env vars dict or ".env" file path
    store: StoreConfig | None          # Long-term memory config
    checkpointer: CheckpointerConfig   # State persistence config
    auth: AuthConfig | None            # Custom auth handler
    encryption: EncryptionConfig       # At-rest encryption
    http: HttpConfig | None            # CORS, route disabling
    webhooks: WebhooksConfig | None    # Webhook handlers
    ui: dict[str, str] | None          # UI components
    dockerfile_lines: list[str]        # Extra Dockerfile instructions
    pip_installer: str | None          # "auto", "pip", "uv"
    image_distro: Distros | None       # "debian", "wolfi", "bookworm"
```

## Graph Definition

```json
{
  "graphs": {
    "my_agent": "./src/agent.py:graph",
    "chat": {
      "path": "./src/chat.py:build_graph",
      "config": {"model": "gpt-4o"}
    }
  }
}
```

Format: `"path/to/file.py:variable_or_function"`

## Store Config (Semantic Search)

```python
class StoreConfig(TypedDict, total=False):
    index: IndexConfig | None

class IndexConfig(TypedDict):
    dims: int           # embedding vector dimensions
    embed: str          # "openai:text-embedding-3-large" or custom path
    fields: list[str]   # JSON fields to extract before embedding
```

## HTTP Config

```python
class HttpConfig(TypedDict, total=False):
    disable_assistants: bool
    disable_threads: bool
    disable_runs: bool
    disable_store: bool
    disable_mcp: bool        # MCP server exposure
    disable_a2a: bool        # Agent-to-Agent protocol
    disable_ui: bool
    disable_meta: bool
    disable_webhooks: bool
    cors: CorsConfig | None
```

## Auth Config

```json
{
  "auth": {
    "path": "./auth.py:my_auth"
  }
}
```

## Example langgraph.json

```json
{
  "python_version": "3.12",
  "dependencies": [".", "langchain_openai"],
  "graphs": {
    "agent": "./src/agent.py:graph"
  },
  "env": ".env",
  "store": {
    "index": {
      "dims": 1536,
      "embed": "openai:text-embedding-3-small",
      "fields": ["content", "summary"]
    }
  },
  "auth": {
    "path": "./auth.py:auth"
  },
  "http": {
    "disable_mcp": false,
    "disable_a2a": false
  },
  "pip_installer": "uv",
  "image_distro": "bookworm"
}
```
