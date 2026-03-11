## Research Scout: improvements from [m1k1o/neko](https://github.com/m1k1o/neko)

Analyzed [m1k1o/neko](https://github.com/m1k1o/neko) and found **3** actionable improvement(s) for the orchestrator.

### 1. Continuous State Recording Stream

**Component:** `checkpoint`
**File:** `src/agent_orchestrator/core/checkpoint.py`

Inspired by Neko's RTMP session recording feature, this adds a continuous state stream capture to checkpointers for full audit trails. It allows the system to replay agent decisions exactly as they occurred. This mirrors Neko's ability to save and review browser sessions.

```python
class RecordingCheckpoint(Checkpointer):
    def __init__(self, base: Checkpointer, stream_url: str):
        self.base = base
        self.stream_url = stream_url

    async def save_state(self, state: dict):
        await self.base.save_state(state)
        await self._broadcast_state(state)

    async def _broadcast_state(self, state: dict):
        # Simulate WebRTC-like streaming of state
        pass
```

**Benefit:** Enables full replay and debugging of agent decision paths.

### 2. Real-time State Subscription Channels

**Component:** `channels`
**File:** `src/agent_orchestrator/core/channels.py`

Inspired by Neko's WebRTC multi-user streaming capabilities, this allows agents to subscribe to live state updates of other agents. It enables real-time collaboration where agents can monitor each other's execution context. This mirrors Neko's feature of multiple users viewing the same desktop stream.

```python
class StreamChannel(Channel):
    def __init__(self, name: str):
        self.name = name
        self.subscribers: List[asyncio.Queue] = []

    async def publish(self, state: dict):
        for q in self.subscribers:
            await q.put(state)

    async def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue()
        self.subscribers.append(q)
        return q
```

**Benefit:** Facilitates collaborative debugging and real-time monitoring of agent workflows.

### 3. Containerized Skill Sandboxing

**Component:** `skill`
**File:** `src/agent_orchestrator/core/skill.py`

Inspired by Neko's Docker container isolation, this wraps skill execution in isolated contexts to prevent state leakage between agents. It ensures that tool execution does not pollute the global environment or other agent sessions. This mirrors Neko's approach of running the browser in a secure, isolated container.

```python
class SandboxedSkill(Skill):
    def __init__(self, skill: Skill, isolation_context: dict):
        self.skill = skill
        self.context = isolation_context

    async def execute(self, *args, **kwargs):
        with self.context:
            return await self.skill.execute(*args, **kwargs)
```

**Benefit:** Enhances security by preventing cross-agent data contamination during tool execution.
