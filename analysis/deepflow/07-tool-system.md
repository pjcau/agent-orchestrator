# 07 - Tool System

## Tool Assembly

Tools are dynamically assembled via `get_available_tools()`:

```python
def get_available_tools(
    groups: list[str] | None = None,       # Filter by tool group
    include_mcp: bool = True,               # Include MCP tools
    model_name: str | None = None,          # For vision tools
    subagent_enabled: bool = False,         # Include task tool
) -> list[BaseTool]:
```

## Tool Sources

### 1. Config-Defined Tools (config.yaml)

```yaml
tools:
  - name: web_search
    group: web
    use: deerflow.community.tavily.tools:web_search_tool
    max_results: 5
  - name: bash
    group: bash
    use: deerflow.sandbox.tools:bash_tool
```

Resolved via reflection: `resolve_variable(tool.use, BaseTool)`

### 2. Built-in Tools

| Tool | Purpose |
|------|---------|
| `present_files` | Make output files visible to user |
| `ask_clarification` | Request clarification (interrupts) |
| `view_image` | Read image as base64 (vision models only) |
| `task` | Delegate to sub-agent (if subagent_enabled) |

### 3. MCP Tools

- From `extensions_config.json`
- Lazy initialized via `get_cached_mcp_tools()`
- Cache invalidation via file mtime comparison
- OAuth support for HTTP/SSE servers

### 4. Sandbox Tools

| Tool | Description |
|------|-------------|
| `bash` | Execute commands with path translation |
| `ls` | Directory listing (tree, max 2 levels) |
| `read_file` | Read with optional line range |
| `write_file` | Write/append, creates directories |
| `str_replace` | Substring replacement |

## Tool Groups

```yaml
tool_groups:
  - name: web
  - name: file:read
  - name: file:write
  - name: bash
```

Groups allow restricting tools per agent or context.

## Community Tools

| Package | Tools |
|---------|-------|
| `tavily/` | web_search (5 results), web_fetch (4KB limit) |
| `jina_ai/` | web_fetch via Jina reader + readability |
| `firecrawl/` | Web scraping via Firecrawl API |
| `image_search/` | DuckDuckGo image search |
| `infoquest/` | BytePlus search/crawl |

## Reflection System

Tools are resolved dynamically via `deerflow.reflection`:
- `resolve_variable(path)` — import module, return variable
- `resolve_class(path, base_class)` — import and validate class
- Path format: `package.module:variable_name`

This enables hot-swappable tools without code changes — just config YAML.

## Key Design Insight

DeerFlow's tool description docstrings follow a consistent pattern:
```python
@tool("bash", parse_docstring=True)
def bash_tool(runtime: ToolRuntime, description: str, command: str) -> str:
    """Execute a bash command in a Linux environment.
    Args:
        description: Explain why you are running this command. ALWAYS PROVIDE FIRST.
        command: The bash command to execute.
    """
```

The `description` parameter on every tool forces the LLM to explain WHY it's calling the tool — improving trace readability and debugging.
