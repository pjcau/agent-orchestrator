# Cache Strategy — Agent Orchestrator

## Current State

### What Exists (Fully Implemented, Never Wired)

**File**: `src/agent_orchestrator/core/cache.py`

| Component | Status | Description |
|-----------|--------|-------------|
| `InMemoryCache` | Complete | LRU-style cache with TTL support, max entries, eviction |
| `CachePolicy` | Complete | Per-node config: enabled, ttl_seconds, max_entries, custom key fn |
| `CacheEntry` | Complete | Stores key, value, created_at, ttl, hit_count, node_name |
| `CacheStats` | Complete | Tracks hits, misses, evictions, saved_tokens |
| `make_cache_key()` | Complete | SHA256 hash from JSON-serialized args |
| `cached_node()` | Complete | Async decorator for graph node functions |
| `BaseCache` ABC | Complete | Abstract interface (get, put, invalidate, clear, size, stats) |

### What Exists (Instrumentation)

**File**: `src/agent_orchestrator/dashboard/instrument.py`

- `_instrument_cache()` — monkey-patches `InMemoryCache.get()` to emit `CACHE_HIT`, `CACHE_MISS`, and `CACHE_STATS` events to EventBus
- Called at startup via `instrument_all()`

### What Exists (Dashboard UI)

**File**: `src/agent_orchestrator/dashboard/static/index.html` (lines 208-242)

- Cache panel with: Hits, Misses, Evictions, Hit Rate
- Cache bar (visual fill)
- Cache log area
- Header: cache hit rate badge

### What Exists (Event Types)

**File**: `src/agent_orchestrator/dashboard/events.py`

- `CACHE_HIT` — emitted on cache hit
- `CACHE_MISS` — emitted on cache miss
- `CACHE_STATS` — emitted with full stats dict

### Current Integration (Implemented)

The cache is now fully wired into the execution pipeline:

- `llm_nodes.py` — `llm_node()` accepts `cache_policy` parameter; shared `InMemoryCache` instance (`get_llm_cache()`). Skips cache when `temperature > 0`.
- `graphs.py` — graph builders pass `cache_policy` (5 min TTL, 500 entries max) to `llm_node()`.
- `agent_runner.py` — `create_skill_registry()` wires `cache_middleware()` for idempotent skills (`file_read`, `glob_search`). Shared `InMemoryCache` via `get_tool_cache()`. Auto-invalidates on `file_write`.
- `skill.py` — `cache_middleware()` available as composable middleware on `SkillRegistry`.
- Dashboard shows cache hits/misses/rate in real time via EventBus instrumentation.

---

## Integration Points (Where to Wire Cache)

### Point 1: LLM Node Cache (`llm_nodes.py`)

**What**: Cache LLM responses by (model_id, system_prompt, user_prompt) hash.

**Where**: `llm_node()` factory function — wrap the inner `node_func` with `cached_node()`.

**How**:
```python
# In llm_nodes.py
from .cache import InMemoryCache, CachePolicy, cached_node, make_cache_key

# Module-level shared cache
_llm_cache = InMemoryCache(max_entries=500)

def llm_node(provider, system, prompt_key="input", ..., cache_policy=None):
    async def node_func(state):
        # ... existing code ...

    if cache_policy and cache_policy.enabled:
        # Custom key: hash(model_id + system + user_content)
        def cache_key_fn(state):
            user_content = str(state.get(prompt_key, ""))
            return make_cache_key(provider.model_id, system, user_content)

        policy = CachePolicy(
            ttl_seconds=cache_policy.ttl_seconds,
            cache_key_fn=cache_key_fn,
        )
        return cached_node(_llm_cache, policy)(node_func)

    return node_func
```

**Impact**: Every identical prompt → same model skips the LLM call entirely. Useful for:
- Graph replays (same node, same input)
- Repeated classify/route calls
- Team-lead plans with same task description
- Parallel reviews hitting same code

**TTL recommendation**: 300s (5 min) for interactive, 3600s (1h) for batch

**Risks**:
- Stale responses if user expects fresh answer → mitigate with `temperature > 0` bypass
- Memory growth → already handled by LRU eviction (max_entries=500)

