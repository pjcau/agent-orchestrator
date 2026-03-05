/** Agent Orchestrator Dashboard v0.2.0
 * - Streaming responses via WebSocket
 * - Multi-turn conversations
 * - File context attachment
 * - Task presets
 * - Model comparison
 * - Ollama model management
 * - Provider selector (Ollama + OpenRouter)
 */

(function () {
  "use strict";

  // --- State ---
  let ws = null;
  let streamWs = null;
  let snapshot = {
    orchestrator_status: "idle",
    agents: {},
    tasks: [],
    total_cost_usd: 0,
    total_tokens: 0,
    graph: { nodes: [], edges: [] },
    event_count: 0,
  };
  let agentRegistry = null;
  let events = [];
  let isRunning = false;
  let activeSkills = new Set();
  let graphNodeStates = {};
  let lastTokenSpeed = 0;
  let conversationId = null;
  let attachedFiles = []; // [{path, content}]
  let allModels = { ollama: [], openrouter: [] };
  const MAX_EVENTS = 500;

  // --- DOM refs ---
  const $ = (id) => document.getElementById(id);
  const $status = $("status-badge");
  const $tokens = $("total-tokens");
  const $cost = $("total-cost");
  const $tokenSpeed = $("token-speed");
  const $wsIndicator = $("ws-indicator");
  const $agentTree = $("agent-tree");
  const $agentMessages = $("agent-messages");
  const $graphCanvas = $("graph-canvas");
  const $chatMessages = $("chat-messages");
  const $promptInput = $("prompt-input");
  const $btnSend = $("btn-send");
  const $promptModel = $("prompt-model");
  const $promptGraph = $("prompt-graph");
  const $promptProvider = $("prompt-provider");
  const $toggleStream = $("toggle-stream");
  const $filterType = $("filter-type");
  const $btnClear = $("btn-clear");
  const $timeline = $("timeline");
  const $detailView = $("detail-view");
  const $presetsBar = $("presets-bar");
  const $attachedFiles = $("attached-files");
  const $btnAttach = $("btn-attach-file");
  const $btnClearCtx = $("btn-clear-context");
  const $btnNewChat = $("btn-new-chat");
  const $ollamaPullInput = $("ollama-pull-input");
  const $btnOllamaPull = $("btn-ollama-pull");
  const $ollamaModelList = $("ollama-model-list");
  const $compareModelA = $("compare-model-a");
  const $compareModelB = $("compare-model-b");
  const $btnCompare = $("btn-compare");
  const $compareResults = $("compare-results");
  const $filePickerModal = $("file-picker-modal");
  const $fileList = $("file-list");
  const $fileBreadcrumb = $("file-breadcrumb");
  const $btnClosePicker = $("btn-close-picker");

  // --- Event WebSocket ---
  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws`);

    ws.onopen = () => {
      $wsIndicator.className = "ws-dot connected";
      $wsIndicator.title = "WebSocket connected";
    };
    ws.onclose = () => {
      $wsIndicator.className = "ws-dot disconnected";
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

  // --- Streaming WebSocket ---
  function connectStream() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    streamWs = new WebSocket(`${proto}//${location.host}/ws/stream`);
    streamWs.onclose = () => setTimeout(connectStream, 3000);
    streamWs.onerror = () => streamWs.close();
  }

  // --- Load Models ---
  async function loadModels() {
    try {
      const resp = await fetch("/api/models");
      const data = await resp.json();
      allModels = data;
      updateModelSelector();
      updateCompareSelectors();
      renderOllamaModelList();
    } catch (e) {
      $promptModel.innerHTML = '<option value="">Failed to load</option>';
    }
  }

  function updateModelSelector() {
    const provider = $promptProvider.value;
    const models = provider === "openrouter" ? allModels.openrouter : allModels.ollama;
    $promptModel.innerHTML = "";

    if (!models.length) {
      $promptModel.innerHTML = '<option value="">No models</option>';
      return;
    }

    models.forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m.name;
      opt.textContent = `${m.name} (${m.size})`;
      $promptModel.appendChild(opt);
    });

    // Auto-select best model
    if (provider === "ollama") {
      const coder = models.find((m) => m.name.includes("coder"));
      if (coder) $promptModel.value = coder.name;
    }
  }

  function updateCompareSelectors() {
    const allList = [...allModels.ollama, ...allModels.openrouter];
    [$compareModelA, $compareModelB].forEach((sel) => {
      sel.innerHTML = allList.map((m) => `<option value="${esc(m.name)}">${esc(m.name)}</option>`).join("");
    });
    if (allList.length > 1) $compareModelB.selectedIndex = 1;
  }

  // --- Agent Tree ---
  async function loadAgents() {
    try {
      const resp = await fetch("/api/agents");
      agentRegistry = await resp.json();
      renderAgentTree();
    } catch (e) {
      agentRegistry = null;
      renderAgentTree();
    }
  }

  function renderAgentTree() {
    if (!agentRegistry || !agentRegistry.agents) {
      $agentTree.innerHTML = '<div class="empty-state">No agent registry</div>';
      return;
    }
    const leader = agentRegistry.agents.find((a) => a.name === "team-lead");
    const subs = agentRegistry.agents.filter((a) => a.name !== "team-lead");
    let html = "";
    if (leader) html += renderAgentNode(leader, true);
    html += '<div class="agent-sub-agents">';
    subs.forEach((a) => (html += renderAgentNode(a, false)));
    html += "</div>";
    $agentTree.innerHTML = html;
  }

  function renderAgentNode(agent, isLead) {
    const status = getAgentStatus(agent.name);
    const skills = (agent.skills || [])
      .map((sk) => `<span class="skill-tag${activeSkills.has(sk) ? " active" : ""}">${esc(sk)}</span>`)
      .join("");
    return `
      <div class="agent-node ${isLead ? "lead" : ""} ${status}">
        <div class="agent-node-header">
          <span class="agent-node-name">${esc(agent.name)}</span>
          <span class="agent-node-model">${esc(agent.model || "")}</span>
          <span class="agent-status-dot ${status}"></span>
        </div>
        ${agent.description ? `<div class="agent-node-desc">${esc(truncate(agent.description, 80))}</div>` : ""}
        ${skills ? `<div class="skill-list">${skills}</div>` : ""}
      </div>`;
  }

  function getAgentStatus(name) {
    const a = snapshot.agents[name];
    return a ? a.status || "idle" : "idle";
  }

  // --- Inter-Agent Messages ---
  function renderAgentMessages() {
    const tasks = snapshot.tasks || [];
    if (!tasks.length) {
      $agentMessages.innerHTML = '<div class="empty-state">No messages yet</div>';
      return;
    }
    $agentMessages.innerHTML = tasks
      .slice(-20)
      .map(
        (t) => `
        <div class="agent-msg">
          <span class="agent-msg-from">${esc(t.from_agent || "?")}</span>
          <span class="agent-msg-arrow">&rarr;</span>
          <span class="agent-msg-to">${esc(t.to_agent || "?")}</span>
          <span class="agent-msg-text">${esc(truncate(t.description, 60))}</span>
          <span class="agent-msg-status ${t.status || "pending"}"></span>
        </div>`
      )
      .join("");
  }

  // --- Ollama Model Management ---
  function renderOllamaModelList() {
    const models = allModels.ollama || [];
    if (!models.length) {
      $ollamaModelList.innerHTML = '<div class="empty-state">No local models</div>';
      return;
    }
    $ollamaModelList.innerHTML = models
      .map(
        (m) => `
        <div class="ollama-model-item">
          <span class="ollama-model-name">${esc(m.name)}</span>
          <span class="ollama-model-size">${esc(m.size)}</span>
          <button class="btn-delete-model" onclick="window._deleteModel('${esc(m.name)}')" title="Delete">&times;</button>
        </div>`
      )
      .join("");
  }

  window._deleteModel = async function (name) {
    if (!confirm(`Delete model ${name}?`)) return;
    try {
      await fetch("/api/ollama/model", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      await loadModels();
    } catch (e) {
      alert("Failed to delete: " + e.message);
    }
  };

  async function pullModel() {
    const name = $ollamaPullInput.value.trim();
    if (!name) return;
    $btnOllamaPull.disabled = true;
    $btnOllamaPull.textContent = "Pulling...";
    try {
      const resp = await fetch("/api/ollama/pull", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const data = await resp.json();
      if (data.success) {
        $ollamaPullInput.value = "";
        await loadModels();
      } else {
        alert("Pull failed: " + (data.error || "unknown"));
      }
    } catch (e) {
      alert("Pull failed: " + e.message);
    } finally {
      $btnOllamaPull.disabled = false;
      $btnOllamaPull.textContent = "Pull";
    }
  }

  // --- Presets ---
  async function loadPresets() {
    try {
      const resp = await fetch("/api/presets");
      const data = await resp.json();
      $presetsBar.innerHTML = (data.presets || [])
        .map(
          (p) => `<button class="preset-btn" data-preset-id="${esc(p.id)}" data-prompt="${esc(p.prompt)}" data-graph="${esc(p.graph)}" title="${esc(p.label)}">
            <span class="preset-icon">${esc(p.icon)}</span>
            <span class="preset-label">${esc(p.label)}</span>
          </button>`
        )
        .join("");

      $presetsBar.querySelectorAll(".preset-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          const ctx = getFileContextText();
          if (!ctx) {
            alert("Attach a file first, then click a preset.");
            return;
          }
          const template = btn.dataset.prompt;
          const prompt = template.replace("{context}", ctx);
          $promptInput.value = prompt;
          $promptGraph.value = btn.dataset.graph;
          $promptInput.dispatchEvent(new Event("input"));
        });
      });
    } catch (e) {
      /* ignore */
    }
  }

  // --- File Context ---
  let currentFilePath = "";

  function openFilePicker() {
    $filePickerModal.classList.remove("hidden");
    loadDirectory("");
  }

  async function loadDirectory(path) {
    currentFilePath = path;
    try {
      const resp = await fetch(`/api/files?path=${encodeURIComponent(path)}`);
      const data = await resp.json();

      // Breadcrumb
      const parts = path ? path.split("/") : [];
      let crumbs = `<span class="crumb" data-path="">root</span>`;
      let acc = "";
      parts.forEach((p) => {
        acc += (acc ? "/" : "") + p;
        crumbs += ` / <span class="crumb" data-path="${esc(acc)}">${esc(p)}</span>`;
      });
      $fileBreadcrumb.innerHTML = crumbs;
      $fileBreadcrumb.querySelectorAll(".crumb").forEach((c) => {
        c.addEventListener("click", () => loadDirectory(c.dataset.path));
      });

      // File list
      $fileList.innerHTML = (data.items || [])
        .map((item) => {
          const icon = item.is_dir ? "📁" : "📄";
          const sizeStr = item.is_dir ? "" : `<span class="file-size">${formatSize(item.size)}</span>`;
          return `<div class="file-item ${item.is_dir ? "dir" : "file"}" data-path="${esc(item.path)}" data-isdir="${item.is_dir}">
            <span class="file-icon">${icon}</span>
            <span class="file-name">${esc(item.name)}</span>
            ${sizeStr}
          </div>`;
        })
        .join("");

      $fileList.querySelectorAll(".file-item").forEach((el) => {
        el.addEventListener("click", () => {
          if (el.dataset.isdir === "true") {
            loadDirectory(el.dataset.path);
          } else {
            attachFile(el.dataset.path);
            $filePickerModal.classList.add("hidden");
          }
        });
      });
    } catch (e) {
      $fileList.innerHTML = `<div class="empty-state">Error: ${esc(e.message)}</div>`;
    }
  }

  async function attachFile(path) {
    if (attachedFiles.find((f) => f.path === path)) return; // already attached
    try {
      const resp = await fetch(`/api/file?path=${encodeURIComponent(path)}`);
      const data = await resp.json();
      if (data.error) {
        alert(data.error);
        return;
      }
      attachedFiles.push({ path: data.path, content: data.content });
      renderAttachedFiles();
    } catch (e) {
      alert("Failed to read file: " + e.message);
    }
  }

  function renderAttachedFiles() {
    if (!attachedFiles.length) {
      $attachedFiles.innerHTML = "";
      return;
    }
    $attachedFiles.innerHTML = attachedFiles
      .map(
        (f, i) => `<span class="attached-file">
          <span class="attached-file-name">${esc(f.path)}</span>
          <button class="btn-remove-file" onclick="window._removeFile(${i})">&times;</button>
        </span>`
      )
      .join("");
  }

  window._removeFile = function (idx) {
    attachedFiles.splice(idx, 1);
    renderAttachedFiles();
  };

  function getFileContextText() {
    if (!attachedFiles.length) return "";
    return attachedFiles.map((f) => `--- ${f.path} ---\n${f.content}`).join("\n\n");
  }

  // --- Conversation ---
  async function startNewConversation() {
    try {
      const resp = await fetch("/api/conversation/new", { method: "POST" });
      const data = await resp.json();
      conversationId = data.conversation_id;
      $chatMessages.innerHTML = "";
      addSystemBubble("New conversation started");
    } catch (e) {
      /* ignore */
    }
  }

  function addSystemBubble(text) {
    const el = document.createElement("div");
    el.className = "chat-bubble system";
    el.textContent = text;
    $chatMessages.appendChild(el);
    $chatMessages.scrollTop = $chatMessages.scrollHeight;
  }

  // --- Chat ---
  function addChatBubble(role, content) {
    const bubble = document.createElement("div");
    bubble.className = `chat-bubble ${role}`;

    if (role === "assistant" && typeof content === "object") {
      let html = "";
      const steps = content.steps || [];
      if (steps.length) {
        steps.forEach((step) => {
          html += `<div class="chat-step"><span class="chat-step-label">${esc(step.node)}</span><span class="chat-step-text">${formatOutput(step.output || "")}</span></div>`;
        });
      } else if (content.output) {
        html = `<span class="chat-step-text">${formatOutput(content.output)}</span>`;
      }
      if (content.usage || content.elapsed_s) {
        const elapsed = content.elapsed_s || 0;
        const outTok = (content.usage && content.usage.output_tokens) || 0;
        const speed = elapsed > 0 ? (outTok / elapsed).toFixed(1) : 0;
        const model = (content.usage && content.usage.model) || "";
        html += `<div class="chat-usage">${outTok} tok &middot; ${speed} tok/s &middot; ${esc(model)} &middot; ${elapsed}s</div>`;
      }
      bubble.innerHTML = html;
    } else {
      bubble.textContent = typeof content === "string" ? content : JSON.stringify(content);
    }

    $chatMessages.appendChild(bubble);
    $chatMessages.scrollTop = $chatMessages.scrollHeight;
    return bubble;
  }

  function addStreamingBubble() {
    const bubble = document.createElement("div");
    bubble.className = "chat-bubble assistant streaming";
    bubble.id = "streaming-bubble";
    bubble.innerHTML = '<span class="stream-cursor"></span>';
    $chatMessages.appendChild(bubble);
    $chatMessages.scrollTop = $chatMessages.scrollHeight;
    return bubble;
  }

  function appendToStream(text) {
    const bubble = $("streaming-bubble");
    if (!bubble) return;
    // Remove cursor, add text, re-add cursor
    const cursor = bubble.querySelector(".stream-cursor");
    if (cursor) cursor.remove();
    bubble.insertAdjacentHTML("beforeend", esc(text));
    bubble.insertAdjacentHTML("beforeend", '<span class="stream-cursor"></span>');
    $chatMessages.scrollTop = $chatMessages.scrollHeight;
  }

  function finalizeStream(data) {
    const bubble = $("streaming-bubble");
    if (!bubble) return;
    bubble.id = "";
    bubble.classList.remove("streaming");
    const cursor = bubble.querySelector(".stream-cursor");
    if (cursor) cursor.remove();
    // Add usage info
    if (data) {
      const elapsed = data.elapsed_s || 0;
      const speed = data.speed || 0;
      const model = (data.usage && data.usage.model) || "";
      const tokens = (data.usage && data.usage.output_tokens) || 0;
      const meta = document.createElement("div");
      meta.className = "chat-usage";
      meta.textContent = `${tokens} tok · ${speed} tok/s · ${model} · ${elapsed}s`;
      bubble.appendChild(meta);
    }
    $chatMessages.scrollTop = $chatMessages.scrollHeight;
  }

  function formatOutput(text) {
    return esc(text).replace(/\n/g, "<br>");
  }

  // --- Send Prompt ---
  async function sendPrompt() {
    const text = $promptInput.value.trim();
    if (!text || isRunning) return;

    const model = $promptModel.value;
    const provider = $promptProvider.value;
    const graphType = $promptGraph.value;
    const useStreaming = $toggleStream.checked;

    if (!model) {
      alert("No model selected.");
      return;
    }

    isRunning = true;
    $btnSend.disabled = true;
    $promptInput.disabled = true;
    $promptInput.value = "";
    $promptInput.style.height = "auto";

    addChatBubble("user", text);
    graphNodeStates = {};

    const fileCtx = getFileContextText();

    if (useStreaming && streamWs && streamWs.readyState === WebSocket.OPEN) {
      // Streaming mode
      addStreamingBubble();
      streamWs.send(
        JSON.stringify({
          prompt: text,
          model: model,
          provider: provider,
          conversation_id: conversationId,
          file_context: fileCtx,
        })
      );
      // Streaming messages handled by streamWs.onmessage
    } else {
      // Non-streaming fallback
      const loadingBubble = document.createElement("div");
      loadingBubble.className = "chat-bubble assistant loading";
      loadingBubble.id = "chat-loading";
      loadingBubble.innerHTML = '<div class="chat-spinner"></div><span>Running graph...</span>';
      $chatMessages.appendChild(loadingBubble);
      $chatMessages.scrollTop = $chatMessages.scrollHeight;

      try {
        const resp = await fetch("/api/prompt", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            prompt: text,
            model: model,
            provider: provider,
            graph_type: graphType,
            conversation_id: conversationId,
            file_context: fileCtx,
          }),
        });
        const data = await resp.json();
        loadingBubble.remove();

        if (data.success) {
          addChatBubble("assistant", data);
          if (data.usage) {
            const total = (data.usage.input_tokens || 0) + (data.usage.output_tokens || 0);
            snapshot.total_tokens = (snapshot.total_tokens || 0) + total;
            if (data.elapsed_s > 0) lastTokenSpeed = (data.usage.output_tokens || 0) / data.elapsed_s;
            renderHeader();
          }
        } else {
          addChatBubble("assistant", `Error: ${data.error || "Unknown error"}`);
        }
      } catch (e) {
        loadingBubble.remove();
        addChatBubble("assistant", `Request failed: ${e.message}`);
      }

      isRunning = false;
      $btnSend.disabled = false;
      $promptInput.disabled = false;
      $promptInput.focus();
    }
  }

  // --- Model Comparison ---
  async function runComparison() {
    const modelA = $compareModelA.value;
    const modelB = $compareModelB.value;
    const lastUserMsg = $promptInput.value.trim() || getLastUserMessage();

    if (!modelA || !modelB) {
      alert("Select 2 models");
      return;
    }
    if (!lastUserMsg) {
      alert("Type a prompt first or have a previous message");
      return;
    }

    $compareResults.innerHTML = '<div class="empty-state">Running...</div>';
    $btnCompare.disabled = true;

    const providerA = detectProvider(modelA);
    const providerB = detectProvider(modelB);
    const fileCtx = getFileContextText();

    try {
      const [respA, respB] = await Promise.all([
        fetch("/api/prompt", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt: lastUserMsg, model: modelA, provider: providerA, graph_type: "chat", file_context: fileCtx }),
        }).then((r) => r.json()),
        fetch("/api/prompt", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt: lastUserMsg, model: modelB, provider: providerB, graph_type: "chat", file_context: fileCtx }),
        }).then((r) => r.json()),
      ]);

      const speedA = respA.elapsed_s > 0 ? ((respA.usage?.output_tokens || 0) / respA.elapsed_s).toFixed(1) : "-";
      const speedB = respB.elapsed_s > 0 ? ((respB.usage?.output_tokens || 0) / respB.elapsed_s).toFixed(1) : "-";

      $compareResults.innerHTML = `
        <div class="compare-col">
          <div class="compare-model-label">${esc(modelA)}</div>
          <div class="compare-stats">${speedA} tok/s · ${respA.elapsed_s || 0}s</div>
          <div class="compare-output">${formatOutput(respA.output || respA.error || "")}</div>
        </div>
        <div class="compare-col">
          <div class="compare-model-label">${esc(modelB)}</div>
          <div class="compare-stats">${speedB} tok/s · ${respB.elapsed_s || 0}s</div>
          <div class="compare-output">${formatOutput(respB.output || respB.error || "")}</div>
        </div>`;
    } catch (e) {
      $compareResults.innerHTML = `<div class="empty-state">Error: ${esc(e.message)}</div>`;
    } finally {
      $btnCompare.disabled = false;
    }
  }

  function detectProvider(modelName) {
    if (modelName.includes("/")) return "openrouter";
    return "ollama";
  }

  function getLastUserMessage() {
    const bubbles = $chatMessages.querySelectorAll(".chat-bubble.user");
    if (bubbles.length) return bubbles[bubbles.length - 1].textContent;
    return "";
  }

  // --- Auto-select model based on task type ---
  function autoSelectModel(text) {
    const lower = text.toLowerCase();
    const models = allModels.ollama || [];
    if (!models.length) return;

    // Coding tasks -> prefer coder model
    if (/\b(code|function|class|bug|test|refactor|fix|implement)\b/.test(lower)) {
      const coder = models.find((m) => m.name.includes("coder"));
      if (coder) {
        $promptModel.value = coder.name;
        return;
      }
    }

    // Reasoning tasks -> prefer deepseek-r1 or similar
    if (/\b(explain|why|analyze|reason|think|compare|evaluate)\b/.test(lower)) {
      const reasoner = models.find((m) => m.name.includes("deepseek") || m.name.includes("r1"));
      if (reasoner) {
        $promptModel.value = reasoner.name;
        return;
      }
    }
  }

  // --- Interactive Graph ---
  function renderGraph() {
    const g = snapshot.graph;
    if (!g || (!g.nodes.length && !g.edges.length)) {
      $graphCanvas.innerHTML = '<div class="empty-state">Submit a prompt to see the graph</div>';
      return;
    }
    const layers = computeLayers(g.nodes || [], g.edges || []);
    let html = "";
    layers.forEach((layer, idx) => {
      html += '<div class="graph-row">';
      layer.forEach((name) => {
        const state = graphNodeStates[name] || "";
        const display = name === "__start__" ? "START" : name === "__end__" ? "END" : name;
        const special = name === "__start__" ? "start" : name === "__end__" ? "end" : "";
        html += `<div class="gnode ${state} ${special}" onclick="window._showNodeDetail('${esc(name)}')"><div class="gnode-box">${esc(display)}</div></div>`;
      });
      html += "</div>";
      if (idx < layers.length - 1) html += '<div class="graph-connector"><span class="graph-arrow">&darr;</span></div>';
    });
    $graphCanvas.innerHTML = html;
  }

  function computeLayers(nodes, edges) {
    const inDeg = {};
    const adj = {};
    nodes.forEach((n) => { inDeg[n] = 0; adj[n] = []; });
    edges.forEach((e) => {
      const targets = e.target ? [e.target] : e.routes || [];
      targets.forEach((t) => { if (adj[e.source]) adj[e.source].push(t); if (inDeg[t] !== undefined) inDeg[t]++; });
    });
    const layers = [];
    const visited = new Set();
    let queue = nodes.filter((n) => inDeg[n] === 0);
    if (!queue.length && nodes.length) queue = [nodes[0]];
    while (queue.length && visited.size < nodes.length) {
      layers.push([...queue]);
      queue.forEach((n) => visited.add(n));
      const next = [];
      queue.forEach((n) => (adj[n] || []).forEach((t) => { if (!visited.has(t)) { inDeg[t]--; if (inDeg[t] <= 0 && !next.includes(t)) next.push(t); } }));
      queue = next;
    }
    const rem = nodes.filter((n) => !visited.has(n));
    if (rem.length) layers.push(rem);
    return layers;
  }

  window._showNodeDetail = function (name) {
    const nodeEvents = events.filter((e) => e.node_name === name || (e.data && (e.data.node === name || e.data.from === name || e.data.to === name)));
    if (!nodeEvents.length) {
      $detailView.innerHTML = `<div class="detail-node-title">${esc(name)}</div><div class="empty-state">No events yet</div>`;
      return;
    }
    let html = `<div class="detail-node-title">${esc(name)}</div>`;
    nodeEvents.forEach((evt) => {
      html += `<div class="detail-event"><span class="detail-event-time">${formatTime(evt.timestamp)}</span> <span class="detail-event-type">${esc(evt.event_type)}</span><pre class="detail-event-data">${formatJson(evt.data || {})}</pre></div>`;
    });
    $detailView.innerHTML = html;
  };

  // --- Event handling ---
  function handleEvent(evt) {
    events.push(evt);
    if (events.length > MAX_EVENTS) events = events.slice(-MAX_EVENTS);
    updateSnapshotFromEvent(evt);
    renderHeader();
    renderTimelineEvent(evt);
    renderAgentTree();
    renderAgentMessages();

    if (evt.event_type === "graph.start") {
      snapshot.graph = { nodes: evt.data.nodes || [], edges: evt.data.edges || [] };
      graphNodeStates = {};
      renderGraph();
    } else if (evt.event_type === "graph.node.enter") {
      graphNodeStates[evt.node_name || evt.data.node] = "active";
      renderGraph();
    } else if (evt.event_type === "graph.node.exit") {
      graphNodeStates[evt.node_name || evt.data.node] = "done";
      renderGraph();
    } else if (evt.event_type === "graph.end") {
      Object.keys(graphNodeStates).forEach((k) => { if (graphNodeStates[k] === "active") graphNodeStates[k] = "done"; });
      renderGraph();
    }

    if (evt.event_type === "agent.tool_call" && evt.data.tool_name) {
      activeSkills.add(evt.data.tool_name);
      renderAgentTree();
      setTimeout(() => { activeSkills.delete(evt.data.tool_name); renderAgentTree(); }, 3000);
    }
    $timeline.scrollTop = $timeline.scrollHeight;
  }

  function updateSnapshotFromEvent(evt) {
    const t = evt.event_type;
    snapshot.event_count = (snapshot.event_count || 0) + 1;
    if (t === "orchestrator.start") snapshot.orchestrator_status = "running";
    else if (t === "orchestrator.end") snapshot.orchestrator_status = evt.data.success ? "completed" : "failed";
    else if (t === "agent.spawn") {
      snapshot.agents[evt.agent_name] = { name: evt.agent_name, status: "running", steps: 0, tokens: 0, cost_usd: 0, provider: evt.data.provider || "", role: evt.data.role || "", tools: evt.data.tools || [] };
    } else if (t === "agent.step" && snapshot.agents[evt.agent_name]) snapshot.agents[evt.agent_name].steps += 1;
    else if (t === "agent.complete" && snapshot.agents[evt.agent_name]) snapshot.agents[evt.agent_name].status = "completed";
    else if (t === "agent.error" && snapshot.agents[evt.agent_name]) snapshot.agents[evt.agent_name].status = "error";
    else if (t === "cooperation.task_assigned") {
      snapshot.tasks.push({ task_id: evt.data.task_id, from_agent: evt.data.from_agent, to_agent: evt.data.to_agent, description: evt.data.description || "", status: "pending", priority: evt.data.priority || "normal" });
    } else if (t === "cooperation.task_completed") {
      const task = snapshot.tasks.find((tk) => tk.task_id === evt.data.task_id);
      if (task) task.status = evt.data.success ? "completed" : "failed";
    } else if (t === "metrics.cost_update") snapshot.total_cost_usd = evt.data.total_cost_usd || snapshot.total_cost_usd;
    else if (t === "metrics.token_update") {
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
    $tokenSpeed.textContent = lastTokenSpeed > 0 ? `${lastTokenSpeed.toFixed(1)} tok/s` : "- tok/s";
  }

  function renderTimelineEvent(evt) {
    const filter = $filterType.value;
    if (filter !== "all" && !evt.event_type.startsWith(filter)) return;
    const cat = eventCategory(evt.event_type);
    const icons = { agent: "A", graph: "G", cooperation: "C", metrics: "M", orchestrator: "O" };
    const el = document.createElement("div");
    el.className = "event-item";
    el.innerHTML = `<span class="event-time">${formatTime(evt.timestamp)}</span><span class="event-icon ${cat}">${icons[cat] || "?"}</span><div class="event-body"><div class="event-type">${esc(evt.event_type)}</div><div class="event-desc">${esc(eventDesc(evt))}</div></div>`;
    el.addEventListener("click", () => { $detailView.innerHTML = `<pre>${formatJson(evt)}</pre>`; });
    $timeline.appendChild(el);
  }

  function eventCategory(type) {
    if (type.startsWith("agent")) return "agent";
    if (type.startsWith("graph")) return "graph";
    if (type.startsWith("cooperation")) return "cooperation";
    if (type.startsWith("metrics")) return "metrics";
    return "orchestrator";
  }

  function eventDesc(evt) {
    const d = evt.data || {};
    const a = evt.agent_name ? `[${evt.agent_name}] ` : "";
    switch (evt.event_type) {
      case "agent.spawn": return `${a}spawned`;
      case "agent.complete": return `${a}completed`;
      case "agent.error": return `${a}error`;
      case "graph.start": return `graph started (${(d.nodes || []).length} nodes)`;
      case "graph.end": return `graph ended ${d.elapsed_s || 0}s`;
      case "graph.node.enter": return `entering ${evt.node_name || ""}`;
      case "graph.node.exit": return `exited ${evt.node_name || ""}`;
      default: return JSON.stringify(d).slice(0, 80);
    }
  }

  // --- Helpers ---
  function formatTime(ts) {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }
  function formatNumber(n) {
    if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
    return String(n);
  }
  function formatSize(bytes) {
    if (bytes > 1e6) return (bytes / 1e6).toFixed(1) + "MB";
    if (bytes > 1e3) return (bytes / 1e3).toFixed(1) + "KB";
    return bytes + "B";
  }
  function truncate(s, max) { return !s ? "" : s.length > max ? s.slice(0, max) + "..." : s; }
  function esc(s) { if (!s) return ""; const d = document.createElement("div"); d.textContent = String(s); return d.innerHTML; }
  function formatJson(obj) {
    return JSON.stringify(obj, null, 2).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"([^"]+)":/g, '<span class="detail-key">"$1"</span>:');
  }

  // --- Event Listeners ---
  $filterType.addEventListener("change", () => { $timeline.innerHTML = ""; events.forEach((e) => renderTimelineEvent(e)); });
  $btnClear.addEventListener("click", () => { $timeline.innerHTML = ""; events = []; });
  $btnSend.addEventListener("click", sendPrompt);
  $promptInput.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendPrompt(); } });
  $promptInput.addEventListener("input", () => {
    $promptInput.style.height = "auto";
    $promptInput.style.height = Math.min($promptInput.scrollHeight, 120) + "px";
    // Auto-select model based on content
    if ($promptProvider.value === "ollama") autoSelectModel($promptInput.value);
  });
  $promptProvider.addEventListener("change", updateModelSelector);
  $btnAttach.addEventListener("click", openFilePicker);
  $btnClearCtx.addEventListener("click", () => { attachedFiles = []; renderAttachedFiles(); });
  $btnClosePicker.addEventListener("click", () => $filePickerModal.classList.add("hidden"));
  $btnNewChat.addEventListener("click", startNewConversation);
  $btnOllamaPull.addEventListener("click", pullModel);
  $ollamaPullInput.addEventListener("keydown", (e) => { if (e.key === "Enter") pullModel(); });
  $btnCompare.addEventListener("click", runComparison);

  // --- Streaming WebSocket message handler ---
  function setupStreamHandler() {
    if (!streamWs) return;
    streamWs.onmessage = (msg) => {
      const data = JSON.parse(msg.data);
      if (data.type === "token") {
        appendToStream(data.content);
      } else if (data.type === "done") {
        finalizeStream(data);
        if (data.speed) lastTokenSpeed = data.speed;
        if (data.usage) {
          snapshot.total_tokens = (snapshot.total_tokens || 0) + (data.usage.output_tokens || 0);
        }
        renderHeader();
        isRunning = false;
        $btnSend.disabled = false;
        $promptInput.disabled = false;
        $promptInput.focus();
      } else if (data.type === "error") {
        const bubble = $("streaming-bubble");
        if (bubble) { bubble.remove(); }
        addChatBubble("assistant", `Error: ${data.error}`);
        isRunning = false;
        $btnSend.disabled = false;
        $promptInput.disabled = false;
      } else if (data.type === "start") {
        // streaming started
      }
    };
  }

  // --- Init ---
  loadModels();
  loadAgents();
  loadPresets();
  startNewConversation();

  fetch("/api/events?limit=200")
    .then((r) => r.json())
    .then((data) => { events = data; data.forEach((e) => renderTimelineEvent(e)); })
    .catch(() => {})
    .finally(() => connect());

  fetch("/api/snapshot")
    .then((r) => r.json())
    .then((data) => { snapshot = data; renderHeader(); renderGraph(); renderAgentMessages(); })
    .catch(() => {});

  // Connect streaming WebSocket with retry
  function initStream() {
    connectStream();
    // Wait for connection then setup handler
    const check = setInterval(() => {
      if (streamWs && streamWs.readyState === WebSocket.OPEN) {
        clearInterval(check);
        setupStreamHandler();
      }
    }, 200);
  }
  initStream();

  // Re-setup stream handler on reconnect
  const origConnectStream = connectStream;
  connectStream = function () {
    origConnectStream();
    setTimeout(() => {
      const check = setInterval(() => {
        if (streamWs && streamWs.readyState === WebSocket.OPEN) {
          clearInterval(check);
          setupStreamHandler();
        }
      }, 200);
    }, 100);
  };
})();
