# 04 - Agent System

## Lead Agent

The central agent in DeerFlow. Created via `make_lead_agent(config: RunnableConfig)` registered in `langgraph.json`.

### Entry Point

```python
# langgraph.json
{
  "agent": {
    "type": "agent",
    "path": "deerflow.agents:make_lead_agent"
  }
}
```

### Creation Flow

```
make_lead_agent(config)
  ├── _resolve_model_name()        # Validate/fallback model
  ├── load_agent_config(name)      # Load custom agent YAML
  ├── create_chat_model()          # Instantiate LLM
  ├── get_available_tools()        # Assemble tool set
  ├── apply_prompt_template()      # Build system prompt
  ├── _build_middlewares()         # Build 11-middleware chain
  └── create_agent()               # LangGraph agent creation
```

### Runtime Configuration

Via `config.configurable`:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `thinking_enabled` | bool | true | Extended thinking mode |
| `reasoning_effort` | str | null | Reasoning effort level |
| `model_name` | str | null | Override model selection |
| `is_plan_mode` | bool | false | Enable TodoList middleware |
| `subagent_enabled` | bool | false | Enable task delegation |
| `max_concurrent_subagents` | int | 3 | Parallel subagent limit |
| `is_bootstrap` | bool | false | Bootstrap agent mode |
| `agent_name` | str | null | Custom agent identifier |

### Model Resolution Priority

1. Explicit `model_name` from request
2. Custom agent config model
3. First model in `config.yaml`

### Key Design Decisions

1. **Single lead agent, not multi-agent graph**: Unlike our orchestrator's 24-agent system, DeerFlow uses ONE lead agent that delegates via `task` tool calls. The lead agent is the router, planner, and synthesizer.

2. **LangGraph's `create_agent()`**: Uses LangGraph's built-in agent factory, not a custom graph. This gives them: ReAct loop, tool calling, state management, streaming — all out of the box.

3. **Middleware over graph nodes**: Instead of separate graph nodes for each concern (memory, title, summarization), they use a middleware chain that wraps the agent. This is more composable but less visible in the graph.

4. **Dynamic tool assembly**: Tools are assembled at agent creation time, not statically defined. MCP tools, skills, vision tools, subagent tools — all conditionally included.

### Agent Config Files

Custom agents can be defined in YAML with:
- Custom system prompt (SOUL.md)
- Model override
- Tool group restrictions
- Per-agent memory storage