---

### Point 2: Graph Builder Cache (`graphs.py`)

**What**: Pass `cache_policy` when creating `llm_node()` in graph builders.

**Where**: `_build_chat_graph()`, `_build_review_graph()`, `_build_chain_graph()`, `_build_parallel_graph()`, `_build_auto_graph()`, `_build_team_graph()`

**How**:
```python
# In graphs.py
from ..core.cache import CachePolicy

# Default policy for graph nodes
_GRAPH_CACHE_POLICY = CachePolicy(ttl_seconds=300, max_entries=500)

def _build_chat_graph(provider, prompt):
    respond = llm_node(
        provider=provider,
        system="You are a helpful AI assistant...",
        prompt_key="input",
        output_key="response",
        cache_policy=_GRAPH_CACHE_POLICY,  # <-- add this
    )
    # ... rest unchanged
```

**When NOT to cache**: `temperature > 0` nodes where randomness is expected.

---

### Point 3: Tool/Skill Cache Middleware (`skill.py`)

**What**: Cache skill results for idempotent tools (file_read, glob_search).

**Where**: Add a `cache_middleware()` to `SkillRegistry`.

**How**:
```python
# New middleware in skill.py
from .cache import InMemoryCache, make_cache_key

def cache_middleware(
    cache: InMemoryCache,
    cacheable_skills: set[str] | None = None,
    ttl_seconds: int = 120,
) -> SkillMiddleware:
    """Cache results of idempotent skills."""
    async def middleware(request, next_fn):
        # Only cache specified skills
        if cacheable_skills and request.skill_name not in cacheable_skills:
            return await next_fn(request)

        key = make_cache_key(request.skill_name, request.params)
        entry = cache.get(key)
        if entry is not None:
            return entry.value

        result = await next_fn(request)
        if result.success:
            cache.put(key, result, ttl_seconds=ttl_seconds, node_name=request.skill_name)
        return result

    return middleware
```

**Cacheable skills** (idempotent, safe to cache):
| Skill | TTL | Reason |
|-------|-----|--------|
| `file_read` | 60s | File content rarely changes during a session |
| `glob_search` | 30s | Directory listing stable during a session |
| `web_reader` | 300s | Web pages don't change frequently |

**NOT cacheable** (side effects):
| Skill | Reason |
|-------|--------|
| `file_write` | Mutates filesystem |
| `shell_exec` | Non-deterministic, side effects |
| `github_skill` | API calls with side effects |
| `webhook_skill` | External notifications |

---

### Point 4: Agent Runner Cache (`agent_runner.py`)

**What**: Wire the skill cache into `create_skill_registry()`.

**Where**: `create_skill_registry()` function.

**How**:
```python
# In agent_runner.py
from ..core.cache import InMemoryCache
from ..core.skill import cache_middleware

# Module-level shared tool cache
_tool_cache = InMemoryCache(max_entries=200)

def create_skill_registry(allowed_commands=None, working_directory=None):
    registry = SkillRegistry()
    # ... register skills ...

    # Add cache middleware (outermost = first registered)
    registry.use(cache_middleware(
        cache=_tool_cache,
        cacheable_skills={"file_read", "glob_search"},
        ttl_seconds=60,
    ))

    return registry
```

**Impact**: When an agent reads the same file twice in one session → cached. Common pattern: agent reads file, modifies, reads again to verify → second read hits cache (need invalidation on write).

**Invalidation**: `file_write` should invalidate cache for the written path:
```python
# After file_write succeeds, invalidate the read cache for that path
key = make_cache_key("file_read", {"file_path": written_path})
_tool_cache.invalidate(key)
```

---

### Point 5: Dashboard API Cache (`app.py`)

**What**: Cache expensive API responses (model listing, pricing, job history).

**Where**: FastAPI endpoint handlers.

**How**: Use `InMemoryCache` or simpler `functools.lru_cache` for:
| Endpoint | TTL | Reason |
|----------|-----|--------|
| `GET /api/models` (Ollama) | 30s | Ollama model list rarely changes |
| `GET /api/models` (OpenRouter) | 300s | Static curated list |
| `GET /api/pricing` | 600s | Pricing updates infrequently |
| `GET /api/jobs` | 5s | Session list, moderate update frequency |

