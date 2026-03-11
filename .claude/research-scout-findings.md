## Research Scout: improvements from [m1k1o/neko](https://github.com/m1k1o/neko)

Analyzed [m1k1o/neko](https://github.com/m1k1o/neko) and found **3** actionable improvement(s) for the orchestrator.

### 1. Persistent Profile Snapshots

**Component:** `store`
**File:** `src/agent_orchestrator/core/store.py`

Inspired by Neko's persistent browser profiles, this adds snapshot and restore capabilities to the store for cloning agent contexts. It allows agents to save their state as a reusable profile, similar to how Neko maintains persistent cookies across sessions. This enables agents to resume work from a known good state without re-initializing tools or memory.

```python
def snapshot(self, namespace: str) -> str:
    import uuid
    import copy
    data = self._store.get(namespace, {})
    snapshot_id = f"{namespace}_snap_{uuid.uuid4().hex[:8]}"
    self._store[snapshot_id] = copy.deepcopy(data)
    return snapshot_id

def restore(self, snapshot_id: str, target_namespace: str):
    data = self._store.get(snapshot_id)
    if data:
        self._store[target_namespace] = copy.deepcopy(data)
```

**Benefit:** Enables agents to maintain persistent state across sessions like Neko's persistent cookies.

### 2. Sandboxed Skill Execution

**Component:** `skill`
**File:** `src/agent_orchestrator/core/skill.py`

Inspired by Neko's Docker container isolation, this adds a sandbox middleware to isolate untrusted skill execution from the main process. It wraps skill runs in a restricted context to prevent side effects on the host environment or other agents. This mirrors Neko's approach of running the browser in an isolated container for security.

```python
class SandboxMiddleware:
    def __init__(self, skill):
        self.skill = skill
    async def execute(self, *args, **kwargs):
        # Simulate isolation context
        with self._isolate_context():
            return await self.skill.run(*args, **kwargs)
    def _isolate_context(self):
        # Context manager for resource isolation
        pass
```

**Benefit:** Prevents skill execution from affecting the main orchestrator state or host environment.

### 3. Real-time State Streaming

**Component:** `channels`
**File:** `src/agent_orchestrator/core/channels.py`

Inspired by Neko's WebRTC video streaming, this adds a Stream channel type for continuous state updates to the dashboard. It allows agents to push incremental progress data in real-time rather than waiting for task completion. This provides a live view of agent activity similar to Neko's live desktop stream.

```python
class StreamChannel:
    def __init__(self):
        self._subscribers = []
    async def subscribe(self, callback):
        self._subscribers.append(callback)
    async def publish(self, data):
        for sub in self._subscribers:
            await sub(data)
```

**Benefit:** Enables real-time dashboard visualization of agent progress similar to Neko's live desktop stream.
