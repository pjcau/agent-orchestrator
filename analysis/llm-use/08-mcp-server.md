# 08 - MCP Server

## Overview
llm-use exposes its orchestrator as an MCP (Model Context Protocol) server via PolyMCP, allowing external AI tools to invoke it as a tool.

## Implementation (lines 1222-1243)

The MCP server is remarkably minimal:
```python
def run_mcp_server(orch: Orchestrator, host: str, port: int):
    def exec_task(task: str) -> Dict[str, Any]:
        return orch.execute(task)

    def stats() -> Dict[str, Any]:
        return stats_snapshot()

    def scrape_url(url: str) -> Dict[str, str]:
        content = simple_scrape(url, cache=cache, backend=orch.scrape_backend)
        return {"url": url, "content": content}

    app = expose_tools_http([exec_task, stats, scrape_url])
    uvicorn.run(app, host=host, port=port)
```

## Exposed Tools
| Tool | Input | Output |
|------|-------|--------|
| `exec_task` | `task: str` | Full execution result (output, mode, cost, duration) |
| `stats` | — | Session statistics snapshot |
| `scrape_url` | `url: str` | Scraped text content |

## PolyMCP Integration
- Uses `polymcp.expose_tools_http()` to auto-generate MCP tool schemas from function signatures
- Runs on uvicorn (ASGI)
- Default: `127.0.0.1:8000`

## Key Patterns
- Function-to-tool mapping: plain Python functions become MCP tools automatically
- Minimal boilerplate: 3 functions, 1 line to expose them
- Reuses existing orchestrator instance (no duplication)

## Relevance to Our Project
Our MCP integration (`mcp_server.py`) is more comprehensive with `MCPServerRegistry`, explicit tool/resource registration, and `Orchestrator.register_mcp_tools()`. Their approach using PolyMCP's auto-exposure is much simpler. We could consider PolyMCP for quick MCP server setup, though our explicit registry gives more control over tool schemas and descriptions.
