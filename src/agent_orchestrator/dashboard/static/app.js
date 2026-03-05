/** Agent Orchestrator Dashboard — real-time monitoring UI with interactive prompt */

(function () {
  "use strict";

  // --- State ---
  let ws = null;
  let snapshot = {
    orchestrator_status: "idle",
    agents: {},
    tasks: [],
    total_cost_usd: 0,
    total_tokens: 0,
    graph: { nodes: [], edges: [] },
    event_count: 0,
  };
  let events = [];
  let isRunning = false;
  const MAX_EVENTS = 500;

  // --- DOM refs ---
  const $status = document.getElementById("status-badge");
  const $tokens = document.getElementById("total-tokens");
  const $cost = document.getElementById("total-cost");
  const $eventCount = document.getElementById("event-count");
  const $wsIndicator = document.getElementById("ws-indicator");
  const $agentsGrid = document.getElementById("agents-grid");
  const $timeline = document.getElementById("timeline");
  const $taskPlan = document.getElementById("task-plan");
  const $graphView = document.getElementById("graph-view");
  const $detailView = document.getElementById("detail-view");
  const $filterType = document.getElementById("filter-type");
  const $autoScroll = document.getElementById("auto-scroll");
  const $btnClear = document.getElementById("btn-clear");

  // Prompt elements
  const $promptInput = document.getElementById("prompt-input");
  const $btnSend = document.getElementById("btn-send");
  const $promptModel = document.getElementById("prompt-model");
  const $promptGraph = document.getElementById("prompt-graph");
  const $responseArea = document.getElementById("response-area");
  const $responseContent = document.getElementById("response-content");
  const $btnDismiss = document.getElementById("btn-dismiss-response");

  // --- WebSocket ---
  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws`);

    ws.onopen = () => {
      $wsIndicator.className = "ws-dot connected";
      $wsIndicator.title = "WebSocket connected";
    };

    ws.onclose = () => {
      $wsIndicator.className = "ws-dot disconnected";
      $wsIndicator.title = "WebSocket disconnected — reconnecting...";
      setTimeout(connect, 2000);
    };

    ws.onerror = () => ws.close();

    ws.onmessage = (msg) => {
      const payload = JSON.parse(msg.data);
      if (payload.type === "snapshot") {
        snapshot = payload.data;
        renderAll();
      } else if (payload.type === "event") {
        handleEvent(payload.data);
      }
    };
  }

  // --- Load Ollama models ---
  async function loadModels() {
    try {
      const resp = await fetch("/api/models");
      const data = await resp.json();
      const models = data.models || [];
      $promptModel.innerHTML = "";
      if (!models.length) {
        $promptModel.innerHTML = '<option value="">No models found</option>';
        return;
      }
      models.forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m.name;
        opt.textContent = `${m.name} (${m.size})`;
        $promptModel.appendChild(opt);
      });
      // Select coding model by default if available
      const coderModel = models.find((m) => m.name.includes("coder") || m.name.includes("qwen2.5-coder"));
      if (coderModel) $promptModel.value = coderModel.name;
    } catch (e) {
      $promptModel.innerHTML = '<option value="">Failed to load models</option>';
    }
  }

  // --- Send prompt ---
  async function sendPrompt() {
    const text = $promptInput.value.trim();
    if (!text || isRunning) return;

    const model = $promptModel.value;
    const graphType = $promptGraph.value;

    if (!model) {
      alert("No model selected. Is Ollama running?");
      return;
    }

    isRunning = true;
    $btnSend.disabled = true;
    $promptInput.disabled = true;

    // Show response area with loading spinner
    $responseArea.classList.remove("hidden");
    $responseContent.innerHTML = '<div class="response-loading"><div class="spinner"></div>Running graph...</div>';

    try {
      const resp = await fetch("/api/prompt", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: text, model: model, graph_type: graphType }),
      });
      const data = await resp.json();

      if (data.success) {
        renderResponse(data);
      } else {
        $responseContent.innerHTML = `<span style="color:var(--red)">Error: ${esc(data.error || "Unknown error")}</span>`;
      }
    } catch (e) {
      $responseContent.innerHTML = `<span style="color:var(--red)">Request failed: ${esc(e.message)}</span>`;
    } finally {
      isRunning = false;
      $btnSend.disabled = false;
      $promptInput.disabled = false;
      $promptInput.focus();
    }
  }

  function renderResponse(data) {
    let html = "";
    const steps = data.steps || [];
    if (steps.length > 0) {
      steps.forEach((step) => {
        html += `<span class="step-label">${esc(step.node)}</span>`;
        html += `<span class="step-text">${esc(step.output || "")}</span>`;
      });
    } else if (data.output) {
      html = `<span class="step-text">${esc(data.output)}</span>`;
    }
    if (data.usage) {
      html += `<span class="step-label">Usage</span>`;
      html += `<span class="step-text">${data.usage.input_tokens} in / ${data.usage.output_tokens} out tokens | model: ${esc(data.usage.model || "")}</span>`;
    }
    $responseContent.innerHTML = html;
  }

  // --- Event handling ---
  function handleEvent(evt) {
    events.push(evt);
    if (events.length > MAX_EVENTS) events = events.slice(-MAX_EVENTS);

    updateSnapshotFromEvent(evt);
    renderAll();
    renderTimelineEvent(evt);

    if ($autoScroll.checked) {
      $timeline.scrollTop = $timeline.scrollHeight;
    }
  }

  function updateSnapshotFromEvent(evt) {
    const t = evt.event_type;
    snapshot.event_count = (snapshot.event_count || 0) + 1;

    if (t === "orchestrator.start") {
      snapshot.orchestrator_status = "running";
    } else if (t === "orchestrator.end") {
      snapshot.orchestrator_status = evt.data.success ? "completed" : "failed";
    } else if (t === "agent.spawn") {
      snapshot.agents[evt.agent_name] = {
        name: evt.agent_name,
        status: "running",
        steps: 0,
        tokens: 0,
        cost_usd: 0,
        provider: evt.data.provider || "",
        role: evt.data.role || "",
        tools: evt.data.tools || [],
      };
    } else if (t === "agent.step" && snapshot.agents[evt.agent_name]) {
      snapshot.agents[evt.agent_name].steps += 1;
    } else if (t === "agent.complete" && snapshot.agents[evt.agent_name]) {
      snapshot.agents[evt.agent_name].status = "completed";
    } else if (t === "agent.error" && snapshot.agents[evt.agent_name]) {
      snapshot.agents[evt.agent_name].status = "error";
    } else if (t === "agent.stalled" && snapshot.agents[evt.agent_name]) {
      snapshot.agents[evt.agent_name].status = "stalled";
    } else if (t === "cooperation.task_assigned") {
      snapshot.tasks.push({
        task_id: evt.data.task_id,
        from_agent: evt.data.from_agent,
        to_agent: evt.data.to_agent,
        description: evt.data.description || "",
        status: "pending",
        priority: evt.data.priority || "normal",
      });
    } else if (t === "cooperation.task_completed") {
      const task = snapshot.tasks.find((t) => t.task_id === evt.data.task_id);
      if (task) task.status = evt.data.success ? "completed" : "failed";
    } else if (t === "metrics.cost_update") {
      snapshot.total_cost_usd = evt.data.total_cost_usd || snapshot.total_cost_usd;
    } else if (t === "metrics.token_update") {
      snapshot.total_tokens = evt.data.total_tokens || snapshot.total_tokens;
      if (evt.agent_name && snapshot.agents[evt.agent_name]) {
        snapshot.agents[evt.agent_name].tokens = evt.data.agent_tokens || 0;
        snapshot.agents[evt.agent_name].cost_usd = evt.data.agent_cost_usd || 0;
      }
    } else if (t === "graph.start") {
      snapshot.graph = {
        nodes: evt.data.nodes || [],
        edges: evt.data.edges || [],
      };
    }
  }

  // --- Rendering ---
  function renderAll() {
    renderHeader();
    renderAgents();
    renderTaskPlan();
    renderGraph();
  }

  function renderHeader() {
    const s = snapshot.orchestrator_status;
    $status.textContent = s.toUpperCase();
    $status.className = `badge ${s}`;
    $tokens.textContent = formatNumber(snapshot.total_tokens);
    $cost.textContent = `$${(snapshot.total_cost_usd || 0).toFixed(4)}`;
    $eventCount.textContent = formatNumber(snapshot.event_count || 0);
  }

  function renderAgents() {
    const agents = Object.values(snapshot.agents);
    if (!agents.length) {
      $agentsGrid.innerHTML = '<div class="empty-state">No agents spawned yet</div>';
      return;
    }

    $agentsGrid.innerHTML = agents
      .map(
        (a) => `
      <div class="agent-card ${a.status}">
        <div class="agent-header">
          <span class="agent-name">${esc(a.name)}</span>
          <span class="agent-status ${a.status}">${a.status}</span>
        </div>
        <div class="agent-meta">${esc(a.provider)} &middot; ${esc(truncate(a.role, 60))}</div>
        <div class="agent-stats">
          <span>Steps: ${a.steps}</span>
          <span>Tok: ${formatNumber(a.tokens)}</span>
          <span>$${(a.cost_usd || 0).toFixed(4)}</span>
        </div>
        ${
          a.tools && a.tools.length
            ? `<div class="agent-tools">${a.tools.map((t) => `<span class="tool-tag">${esc(t)}</span>`).join("")}</div>`
            : ""
        }
      </div>
    `
      )
      .join("");
  }

  function renderTimelineEvent(evt) {
    const filter = $filterType.value;
    if (filter !== "all" && !evt.event_type.startsWith(filter)) return;

    const cat = eventCategory(evt.event_type);
    const time = formatTime(evt.timestamp);
    const icon = categoryIcon(cat);
    const desc = eventDescription(evt);

    const el = document.createElement("div");
    el.className = "event-item";
    el.innerHTML = `
      <span class="event-time">${time}</span>
      <span class="event-icon ${cat}">${icon}</span>
      <div class="event-body">
        <div class="event-type">${esc(evt.event_type)}</div>
        <div class="event-desc">${esc(desc)}</div>
      </div>
    `;
    el.addEventListener("click", () => showDetail(evt));
    $timeline.appendChild(el);
  }

  function renderTaskPlan() {
    if (!snapshot.tasks.length) {
      $taskPlan.innerHTML = '<div class="empty-state">No tasks assigned yet</div>';
      return;
    }

    $taskPlan.innerHTML = snapshot.tasks
      .map(
        (t) => `
      <div class="task-item">
        <div class="task-header">
          <span class="task-id">${esc(t.task_id || "")}</span>
          <span class="task-status-dot ${t.status}"></span>
        </div>
        <div class="task-desc">${esc(truncate(t.description, 120))}</div>
        <div class="task-meta">
          <span>${esc(t.from_agent || "?")}</span>
          <span class="arrow">-></span>
          <span>${esc(t.to_agent || "?")}</span>
          <span>&middot; ${t.priority}</span>
        </div>
      </div>
    `
      )
      .join("");
  }

  function renderGraph() {
    const g = snapshot.graph;
    if (!g || (!g.nodes.length && !g.edges.length)) {
      $graphView.innerHTML = '<div class="empty-state">No graph running</div>';
      return;
    }

    const nodesHtml = g.nodes
      .map((n) => `<span class="graph-node">${esc(n)}</span>`)
      .join("");

    const edgesHtml = g.edges
      .map((e) => {
        const target = e.target || (e.routes ? `{${e.routes.join("|")}}` : "?");
        const typ = e.type === "conditional" ? " (cond)" : "";
        return `<div>${esc(e.source)} <span class="arrow-sym">-></span> ${esc(target)}${typ}</div>`;
      })
      .join("");

    $graphView.innerHTML = `
      <div class="graph-nodes">${nodesHtml}</div>
      ${edgesHtml ? `<div class="graph-edge-list">${edgesHtml}</div>` : ""}
    `;
  }

  function showDetail(evt) {
    $detailView.innerHTML = `<pre>${formatJson(evt)}</pre>`;
  }

  // --- Helpers ---
  function eventCategory(type) {
    if (type.startsWith("agent")) return "agent";
    if (type.startsWith("graph")) return "graph";
    if (type.startsWith("cooperation")) return "cooperation";
    if (type.startsWith("metrics")) return "metrics";
    return "orchestrator";
  }

  function categoryIcon(cat) {
    const icons = { agent: "A", graph: "G", cooperation: "C", metrics: "M", orchestrator: "O" };
    return icons[cat] || "?";
  }

  function eventDescription(evt) {
    const d = evt.data || {};
    const agent = evt.agent_name ? `[${evt.agent_name}] ` : "";
    const node = evt.node_name ? `node:${evt.node_name} ` : "";

    switch (evt.event_type) {
      case "agent.spawn":
        return `${agent}spawned with provider ${d.provider || "?"}`;
      case "agent.step":
        return `${agent}step ${d.step || ""}`;
      case "agent.tool_call":
        return `${agent}calling ${d.tool_name || "?"}(${truncate(JSON.stringify(d.arguments || {}), 60)})`;
      case "agent.tool_result":
        return `${agent}tool result: ${truncate(d.result || "", 80)}`;
      case "agent.complete":
        return `${agent}completed: ${truncate(d.output || "", 80)}`;
      case "agent.error":
        return `${agent}error: ${d.error || "unknown"}`;
      case "graph.node.enter":
        return `${node}entering`;
      case "graph.node.exit":
        return `${node}exited`;
      case "graph.edge":
        return `${d.from || "?"} -> ${d.to || "?"}`;
      case "graph.parallel":
        return `parallel: ${(d.nodes || []).join(", ")}`;
      case "cooperation.task_assigned":
        return `${d.from_agent || "?"} -> ${d.to_agent || "?"}: ${truncate(d.description || "", 60)}`;
      case "cooperation.task_completed":
        return `${d.agent_name || "?"} finished ${d.task_id || ""}`;
      case "metrics.cost_update":
        return `total: $${(d.total_cost_usd || 0).toFixed(4)}`;
      case "metrics.token_update":
        return `total: ${formatNumber(d.total_tokens || 0)} tokens`;
      default:
        return JSON.stringify(d).slice(0, 100);
    }
  }

  function formatTime(ts) {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function formatNumber(n) {
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
    if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
    return String(n);
  }

  function truncate(s, max) {
    if (!s) return "";
    return s.length > max ? s.slice(0, max) + "..." : s;
  }

  function esc(s) {
    if (!s) return "";
    const div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
  }

  function formatJson(obj) {
    return JSON.stringify(obj, null, 2)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"([^"]+)":/g, '<span class="detail-key">"$1"</span>:');
  }

  // --- Event listeners ---
  $filterType.addEventListener("change", () => {
    $timeline.innerHTML = "";
    events.forEach((e) => renderTimelineEvent(e));
  });

  $btnClear.addEventListener("click", () => {
    $timeline.innerHTML = "";
    events = [];
  });

  $btnDismiss.addEventListener("click", () => {
    $responseArea.classList.add("hidden");
  });

  $btnSend.addEventListener("click", sendPrompt);

  $promptInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendPrompt();
    }
  });

  // Auto-resize textarea
  $promptInput.addEventListener("input", () => {
    $promptInput.style.height = "auto";
    $promptInput.style.height = Math.min($promptInput.scrollHeight, 120) + "px";
  });

  // --- Init ---
  loadModels();

  fetch("/api/events?limit=200")
    .then((r) => r.json())
    .then((data) => {
      events = data;
      data.forEach((e) => renderTimelineEvent(e));
    })
    .catch(() => {})
    .finally(() => connect());

  fetch("/api/snapshot")
    .then((r) => r.json())
    .then((data) => {
      snapshot = data;
      renderAll();
    })
    .catch(() => {});
})();
