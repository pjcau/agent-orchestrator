# 10 - LLM Integration

## Model Factory

`create_chat_model()` in `deerflow/models/factory.py`:

```python
def create_chat_model(name=None, thinking_enabled=False, **kwargs):
    config = get_app_config()
    model_config = config.get_model_config(name)
    model_class = resolve_class(model_config.use, BaseChatModel)
    # ... build settings, handle thinking mode
    return model_class(**kwargs, **model_settings_from_config)
```

## Provider Support via LangChain

| Provider | `use` path |
|----------|-----------|
| OpenAI | `langchain_openai:ChatOpenAI` |
| Anthropic | `langchain_anthropic:ChatAnthropic` |
| DeepSeek | `deerflow.models.patched_deepseek:PatchedChatDeepSeek` |
| Google Gemini | `langchain_google_genai:ChatGoogleGenerativeAI` |
| OpenRouter | `langchain_openai:ChatOpenAI` + `base_url` |
| Volcengine | `deerflow.models.patched_deepseek:PatchedChatDeepSeek` |
| Novita AI | `langchain_openai:ChatOpenAI` + `base_url` |
| MiniMax | `langchain_openai:ChatOpenAI` + `base_url` |

## Thinking Mode Support

Models can declare `supports_thinking: true` in config. When enabled:

```yaml
models:
  - name: claude-3-5-sonnet
    supports_thinking: true
    when_thinking_enabled:
      thinking:
        type: enabled
```

The factory handles two patterns:
1. **Native langchain_anthropic**: `thinking: {type: enabled}` as constructor param
2. **OpenAI-compatible gateway**: `extra_body: {thinking: {type: enabled}}`

When thinking is disabled for a thinking-capable model, factory explicitly sets:
```python
kwargs.update({"thinking": {"type": "disabled"}})
# or for OpenAI-compatible:
kwargs.update({"extra_body": {"thinking": {"type": "disabled"}}})
```

## Vision Support

Models declare `supports_vision: true`:
- `view_image` tool only added if model supports vision
- `ViewImageMiddleware` only added if model supports vision
- Images converted to base64 and injected into state

## LangSmith Tracing

Automatically attached if enabled:
```python
if is_tracing_enabled():
    tracer = LangChainTracer(project_name=tracing_config.project)
    model_instance.callbacks = [*existing_callbacks, tracer]
```

## Environment Variable Resolution

Config values starting with `$` are resolved:
```yaml
api_key: $OPENAI_API_KEY  # Resolved at runtime from env
```

## Key Difference from Our Approach

DeerFlow uses LangChain's model abstraction exclusively. We have our own `Provider` abstraction that wraps different LLM APIs:

| Aspect | DeerFlow | Our Orchestrator |
|--------|----------|-----------------|
| Abstraction | LangChain BaseChatModel | Custom Provider interface |
| Config | YAML with `use` path | Python code |
| Resolution | Reflection (resolve_class) | Direct import |
| Vendor Lock | LangChain ecosystem | Provider-agnostic |
| Extensibility | Any LangChain model | Custom provider impl |