**Not worth caching**: WebSocket events, `/api/usage` (real-time), streaming responses.

---

## Implementation Levels

### Level 1: LLM Response Cache (Sprint-Ready)

**Effort**: ~2h
**Files to modify**: `llm_nodes.py`, `graphs.py`
**Tests to add**: `test_llm_cache.py`

Tasks:
1. Add `cache_policy` parameter to `llm_node()`, `multi_provider_node()`, `chat_node()`
2. Create module-level `InMemoryCache` instance in `llm_nodes.py`
3. Wrap node functions with `cached_node()` when policy is provided
4. Add `cache_policy` to all graph builders in `graphs.py`
5. Custom `cache_key_fn` that hashes (model_id, system, user_content)
6. Skip cache when `temperature > 0`
7. Tests: cache hit, cache miss, TTL expiry, temperature bypass, eviction

**Expected result**: Dashboard cache panel shows hits/misses/rate for LLM calls. Replay of same prompt → instant response.

---

### Level 2: Tool/Skill Cache (Sprint-Ready)

**Effort**: ~2h
**Files to modify**: `skill.py`, `agent_runner.py`
**Tests to add**: `test_skill_cache.py`

Tasks:
1. Add `cache_middleware()` to `skill.py`
2. Wire cache middleware into `create_skill_registry()` in `agent_runner.py`
3. Define cacheable skills whitelist (`file_read`, `glob_search`)
4. Add cache invalidation on `file_write` (invalidate `file_read` for same path)
5. Tests: middleware caching, invalidation on write, non-cacheable skills bypass

**Expected result**: Agents doing repeated file reads get instant results. Visible in cache panel.

---

### Level 3: Semantic Cache (Future — Research Needed)

**Effort**: ~1-2 weeks
**Dependencies**: Embedding model (local or API), vector store

**Concept**: Instead of exact-match SHA256, use embedding similarity to find "close enough" cached responses.

**How it works**:
1. Before LLM call: embed the prompt
2. Search vector store for similar prompts (cosine similarity > threshold)
3. If found: return cached response (adapted if needed)
4. If not: call LLM, embed prompt, store in vector store

**Approaches**:
| Approach | Pros | Cons |
|----------|------|------|
| GPTCache-style (embedding + similarity) | High hit rate, works across paraphrases | Needs embedding model, quality risk |
| LangChain CacheBackedEmbeddings | Standard pattern, well-tested | Adds LangChain dependency |
| Custom (local Ollama embeddings) | No external API cost, fast | Embedding quality varies |

**When to consider**: When LLM costs become significant and many prompts are semantically similar but not identical.

---

### Level 4: Distributed Cache (Future — Scale Only)

**Effort**: ~1 week
**Dependencies**: Redis (already in `docker-compose.prod.yml`)

**What**: Replace `InMemoryCache` with Redis-backed cache for multi-instance deployment.

**Implementation**:
```python
class RedisCache(BaseCache):
    """Redis-backed cache implementing BaseCache interface."""
    def __init__(self, redis_url: str, prefix: str = "cache:"):
        self._redis = redis.from_url(redis_url)
        self._prefix = prefix

    def get(self, key: str) -> CacheEntry | None:
        data = self._redis.get(f"{self._prefix}{key}")
        # ... deserialize ...

    def put(self, key, value, ttl_seconds=3600, node_name=""):
        self._redis.setex(f"{self._prefix}{key}", ttl_seconds, serialize(value))
```

**When to consider**: When running multiple dashboard instances behind a load balancer.

---

## External Framework Comparison

### LangGraph
- **No built-in cache**. Only has `checkpointing` (state persistence per thread).
- Cache is left to the user: wrap tools with `functools.lru_cache` or use LangChain's `InMemoryCache`.
- Relevant pattern: `ToolNode` wraps tool execution — similar to our `SkillRegistry.execute()`.

### CrewAI
- **Tool-level caching** built-in: `Tool(cache_function=my_filter)` where `my_filter(args, result)` returns bool.
- Smart: lets you control WHICH results to cache based on content quality.
- Our equivalent: `cache_middleware` with a custom filter function.

