# 03 - Provider System

## Overview
llm-use implements a simple provider abstraction: each provider class exposes an identical `call()` method that returns `(text, tokens_in, tokens_out)`.

## Provider Interface (Implicit)
There is no formal base class or protocol. All providers share this signature:
```python
def call(self, model: str, prompt: str, max_tokens: int,
         temperature: float, timeout: int) -> Tuple[str, int, int]:
```
Returns: `(response_text, input_tokens, output_tokens)`

## Provider Implementations

### OllamaProvider (lines 219-233)
- Calls `POST /api/generate` with `stream: False`
- Token estimation: `len(prompt.split()) * 1.3` (word count heuristic)
- Base URL configurable (default: `http://localhost:11434`)

### LlamaCppProvider (lines 235-256)
- OpenAI-compatible: calls `POST /v1/chat/completions`
- Uses real token counts from `usage` field when available
- Falls back to word count heuristic if `usage` is missing

### AnthropicProvider (lines 258-267)
- Wraps `anthropic.Anthropic` SDK
- Uses `messages.create()` API
- Token counts from `r.usage.input_tokens` / `r.usage.output_tokens`

### OpenAIProvider (lines 269-278)
- Wraps `openai.OpenAI` SDK
- Uses `chat.completions.create()` API
- Token counts from `r.usage.prompt_tokens` / `r.usage.completion_tokens`

## API Class (Provider Registry)
The `API` class (lines 377-426) serves as both provider registry and call dispatcher:

```python
class API:
    def __init__(self, anthropic_key, openai_key, ollama_url,
                 llama_cpp_url, cache, enable_cache):
        self.providers = {}
        # Initialize each provider, silently skip failures
        if anthropic_key:
            self.providers["anthropic"] = AnthropicProvider(anthropic_key)
        ...
```

Key behaviors:
- **Silent failure**: `try/except: pass` on provider init — fails silently
- **Cache integration**: LLM responses cached in SQLite before returning
- **Retry logic**: 3 retries with exponential backoff (`2 ** attempt` seconds)
- **Cost calculation**: `(tokens_in / 1M) * cost_in + (tokens_out / 1M) * cost_out`

## Cost Model
Costs are defined per model in `DEFAULT_MODELS` dict:
```python
DEFAULT_MODELS = {
    "claude-3-7-sonnet-20250219": {"provider": "anthropic", "cost_in": 3.0, "cost_out": 15.0},
    "gpt-4o-mini": {"provider": "openai", "cost_in": 0.15, "cost_out": 0.60},
    "llama3.1:70b": {"provider": "ollama", "cost_in": 0.0, "cost_out": 0.0},
}
```
Prices are per million tokens. Custom models default to $0.

## Key Patterns
- Duck typing (no formal interface/protocol)
- Silent failure on provider initialization
- Integrated caching at the API layer
- Exponential backoff retry

## Relevance to Our Project
Our `Provider` abstraction is much more formal (abstract base class with health monitoring, tracing, rate limiting). The simplicity here is appealing for quick prototyping, but the duck typing and silent failures would be problematic at scale. Their integrated retry in the API layer is simpler than our separate `rate_limiter.py` + middleware approach.
