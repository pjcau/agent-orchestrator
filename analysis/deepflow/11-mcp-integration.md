# 11 - MCP Integration

## Architecture

DeerFlow uses `langchain-mcp-adapters` for multi-server MCP management.

```
extensions_config.json
  │
  └── mcpServers: {
        "github": { type: "stdio", command: "npx", ... },
        "api-server": { type: "http", url: "...", oauth: {...} }
      }
      │
      ▼
  MultiServerMCPClient
      │
      ├── stdio transport
      ├── SSE transport
      └── HTTP transport
```

## Configuration (`extensions_config.json`)

```json
{
  "mcpServers": {
    "github": {
      "enabled": true,
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_TOKEN": "$GITHUB_TOKEN"}
    }
  },
  "skills": {
    "deep-research": {"enabled": true}
  }
}
```

## OAuth Support

For HTTP/SSE MCP servers, OAuth token flows are supported:
- `client_credentials` grant type
- `refresh_token` grant type
- Automatic token refresh
- Authorization header injection

```python
async def get_initial_oauth_headers(extensions_config):
    # Fetch tokens for each server with OAuth config
    # Inject Authorization headers into server connection

oauth_interceptor = build_oauth_tool_interceptor(extensions_config)
# Refreshes tokens before each tool call if needed
```

## Caching

```python
# In deerflow/mcp/cache.py
def get_cached_mcp_tools():
    # Check extensions_config.json mtime
    # If changed: reinitialize MCP client
    # Return cached tools
```

- File mtime-based cache invalidation
- Detects config changes from Gateway API (separate process)
- Lazy initialization on first use

## Runtime Updates

1. Gateway API `PUT /api/mcp/config` saves to `extensions_config.json`
2. LangGraph Server detects mtime change on next tool load
3. MCP client reinitializes with updated config
4. Next agent run uses new tools

## Key Insight

DeerFlow's MCP integration is more mature than ours:
- OAuth token flows
- Multi-server management with interceptors
- File-based config sharing between processes
- Automatic cache invalidation

Our MCPServerRegistry is simpler — it bridges agents/skills as MCP tools for external consumption, but doesn't consume external MCP servers with the same sophistication.
