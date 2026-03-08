# Research Scout Findings: Neko Virtual Browser as Agent Output Sandbox

**Date:** 2026-03-08
**Scout:** research-scout (opus)
**Applicability Score:** 0.825 / 1.0

---

## What is Neko?

[m1k1o/neko](https://github.com/m1k1o/neko) is a self-hosted virtual browser running inside Docker,
streaming its display over WebRTC to any connected viewer. Key properties:

- **WebRTC streaming** — sub-300 ms latency, no plugins required in the viewer's browser
- **Multi-user sessions** — multiple viewers can watch the same session simultaneously
- **Pre-built images** — `m1k1o/neko:chromium`, `m1k1o/neko:firefox`, `m1k1o/neko:brave`, and more
- **REST API (OpenAPI 3.0)** — programmatic control: navigate, click, type, screenshot
- **Active project** — 7 000+ GitHub stars, maintained, well-documented
- **Fully self-hosted** — no external dependencies, no telemetry, open-source (Apache-2.0)

---

## Why It Matters for Agent Orchestrator

### Roadmap fit

Phase 1 of the Agent Orchestrator roadmap includes **"Agent Output Sandbox (Preview & Test)"**.
Neko directly implements this: an agent produces HTML/CSS/JS output, Neko loads it in a real
browser, and the result is streamed back to the dashboard for human review.

### Problems Neko solves

| Problem | Without Neko | With Neko |
|---|---|---|
| Preview agent-generated HTML | Must open a file locally | Streamed to dashboard as a live view |
| Browser automation for agents | Headless Selenium/Playwright outside Docker | REST API calls to a container-native browser |
| Visual regression | Custom screenshot tooling required | `GET /api/screen` returns PNG, diff in CI |
| Human-in-the-loop approval | Text-only output shown in chat | Real browser view with Approve / Reject buttons |
| Reproducible browser environment | Depends on local machine | Identical Docker image every run |

---

## Architecture

### High-level flow

```
Agent produces output
  -> Saves HTML/JS/CSS to shared Docker volume (/tmp/agent_output)
  -> Dashboard calls Neko REST API: navigate to file:///tmp/agent_output/index.html
  -> Neko renders page in Chromium
  -> WebRTC stream is embedded in dashboard as an <iframe> or direct WebRTC viewer
  -> Human operator sees the live render and clicks [Approve] or [Reject]
  -> If Rejected: feedback text is sent back to the agent as a new task
  -> If Approved: output is promoted (copy to final destination, trigger deploy skill)
```

### Component diagram (ASCII)

```
┌──────────────────────────────────────────────────────────────┐
│              Agent Orchestrator Dashboard                    │
│              (FastAPI + WebSocket, port 5005)                │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Neko viewer panel (WebRTC stream)                  │    │
│  │                                                     │    │
│  │   ┌───────────────────────────────────────────┐    │    │
│  │   │  Live Chromium render of agent output     │    │    │
│  │   │  (iframe pointing to Neko WebRTC player)  │    │    │
│  │   └───────────────────────────────────────────┘    │    │
│  │                                                     │    │
│  │   [Approve]                          [Reject]       │    │
│  │   (promotes output)           (sends feedback text  │    │
│  │                                back to agent)       │    │
│  └─────────────────────────────────────────────────────┘    │
└──────────────────────────┬───────────────────────────────────┘
                           │ REST API calls (navigate/screenshot)
                           │
               ┌───────────v──────────────┐
               │     Neko Container       │
               │     image: chromium      │
               │     REST API  :8080      │
               │     WebRTC stream        │
               │                          │
               │   /tmp/agent_output/  <──┼── shared Docker volume
               │     index.html           │
               │     style.css            │
               │     app.js               │
               └──────────────────────────┘
                           ^
                           │ writes output files
               ┌───────────┴──────────────┐
               │   Agent container        │
               │   (frontend / backend)   │
               │   generates HTML/CSS/JS  │
               └──────────────────────────┘
```

---

## Proposed Docker Compose Addition

Add the following service to `docker-compose.yml`. No existing services need modification.

```yaml
  neko:
    image: m1k1o/neko:chromium
    ports:
      - "8080:8080"
    environment:
      NEKO_SCREEN_WIDTH: 1920
      NEKO_SCREEN_HEIGHT: 1080
      NEKO_CAPTURE_VIDEO_CODEC: vp8
      NEKO_VIDEO_BITRATE: 3000
      NEKO_SCREEN_FPS: 25
      NEKO_ADMIN_PASSWORD: admin
      NEKO_USER_PASSWORD: user
    volumes:
      - agent_output:/tmp/agent_output
    shm_size: 2gb
    restart: unless-stopped
```

A named volume `agent_output` must also be declared in the top-level `volumes:` block:

```yaml
volumes:
  agent_output:
```

The same volume is mounted into agent containers that produce file output, allowing Neko to access
the generated files without any network transfer.

---

## Proposed Skill: `/web-interaction`

**File:** `src/agent_orchestrator/skills/web_interaction.py`

This skill wraps the Neko REST API and exposes browser control as an agent capability.

```python
# Concept: src/agent_orchestrator/skills/web_interaction.py

from dataclasses import dataclass
from typing import Optional
import httpx


NEKO_BASE_URL = "http://neko:8080"  # service name from docker-compose


@dataclass
class Screenshot:
    """PNG bytes returned from a browser capture."""
    data: bytes
    width: int
    height: int


@dataclass
class DiffResult:
    """Result of a visual comparison between two screenshots."""
    match: bool
    diff_pixels: int
    diff_percent: float
    diff_image: Optional[bytes]  # PNG highlight of differing regions


class WebInteractionSkill:
    """
    Agent skill for browser-based interaction via Neko.

    Registered as: /web-interaction
    Used by: frontend agent, ai-engineer agent
    """

    def __init__(self, base_url: str = NEKO_BASE_URL, password: str = "admin") -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base,
            auth=("admin", password),
            timeout=10.0,
        )

    async def navigate(self, url: str) -> Screenshot:
        """Navigate the browser to url and return a screenshot."""
        await self._client.post("/api/room/screen/set", json={"url": url})
        return await self.screenshot()

    async def click(self, x: int, y: int) -> Screenshot:
        """Click at pixel coordinates (x, y) and return a screenshot."""
        await self._client.post("/api/room/mouse/click", json={"x": x, "y": y})
        return await self.screenshot()

    async def type_text(self, text: str) -> Screenshot:
        """Type text into the currently focused element and return a screenshot."""
        await self._client.post("/api/room/keyboard/type", json={"text": text})
        return await self.screenshot()

    async def screenshot(self) -> Screenshot:
        """Capture the current screen as a PNG screenshot."""
        resp = await self._client.get("/api/room/screen/screenshot")
        resp.raise_for_status()
        # Neko returns image/png directly
        return Screenshot(data=resp.content, width=1920, height=1080)

    async def visual_diff(self, expected_path: str) -> DiffResult:
        """
        Compare current screen against a stored reference image.

        Args:
            expected_path: Absolute path to the reference PNG on the shared volume.

        Returns:
            DiffResult with match flag and pixel-level diff information.
        """
        current = await self.screenshot()
        # Actual pixel-diff implementation uses pillow / imagehash
        # Stub shown here for API design clarity
        raise NotImplementedError("visual_diff requires pillow integration (Phase 3)")

    async def close(self) -> None:
        await self._client.aclose()
```

### Skill registration (in `src/agent_orchestrator/core/skill.py`)

```python
# Register the skill under the /web-interaction command
registry.register(
    name="web-interaction",
    skill=WebInteractionSkill(),
    agents=["frontend", "ai-engineer"],
    description="Control a live Chromium browser via Neko REST API",
)
```

---

## Neko REST API Endpoints Used

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/api/room/screen/screenshot` | Capture current screen as PNG |
| `POST` | `/api/room/mouse/click` | Click at (x, y) coordinates |
| `POST` | `/api/room/keyboard/type` | Type text into active element |
| `GET` | `/api/room` | Session/room metadata |
| `POST` | `/api/room/screen/set` | Navigate to URL |

Full OpenAPI spec: `http://localhost:8080/api/swagger`

---

## Dashboard Integration

### Iframe embed (Option A — simplest)

The Neko frontend is served at `http://localhost:8080`. The dashboard can embed it:

```html
<!-- In dashboard index.html, inside a new "Preview" panel -->
<div id="sandbox-panel" class="panel hidden">
  <h3>Agent Output Preview</h3>
  <iframe
    id="neko-frame"
    src="http://localhost:8080"
    allow="camera; microphone; display-capture"
    width="100%"
    height="600px"
    frameborder="0"
  ></iframe>
  <div class="sandbox-controls">
    <button id="approve-btn" class="btn btn-success">Approve</button>
    <button id="reject-btn"  class="btn btn-danger">Reject</button>
    <textarea id="reject-reason" placeholder="Rejection reason for agent..."></textarea>
  </div>
</div>
```

### Approve / Reject flow (dashboard WebSocket event)

```javascript
// In app.js
document.getElementById('approve-btn').addEventListener('click', () => {
  ws.send(JSON.stringify({ type: 'sandbox_decision', decision: 'approve', jobId: currentJobId }));
});

document.getElementById('reject-btn').addEventListener('click', () => {
  const reason = document.getElementById('reject-reason').value;
  ws.send(JSON.stringify({ type: 'sandbox_decision', decision: 'reject', reason, jobId: currentJobId }));
});
```

```python
# In dashboard/app.py WebSocket handler
elif msg["type"] == "sandbox_decision":
    job_id = msg["job_id"]
    if msg["decision"] == "approve":
        await event_bus.emit(Event("sandbox_approved", {"job_id": job_id}))
    else:
        await event_bus.emit(Event("sandbox_rejected", {
            "job_id": job_id,
            "reason": msg.get("reason", ""),
        }))
```

The `agent_runner.py` listens for `sandbox_rejected` and requeues the task with the rejection
reason appended to the prompt.

---

## Implementation Phases

| Phase | Tasks | Effort |
|-------|-------|--------|
| **1. Docker integration** | Add `neko` service to `docker-compose.yml`, declare `agent_output` volume, smoke-test screenshot endpoint | ~1 hour |
| **2. Skill skeleton** | Create `web_interaction.py`, register skill, write unit tests with httpx mock | ~2 hours |
| **3. Dashboard iframe** | Add sandbox panel to `index.html`/`app.js`/`style.css`, wire Approve/Reject buttons to WebSocket events | ~2 hours |
| **4. Approve/Reject flow** | Handle `sandbox_decision` in `app.py`, requeue rejected tasks in `agent_runner.py`, add integration tests | ~4 hours |
| **5. Visual regression** | Implement `visual_diff` with pillow, store reference screenshots per job, CI comparison step | Future |

**Total for Phases 1-4:** approximately 9 hours

---

## Evaluation

| Criterion | Score | Rationale |
|-----------|-------|-----------|
| Applicable | 0.9 | Directly solves Phase 1 roadmap item; no alternative covers the same scope |
| Quality | 0.8 | 7 000+ stars, active maintenance, OpenAPI spec, production-tested |
| Compatible | 0.7 | Docker-native, REST API matches our stack; WebRTC in iframe needs CORS/header care |
| Safe | 0.9 | Fully self-hosted, open-source (Apache-2.0), no external calls |
| **Overall** | **0.825** | Strong recommendation to proceed with Phase 1 and 2 |

### Risks and mitigations

| Risk | Mitigation |
|------|------------|
| WebRTC iframe blocked by browser security policy | Serve dashboard and Neko under same origin via nginx reverse proxy or use Neko's standalone player URL |
| `shm_size: 2gb` may be too large on low-RAM machines | Make it configurable via `.env`; Chromium works with 512 MB for simple pages |
| Neko API surface changes between versions | Pin image tag (e.g., `m1k1o/neko:chromium-1.6`) in docker-compose |
| Agent output volume grows unbounded | Add a cleanup skill or TTL-based purge job |

---

## References

- GitHub: https://github.com/m1k1o/neko
- Docker Hub: https://hub.docker.com/r/m1k1o/neko
- REST API docs (live): `http://localhost:8080/api/swagger`
- WebRTC latency benchmarks: < 300 ms on LAN (vendor docs)
