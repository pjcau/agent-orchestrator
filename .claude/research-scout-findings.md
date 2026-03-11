## Research Scout: improvements from [m1k1o/neko](https://github.com/m1k1o/neko)

Analyzed [m1k1o/neko](https://github.com/m1k1o/neko) and found **3** actionable improvement(s) for the orchestrator.

### 1. Session-Scoped Data Isolation

**Component:** `store`
**File:** `src/agent_orchestrator/core/store.py`

Inspired by Neko's persistent browser state per container, this adds session-scoped isolation to the store to prevent data leakage between agent tasks.

```python
def set(self, key: str, value: Any, session_id: str = None) -> None:
        if session_id:
            key = f"{session_id}:{key}"
        self._storage[key] = value
```

**Benefit:** Ensures agent context remains private and persistent across task boundaries.

### 2. Collaborative Room Abstraction

**Component:** `cooperation`
**File:** `src/agent_orchestrator/core/cooperation.py`

Mirroring Neko's multi-user WebRTC rooms, this adds a shared context space where multiple agents can interact with the same state concurrently.

```python
class CollaborativeRoom:
    def __init__(self, room_id: str):
        self.room_id = room_id
        self.state: Dict = {}
        self.listeners: List = []
    def broadcast(self, message: str):
        for listener in self.listeners:
            listener.on_message(message)
```

**Benefit:** Enables real-time multi-agent collaboration on shared tasks.

### 3. Dynamic Agent Instance Factory

**Component:** `orchestrator`
**File:** `src/agent_orchestrator/core/orchestrator.py`

Based on Neko's API-driven room creation, this allows on-demand provisioning of isolated agent instances with specific configurations.

```python
def create_agent_instance(self, config: AgentConfig) -> Agent:
        instance = Agent(config)
        self.instances[config.id] = instance
        return instance
```

**Benefit:** Provides flexible resource allocation and isolation for specific task requirements.
