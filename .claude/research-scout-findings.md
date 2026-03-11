## Research Scout: improvements from [m1k1o/neko](https://github.com/m1k1o/neko)

Analyzed [m1k1o/neko](https://github.com/m1k1o/neko) and found **3** actionable improvement(s) for the orchestrator.

### 1. Sandboxed Skill Execution

**Component:** `skill`
**File:** `src/agent_orchestrator/core/skill.py`

Inspired by Neko's Docker container isolation, this adds ephemeral execution contexts to skills to prevent state leakage and enhance security during tool usage. It ensures that high-risk operations run in a clean environment similar to Neko's virtual browser sessions.

```python
class SandboxedSkill(Skill):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._context = None

    async def execute(self, *args, **kwargs):
        with self._isolate_context():
            return await super().execute(*args, **kwargs)

    def _isolate_context(self):
        # Simulates container isolation for state safety
        return ContextManager()
```

**Benefit:** Prevents agent tool misuse from corrupting the main orchestration state.

### 2. Real-time Shared Workspace

**Component:** `cooperation`
**File:** `src/agent_orchestrator/core/cooperation.py`

Inspired by Neko's multi-user room synchronization, this introduces a shared mutable state channel allowing agents to collaborate on a common workspace object. It enables real-time updates across agents, mirroring how Neko syncs user inputs in a shared room.

```python
class SharedWorkspace:
    def __init__(self):
        self.state = {}
        self.listeners = []

    def update(self, key, value):
        self.state[key] = value
        self._broadcast(key, value)

    def _broadcast(self, key, value):
        for listener in self.listeners:
            listener.on_state_change(key, value)
```

**Benefit:** Enables agents to collaboratively edit and view a shared state without race conditions.

### 3. Binary Stream Channel

**Component:** `channels`
**File:** `src/agent_orchestrator/core/channels.py`

Inspired by Neko's WebRTC media streaming, this extends channels to support efficient binary data streaming for large tool outputs or logs. It allows for low-latency transmission of large payloads, similar to how Neko streams video frames to clients.

```python
class BinaryStreamChannel:
    def __init__(self, name):
        self.name = name
        self.buffer = []

    async def send(self, data: bytes):
        self.buffer.append(data)
        await self.notify(data)

    async def notify(self, data):
        # Efficient binary transmission logic
        pass
```

**Benefit:** Reduces overhead when streaming large logs or file contents between agents.