### AutoGen
- **No explicit cache layer**. Relies on conversation history replay.
- Token optimization via message compression, not caching.

### GPTCache
- Dedicated caching library for LLM calls.
- Embedding-based similarity search (not exact match).
- Eviction: LRU + TTL + embedding distance.
- Overkill for our current needs, but good reference for Level 3.

---

## Metrics & Observability

Once cache is wired, the dashboard will show:

| Metric | Source | Where Displayed |
|--------|--------|-----------------|
| Cache Hits | `CacheStats.hits` via EventBus | Cache panel + header badge |
| Cache Misses | `CacheStats.misses` via EventBus | Cache panel |
| Hit Rate | `hits / (hits + misses)` | Cache panel bar + header |
| Evictions | `CacheStats.evictions` | Cache panel |
| Saved Tokens | `CacheStats.total_saved_tokens` | Cache panel (needs wiring) |
| Cache Log | `CACHE_HIT` / `CACHE_MISS` events | Cache log area |

**To enable `total_saved_tokens`**: When a cache hit occurs on an LLM node, estimate saved tokens from the cached response's `_usage` data and increment `CacheStats.total_saved_tokens`.

---

## Sprint Task Breakdown

### Sprint A: Core Cache Activation (Level 1 + Level 2)

| # | Task | Estimate | Dependencies |
|---|------|----------|--------------|
| A1 | Add `cache_policy` param to `llm_node()` in `llm_nodes.py` | 30min | — |
| A2 | Create shared `InMemoryCache` instance + wire `cached_node` | 30min | A1 |
| A3 | Add `cache_policy` to all graph builders in `graphs.py` | 20min | A1 |
| A4 | Skip cache when `temperature > 0` | 10min | A2 |
| A5 | Write `test_llm_cache.py` (hit, miss, TTL, temp bypass) | 30min | A1-A4 |
| A6 | Add `cache_middleware()` to `skill.py` | 30min | — |
| A7 | Wire cache middleware into `agent_runner.create_skill_registry()` | 20min | A6 |
| A8 | Add `file_write` → invalidate `file_read` cache logic | 20min | A7 |
| A9 | Write `test_skill_cache.py` (middleware, invalidation) | 30min | A6-A8 |
| A10 | Verify dashboard cache panel shows real data | 20min | All above |

**Total**: ~4h

### Sprint B: Cache Enhancements

| # | Task | Estimate | Dependencies |
|---|------|----------|--------------|
| B1 | Track `total_saved_tokens` on cache hits | 30min | A2 |
| B2 | Add cache clear button to dashboard UI | 20min | A10 |
| B3 | Add per-node cache stats (which nodes hit cache most) | 30min | A10 |
| B4 | API endpoint cache for `/api/models`, `/api/pricing` | 30min | — |
| B5 | Cache size/memory display in dashboard | 20min | A10 |

**Total**: ~2.5h

---

## Configuration

Proposed config structure (via `ConfigManager` or env vars):

```json
{
  "cache": {
    "llm": {
      "enabled": true,
      "max_entries": 500,
      "ttl_seconds": 300,
      "skip_on_temperature": true
    },
    "tools": {
      "enabled": true,
      "max_entries": 200,
      "ttl_seconds": 60,
      "cacheable_skills": ["file_read", "glob_search"]
    }
  }
}
```

Environment variables override:
- `CACHE_LLM_ENABLED=true`
- `CACHE_LLM_TTL=300`
- `CACHE_TOOLS_ENABLED=true`
- `CACHE_TOOLS_TTL=60`

---

## Decision Log

| Decision | Rationale |
|----------|-----------|
| InMemoryCache first, not Redis | Single-instance deployment, no serialization overhead |
| SHA256 exact-match, not semantic | Simpler, deterministic, no embedding dependency |
| Cache at node level, not provider level | Preserves state delta semantics, works with `cached_node` decorator |
| Whitelist cacheable skills, not blacklist | Safer: new skills default to uncached until explicitly opted in |
| Module-level cache instances | Shared across all graph executions within the process |
| Skip cache on `temperature > 0` | Non-deterministic outputs shouldn't be cached |
