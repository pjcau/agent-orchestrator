/** Agent Orchestrator Dashboard — redesigned UI with chat, agent tree, interactive graph */

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
  let agentRegistry = null; // loaded from /api/agents
  let events = [];
  let isRunning = false;
  let activeSkills = new Set();
  let graphNodeStates = {}; // node_name -> "active" | "done" | "error"
  const MAX_EVENTS = 500;

  // --- DOM refs ---
  const $status = document.getElementById("status-badge");
  const $tokens = document.getElementById("total-tokens");
  const $cost = document.getElementById("total-cost");
  const $wsIndicator = document.getElementById("ws-indicator");
  const $agentTree = document.getElementById("agent-tree");
  const $agentMessages = document.getElementById("agent-messages");
  const $graphCanvas = document.getElementById("graph-canvas");
  const $chatMessages = document.getElementById("chat-messages");
  const $promptInput = document.getElementById("prompt-input");
  const $btnSend = document.getElementById("btn-send");
  const $promptModel = document.getElementById("prompt-model");
  const $promptGraph = document.getElementById("prompt-graph");
  const $filterType = document.getElementById("filter-type");
  const $btnClear = document.getElementById("btn-clear");
  const $timeline = document.getElementById("timeline");
  const $detailView = document.getElementById("detail-view");

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
        renderHeader();
        renderGraph();
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
      const coderModel = models.find((m) => m.name.includes("coder") || m.name.includes("qwen2.5-coder"));
      if (coderModel) $promptModel.value = coderModel.name;
    } catch (e) {
      $promptModel.innerHTML = '<option value="">Failed to load models</option>';
    }
  }

  // --- Load agent registry ---
  async function loadAgents() {
    try {
      const resp = await fetch("/api/agents");
      const data = await resp.json();
      agentRegistry = data;
      renderAgentTree();
    } catch (e) {
      agentRegistry = null;
      renderAgentTree();
    }
  }

  // --- Agent Tree ---
  function renderAgentTree() {
    if (!agentRegistry || !agentRegistry.agents) {
      $agentTree.innerHTML = '<div class="empty-state">No agent registry</div>';
      return;
    }

    const leader = agentRegistry.agents.find((a) => a.name === "team-lead");
    const subAgents = agentRegistry.agents.filter((a) => a.name !== "team-lead");

    let html = "";

    // Team lead node
    if (leader) {
      html += renderAgentNode(leader, true);
    }

    // Sub-agents
    html += '<div class="agent-sub-agents">';
    subAgents.forEach((agent) => {
      html += renderAgentNode(agent, false);
    });
    html += "</div>";

    $agentTree.innerHTML = html;
  }

  function renderAgentNode(agent, isLead) {
    const statusClass = getAgentStatus(agent.name);
    const skills = agent.skills || [];

    let skillsHtml = "";
    if (skills.length) {
      skillsHtml = '<div class="skill-list">';
      skills.forEach((sk) => {
        const isActive = activeSkills.has(sk);
        skillsHtml += `<span class="skill-tag${isActive ? " active" : ""}">${esc(sk)}</span>`;
      });
      skillsHtml += "</div>";
    }

    return `
      <div class="agent-node ${isLead ? "lead" : ""} ${statusClass}">
        <div class="agent-node-header">
          <span class="agent-node-name">${esc(agent.name)}</span>
          <span class="agent-node-model">${esc(agent.model || "")}</span>
          <span class="agent-status-dot ${statusClass}"></span>
        </div>
        ${agent.description ? `<div class="agent-node-desc">${esc(truncate(agent.description, 80))}</div>` : ""}
        ${skillsHtml}
      </div>
    `;
  }

  function getAgentStatus(name) {
    const a = snapshot.agents[name];
    if (!a) return "idle";
    return a.status || "idle";
  }

  // --- Inter-Agent Messages ---
  function renderAgentMessages() {
    const tasks = snapshot.tasks || [];
    if (!tasks.length) {
      $agentMessages.innerHTML = '<div class="empty-state">No messages yet</div>';
      return;
    }

    $agentMessages.innerHTML = tasks
      .slice(-20) // show last 20
      .map(
        (t) => `
        <div class="agent-msg">
          <span class="agent-msg-from">${esc(t.from_agent || "?")}</span>
          <span class="agent-msg-arrow">&rarr;</span>
          <span class="agent-msg-to">${esc(t.to_agent || "?")}</span>
          <span class="agent-msg-text">${esc(truncate(t.description, 60))}</span>
          <span class="agent-msg-status ${t.status || "pending"}"></span>
        </div>
      `
      )
      .join("");
  }

  // --- Interactive Graph ---
  function renderGraph() {
    const g = snapshot.graph;
    if (!g || (!g.nodes.length && !g.edges.length)) {
      $graphCanvas.innerHTML = '<div class="empty-state">Submit a prompt to see the graph</div>';
      return;
    }

    // Build adjacency for layout
    const nodes = g.nodes || [];
    const edges = g.edges || [];

    // Compute layers using topological ordering
    const layers = computeLayers(nodes, edges);

    let html = "";
    layers.forEach((layer, layerIdx) => {
      html += '<div class="graph-row">';
      layer.forEach((nodeName) => {
        const state = graphNodeStates[nodeName] || "";
        const isSpecial = nodeName === "__start__" || nodeName === "__end__";
        const displayName = nodeName === "__start__" ? "START" : nodeName === "__end__" ? "END" : nodeName;
        const specialClass = nodeName === "__start__" ? "start" : nodeName === "__end__" ? "end" : "";

        html += `
          <div class="gnode ${state} ${specialClass}" data-node="${esc(nodeName)}" onclick="window._showNodeDetail('${esc(nodeName)}')">
            <div class="gnode-box">${esc(displayName)}</div>
          </div>
        `;
      });
      html += "</div>";

      // Add connector between layers (except after last)
      if (layerIdx < layers.length - 1) {
        html += '<div class="graph-connector">';
        // Draw arrows for edges from this layer to next
        const currentNodes = new Set(layer);
        const nextNodes = new Set(layers[layerIdx + 1] || []);
        edges.forEach((e) => {
          const src = e.source;
          if (currentNodes.has(src)) {
            const targets = e.target ? [e.target] : e.routes || [];
            targets.forEach((tgt) => {
              if (nextNodes.has(tgt)) {
                const label = e.type === "conditional" ? "?" : "";
                html += `<span class="graph-arrow">${label}&darr;</span>`;
              }
            });
          }
        });
        html += "</div>";
      }
    });

    $graphCanvas.innerHTML = html;
  }

  function computeLayers(nodes, edges) {
    // Simple topological layer assignment
    const inDegree = {};
    const adjList = {};
    nodes.forEach((n) => {
      inDegree[n] = 0;
      adjList[n] = [];
    });

    edges.forEach((e) => {
      const src = e.source;
      const targets = e.target ? [e.target] : e.routes || [];
      targets.forEach((tgt) => {
        if (adjList[src]) adjList[src].push(tgt);
        if (inDegree[tgt] !== undefined) inDegree[tgt]++;
      });
    });

    const layers = [];
    const visited = new Set();
    let queue = nodes.filter((n) => inDegree[n] === 0);
    if (!queue.length && nodes.length) queue = [nodes[0]]; // fallback

    while (queue.length && visited.size < nodes.length) {
      layers.push([...queue]);
      queue.forEach((n) => visited.add(n));

      const nextQueue = [];
      queue.forEach((n) => {
        (adjList[n] || []).forEach((tgt) => {
          if (!visited.has(tgt)) {
            inDegree[tgt]--;
            if (inDegree[tgt] <= 0 && !nextQueue.includes(tgt)) {
              nextQueue.push(tgt);
            }
          }
        });
      });
      queue = nextQueue;
    }

    // Add any remaining nodes
    const remaining = nodes.filter((n) => !visited.has(n));
    if (remaining.length) layers.push(remaining);

    return layers;
  }

  // Global handler for node clicks
  window._showNodeDetail = function (nodeName) {
    // Find relevant events for this node
    const nodeEvents = events.filter(
      (e) => e.node_name === nodeName || (e.data && (e.data.node === nodeName || e.data.from === nodeName || e.data.to === nodeName))
    );

    if (!nodeEvents.length) {
      $detailView.innerHTML = `<div class="detail-node-title">${esc(nodeName)}</div><div class="empty-state">No events for this node yet</div>`;
      return;
    }

    let html = `<div class="detail-node-title">${esc(nodeName)}</div>`;
    nodeEvents.forEach((evt) => {
      const time = formatTime(evt.timestamp);
      html += `<div class="detail-event">
        <span class="detail-event-time">${time}</span>
        <span class="detail-event-type">${esc(evt.event_type)}</span>
        <pre class="detail-event-data">${formatJson(evt.data || {})}</pre>
      </div>`;
    });

    $detailView.innerHTML = html;
  };

  // --- Chat ---
  function addChatBubble(role, content) {
    const bubble = document.createElement("div");
    bubble.className = `chat-bubble ${role}`;

    if (role === "assistant" && typeof content === "object") {
      // Structured response with steps
      let html = "";
      const steps = content.steps || [];
      if (steps.length) {
        steps.forEach((step) => {
          html += `<div class="chat-step"><span class="chat-step-label">${esc(step.node)}</span><span class="chat-step-text">${formatOutput(step.output || "")}</span></div>`;
        });
      } else if (content.output) {
        html = `<span class="chat-step-text">${formatOutput(content.output)}</span>`;
      }
      if (content.usage) {
        html += `<div class="chat-usage">${content.usage.input_tokens} in / ${content.usage.output_tokens} out &middot; ${esc(content.usage.model || "")} &middot; ${content.elapsed_s || 0}s</div>`;
      }
      bubble.innerHTML = html;
    } else {
      bubble.textContent = content;
    }

    $chatMessages.appendChild(bubble);
    $chatMessages.scrollTop = $chatMessages.scrollHeight;
  }

  function addChatLoading() {
    const bubble = document.createElement("div");
    bubble.className = "chat-bubble assistant loading";
    bubble.id = "chat-loading";
    bubble.innerHTML = '<div class="chat-spinner"></div><span>Running graph...</span>';
    $chatMessages.appendChild(bubble);
    $chatMessages.scrollTop = $chatMessages.scrollHeight;
  }

  function removeChatLoading() {
    const el = document.getElementById("chat-loading");
    if (el) el.remove();
  }

  function formatOutput(text) {
    // Basic formatting: preserve newlines, escape HTML
    return esc(text).replace(/\n/g, "<br>");
  }

  // --- Send Prompt ---
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
    $promptInput.value = "";
    $promptInput.style.height = "auto";

    // Add user message to chat
    addChatBubble("user", text);
    addChatLoading();

    // Reset graph node states
    graphNodeStates = {};

    try {
      const resp = await fetch("/api/prompt", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: text, model: model, graph_type: graphType }),
      });
      const data = await resp.json();

      removeChatLoading();

      if (data.success) {
        addChatBubble("assistant", data);
        // Update token counter
        if (data.usage) {
          const total = (data.usage.input_tokens || 0) + (data.usage.output_tokens || 0);
          snapshot.total_tokens = (snapshot.total_tokens || 0) + total;
          renderHeader();
        }
      } else {
        addChatBubble("assistant", `Error: ${data.error || "Unknown error"}`);
      }
    } catch (e) {
      removeChatLoading();
      addChatBubble("assistant", `Request failed: ${e.message}`);
    } finally {
      isRunning = false;
      $btnSend.disabled = false;
      $promptInput.disabled = false;
      $promptInput.focus();
    }
  }

  // --- Event handling ---
  function handleEvent(evt) {
    events.push(evt);
    if (events.length > MAX_EVENTS) events = events.slice(-MAX_EVENTS);

    updateSnapshotFromEvent(evt);
    renderHeader();
    renderTimelineEvent(evt);
    renderAgentTree();
    renderAgentMessages();

    // Update graph node states
    if (evt.event_type === "graph.start") {
      snapshot.graph = {
        nodes: evt.data.nodes || [],
        edges: evt.data.edges || [],
      };
      graphNodeStates = {};
      renderGraph();
    } else if (evt.event_type === "graph.node.enter") {
      graphNodeStates[evt.node_name || evt.data.node] = "active";
      renderGraph();
    } else if (evt.event_type === "graph.node.exit") {
      graphNodeStates[evt.node_name || evt.data.node] = "done";
      renderGraph();
    } else if (evt.event_type === "graph.end") {
      // Mark all remaining active as done
      Object.keys(graphNodeStates).forEach((k) => {
        if (graphNodeStates[k] === "active") graphNodeStates[k] = "done";
      });
      renderGraph();
    }

    // Track active skills
    if (evt.event_type === "agent.tool_call" && evt.data.tool_name) {
      activeSkills.add(evt.data.tool_name);
      renderAgentTree();
      setTimeout(() => {
        activeSkills.delete(evt.data.tool_name);
        renderAgentTree();
      }, 3000);
    }

    // Auto-scroll timeline
    $timeline.scrollTop = $timeline.scrollHeight;
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
      const task = snapshot.tasks.find((tk) => tk.task_id === evt.data.task_id);
      if (task) task.status = evt.data.success ? "completed" : "failed";
    } else if (t === "metrics.cost_update") {
      snapshot.total_cost_usd = evt.data.total_cost_usd || snapshot.total_cost_usd;
    } else if (t === "metrics.token_update") {
      snapshot.total_tokens = evt.data.total_tokens || snapshot.total_tokens;
      if (evt.agent_name && snapshot.agents[evt.agent_name]) {
        snapshot.agents[evt.agent_name].tokens = evt.data.agent_tokens || 0;
        snapshot.agents[evt.agent_name].cost_usd = evt.data.agent_cost_usd || 0;
      }
    }
  }

  // --- Rendering ---
  function renderHeader() {
    const s = snapshot.orchestrator_status;
    $status.textContent = s.toUpperCase();
    $status.className = `badge ${s}`;
    $tokens.textContent = formatNumber(snapshot.total_tokens);
    $cost.textContent = `$${(snapshot.total_cost_usd || 0).toFixed(3)}`;
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
    el.addEventListener("click", () => showEventDetail(evt));
    $timeline.appendChild(el);
  }

  function showEventDetail(evt) {
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
        return `${agent}calling ${d.tool_name || "?"}`;
      case "agent.tool_result":
        return `${agent}tool result: ${truncate(d.result || "", 80)}`;
      case "agent.complete":
        return `${agent}completed`;
      case "agent.error":
        return `${agent}error: ${d.error || "unknown"}`;
      case "graph.start":
        return `graph started (${(d.nodes || []).length} nodes)`;
      case "graph.node.enter":
        return `${node}entering`;
      case "graph.node.exit":
        return `${node}exited`;
      case "graph.edge":
        return `${d.from || "?"} -> ${d.to || "?"}`;
      case "graph.parallel":
        return `parallel: ${(d.nodes || []).join(", ")}`;
      case "graph.end":
        return `graph ended (${d.success ? "ok" : "fail"}) ${d.elapsed_s || 0}s`;
      case "cooperation.task_assigned":
        return `${d.from_agent || "?"} -> ${d.to_agent || "?"}: ${truncate(d.description || "", 60)}`;
      case "cooperation.task_completed":
        return `${d.agent_name || "?"} finished ${d.task_id || ""}`;
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
    div.textContent = String(s);
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
  loadAgents();

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
      renderHeader();
      renderGraph();
      renderAgentMessages();
    })
    .catch(() => {});
})();
