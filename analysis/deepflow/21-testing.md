# 21 - Testing

## Test Suite

Located in `backend/tests/` with ~40 test files.

## Test Categories

### Architecture Tests
- `test_harness_boundary.py` — Ensures `deerflow/` never imports from `app.*`
- `test_config_version.py` — Config schema versioning

### Agent Tests
- `test_lead_agent_model_resolution.py` — Model fallback logic
- `test_custom_agent.py` — Custom agent creation
- `test_loop_detection_middleware.py` — Loop detection safety

### Sandbox Tests
- `test_sandbox_tools_security.py` — Path traversal prevention
- `test_docker_sandbox_mode_detection.py` — Mode detection from config
- `test_provisioner_kubeconfig.py` — K8s config handling

### Tool Tests
- `test_present_file_tool_core_logic.py` — File presentation
- `test_task_tool_core_logic.py` — Sub-agent delegation
- `test_tool_error_handling_middleware.py` — Error handling

### MCP Tests
- `test_mcp_client_config.py` — MCP configuration
- `test_mcp_oauth.py` — OAuth token flows

### Memory Tests
- `test_memory_prompt_injection.py` — Memory injection safety
- `test_memory_upload_filtering.py` — Upload event filtering

### Client Tests
- `test_client.py` — 77 unit tests including `TestGatewayConformance`
- `test_client_live.py` — Live integration tests

### Other
- `test_skills_loader.py` — Skill discovery
- `test_skills_router.py` — Skill API routing
- `test_channels.py` — IM channels
- `test_readability.py` — HTML readability extraction
- `test_title_generation.py` — Auto-title

## Gateway Conformance Tests

Validates embedded client matches HTTP API:
```python
class TestGatewayConformance:
    # Every dict-returning client method parsed through
    # corresponding Gateway Pydantic response model
    # If Gateway adds required field, Pydantic raises ValidationError
```

Covered: ModelsListResponse, ModelResponse, SkillsListResponse, etc.

## CI

`.github/workflows/backend-unit-tests.yml`:
- Runs on every PR
- Python version: 3.12+
- Tool: `uv run pytest`

## Test Infrastructure

```python
# conftest.py
# sys.modules mocking for circular import prevention
# e.g., mock deerflow.subagents.executor
```

## Key Observations

1. **No integration tests with real LLMs** — only unit tests and mock-based tests
2. **Harness boundary enforcement** — architectural test in CI
3. **Gateway conformance** — ensures embedded client stays in sync
4. **Security-focused tests** — path traversal, prompt injection
5. **No frontend tests** — no jest/vitest for the Next.js app
