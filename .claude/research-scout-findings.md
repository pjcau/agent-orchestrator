## Research Scout: improvements from [m1k1o/neko](https://github.com/m1k1o/neko)

Analyzed [m1k1o/neko](https://github.com/m1k1o/neko) and found **3** actionable improvement(s) for the orchestrator.

### 1. Real-time Execution Streaming

**Component:** `graph`
**File:** `src/agent_orchestrator/core/graph.py`

Inspired by Neko's WebRTC state synchronization, this adds immediate event broadcasting for graph nodes to the dashboard. It allows the UI to reflect agent state changes instantly rather than polling logs. This mirrors how Neko streams desktop frames to multiple clients in real-time.

```python
def stream_events(self, channel: Channel):
    for event in self.run():
        channel.put(event)
        yield event
```

**Benefit:** Enables low-latency dashboard updates similar to Neko's video stream for better monitoring.

### 2. Ephemeral Session Storage

**Component:** `store`
**File:** `src/agent_orchestrator/core/store.py`

Inspired by Neko's throwaway browser mode, this adds volatile storage that clears automatically on disconnect. It ensures no sensitive state persists on the host after the session ends, mitigating OS fingerprinting risks. This mimics Neko's container isolation where cookies are not transferred to the host browser.

```python
class Store:
    def __init__(self, volatile=False):
        self.volatile = volatile
        self.data = {}
    def save(self, key, value):
        self.data[key] = value
        if self.volatile: self._schedule_cleanup()
```

**Benefit:** Prevents sensitive data leakage between sessions like Neko's isolated containers.

### 3. Multi-User Collaboration Channel

**Component:** `cooperation`
**File:** `src/agent_orchestrator/core/cooperation.py`

Inspired by Neko's multi-user control feature, this allows multiple humans to interact with one agent session simultaneously. It enables collaborative debugging and watch parties where participants can inject inputs into the agent flow. This extends Neko's shared session concept from screen control to agent decision-making.

```python
class HumanCollaborator:
    def __init__(self, channel: Topic):
        self.channel = channel
    def input(self, message):
        self.channel.publish(message)
```

**Benefit:** Supports collaborative debugging and watch parties similar to Neko's shared sessions.
