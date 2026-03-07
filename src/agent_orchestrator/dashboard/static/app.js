/** Agent Orchestrator Dashboard v0.3.0
 * Unified interface: Multi-Agent / Single Agent / Simple Prompt
 * Default: Multi-Agent + Cloud (OpenRouter)
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
    cache: { hits: 0, misses: 0, hit_rate: 0, evictions: 0 },
    event_count: 0,
  };
  let agentRegistry = null;
  let events = [];
  let isRunning = false;
  let activeSkills = new Set();
  let graphNodeStates = {};
  let lastTokenSpeed = 0;
  let conversationId = null;
  let attachedFiles = [];
  let allModels = { ollama: [], openrouter: [] };
  const MAX_EVENTS = 500;

  // --- DOM refs ---
  const $ = (id) => document.getElementById(id);
  const $status = $("status-badge");
  const $tokens = $("total-tokens");
  const $cost = $("total-cost");
  const $tokenSpeed = $("token-speed");
  const $wsIndicator = $("ws-indicator");
  const $agentBadges = $("agent-badges");
  const $agentMessages = $("agent-messages");
  const $graphCanvas = $("graph-canvas");
  const $chatMessages = $("chat-messages");
  const $promptInput = $("prompt-input");
  const $btnSend = $("btn-send");
  const $execMode = $("exec-mode");
  const $promptModel = $("prompt-model");
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
  const $btnResetGraph = $("btn-reset-graph");
  const $btnToggleSidebar = $("btn-toggle-sidebar");
  const $sidebar = $("sidebar");
  const $agentActivity = $("agent-activity");
  const $cacheHitRate = $("cache-hit-rate");
  const $cacheHits = $("cache-hits");
  const $cacheMisses = $("cache-misses");
  const $cacheEvictions = $("cache-evictions");
  const $cacheRate = $("cache-rate");
  const $cacheBarFill = $("cache-bar-fill");
  const $cacheLog = $("cache-log");

  // --- Event WebSocket ---
  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws`);
    ws.onopen = () => { $wsIndicator.className = "ws-dot connected"; };
    ws.onclose = () => { $wsIndicator.className = "ws-dot disconnected"; setTimeout(connect, 2000); };
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
    const models = (provider === "openrouter" ? allModels.openrouter : allModels.ollama) || [];
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

  // --- Agent Badges (compact, in graph header) ---
  function renderAgentBadges() {
    if (!agentRegistry || !agentRegistry.agents) {
      $agentBadges.innerHTML = "";
      return;
    }
    $agentBadges.innerHTML = agentRegistry.agents.map((a) => {
      const status = getAgentStatus(a.name);
      return `<span class="agent-badge ${status}"><span class="agent-dot"></span>${esc(a.name)}</span>`;
    }).join("");
  }

  async function loadAgents() {
    try {
      const resp = await fetch("/api/agents");
      agentRegistry = await resp.json();
      renderAgentBadges();
    } catch (e) {
      agentRegistry = null;
    }
  }

  function getAgentStatus(name) {
    const a = snapshot.agents[name];
    return a ? a.status || "idle" : "idle";
  }

  // --- Inter-Agent Messages ---
  function renderAgentMessages() {
    const tasks = snapshot.tasks || [];
    if (!tasks.length) {
      $agentMessages.innerHTML = "";
      return;
    }
    $agentMessages.innerHTML = tasks.slice(-10).map((t) => `
      <div class="agent-msg">
        <span class="agent-msg-from">${esc(t.from_agent || "?")}</span>
        <span class="agent-msg-arrow">&rarr;</span>
        <span class="agent-msg-to">${esc(t.to_agent || "?")}</span>
        <span class="agent-msg-text">${esc(truncate(t.description, 40))}</span>
        <span class="agent-msg-status ${t.status || "pending"}"></span>
      </div>`).join("");
  }

  // --- Ollama Model Management ---
  function renderOllamaModelList() {
    const models = allModels.ollama || [];
    if (!models.length) {
      $ollamaModelList.innerHTML = '<div class="empty-state">No local models</div>';
      return;
    }
    $ollamaModelList.innerHTML = models.map((m) => `
      <div class="ollama-model-item">
        <span class="ollama-model-name">${esc(m.name)}</span>
        <span class="ollama-model-size">${esc(m.size)}</span>
        <button class="btn-delete-model" onclick="window._deleteModel('${esc(m.name)}')" title="Delete">&times;</button>
      </div>`).join("");
  }

  window._deleteModel = async function (name) {
    if (!confirm(`Delete model ${name}?`)) return;
    try {
      await fetch("/api/ollama/model", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
      await loadModels();
    } catch (e) { alert("Failed: " + e.message); }
  };

  async function pullModel() {
    const name = $ollamaPullInput.value.trim();
    if (!name) return;
    $btnOllamaPull.disabled = true;
    $btnOllamaPull.textContent = "Pulling...";
    try {
      const resp = await fetch("/api/ollama/pull", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
      const data = await resp.json();
      if (data.success) { $ollamaPullInput.value = ""; await loadModels(); }
      else alert("Pull failed: " + (data.error || "unknown"));
    } catch (e) { alert("Pull failed: " + e.message); }
    finally { $btnOllamaPull.disabled = false; $btnOllamaPull.textContent = "Pull"; }
  }

  // --- Presets ---
  async function loadPresets() {
    try {
      const resp = await fetch("/api/presets");
      const data = await resp.json();
      $presetsBar.innerHTML = (data.presets || []).map((p) =>
        `<button class="preset-btn" data-prompt="${esc(p.prompt)}" data-graph="${esc(p.graph)}" title="${esc(p.label)}">
          <span class="preset-icon">${esc(p.icon)}</span><span>${esc(p.label)}</span>
        </button>`
      ).join("");

      $presetsBar.querySelectorAll(".preset-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          const ctx = getFileContextText();
          if (!ctx) { alert("Attach a file first, then click a preset."); return; }
          $promptInput.value = btn.dataset.prompt.replace("{context}", ctx);
          $promptInput.dispatchEvent(new Event("input"));
        });
      });
    } catch (e) { /* ignore */ }
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

      const parts = path ? path.split("/") : [];
      let crumbs = `<span class="crumb" data-path="">root</span>`;
      let acc = "";
      parts.forEach((p) => { acc += (acc ? "/" : "") + p; crumbs += ` / <span class="crumb" data-path="${esc(acc)}">${esc(p)}</span>`; });
      $fileBreadcrumb.innerHTML = crumbs;
      $fileBreadcrumb.querySelectorAll(".crumb").forEach((c) => c.addEventListener("click", () => loadDirectory(c.dataset.path)));

      $fileList.innerHTML = (data.items || []).map((item) => {
        const icon = item.is_dir ? "📁" : "📄";
        const sizeStr = item.is_dir ? "" : `<span class="file-size">${formatSize(item.size)}</span>`;
        return `<div class="file-item ${item.is_dir ? "dir" : "file"}" data-path="${esc(item.path)}" data-isdir="${item.is_dir}">
          <span class="file-icon">${icon}</span><span class="file-name">${esc(item.name)}</span>${sizeStr}
        </div>`;
      }).join("");

      $fileList.querySelectorAll(".file-item").forEach((el) => {
        el.addEventListener("click", () => {
          if (el.dataset.isdir === "true") loadDirectory(el.dataset.path);
          else { attachFile(el.dataset.path); $filePickerModal.classList.add("hidden"); }
        });
      });
    } catch (e) { $fileList.innerHTML = `<div class="empty-state">Error: ${esc(e.message)}</div>`; }
  }

  async function attachFile(path) {
    if (attachedFiles.find((f) => f.path === path)) return;
    try {
      const resp = await fetch(`/api/file?path=${encodeURIComponent(path)}`);
      const data = await resp.json();
      if (data.error) { alert(data.error); return; }
      attachedFiles.push({ path: data.path, content: data.content });
      renderAttachedFiles();
    } catch (e) { alert("Failed to read file: " + e.message); }
  }

  function renderAttachedFiles() {
    if (!attachedFiles.length) { $attachedFiles.innerHTML = ""; return; }
    $attachedFiles.innerHTML = attachedFiles.map((f, i) =>
      `<span class="attached-file"><span class="attached-file-name">${esc(f.path)}</span><button class="btn-remove-file" onclick="window._removeFile(${i})">&times;</button></span>`
    ).join("");
  }

  window._removeFile = function (idx) { attachedFiles.splice(idx, 1); renderAttachedFiles(); };

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
    } catch (e) { /* ignore */ }
  }

  function addSystemBubble(text) {
    const el = document.createElement("div");
    el.className = "chat-bubble system";
    el.textContent = text;
    $chatMessages.appendChild(el);
    $chatMessages.scrollTop = $chatMessages.scrollHeight;
  }

  // --- Chat Bubbles ---
  function addChatBubble(role, content) {
    const bubble = document.createElement("div");
    bubble.className = `chat-bubble ${role}`;

    if (role === "assistant" && typeof content === "object") {
      let html = "";
      const steps = content.steps || [];
      if (steps.length) {
        steps.forEach((step) => {
          html += `<div class="chat-step"><span class="chat-step-label">${esc(step.node)}</span><div class="chat-step-text md-content">${renderMarkdown(step.output || "")}</div></div>`;
        });
      } else if (content.output) {
        html = `<div class="chat-step-text md-content">${renderMarkdown(content.output)}</div>`;
      }
      if (content.usage || content.elapsed_s) {
        const elapsed = content.elapsed_s || 0;
        const outTok = (content.usage && content.usage.output_tokens) || 0;
        const speed = elapsed > 0 ? (outTok / elapsed).toFixed(1) : 0;
        const model = (content.usage && content.usage.model) || "";
        html += `<div class="chat-usage">${outTok} tok &middot; ${speed} tok/s &middot; ${esc(model)} &middot; ${elapsed}s</div>`;
      }
      bubble.innerHTML = html;
    } else if (role === "assistant") {
      const text = typeof content === "string" ? content : JSON.stringify(content);
      bubble.innerHTML = `<div class="md-content">${renderMarkdown(text)}</div>`;
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
    const rawText = bubble.textContent || "";
    bubble.innerHTML = `<div class="md-content">${renderMarkdown(rawText)}</div>`;
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

  // --- Markdown Renderer ---
  function renderMarkdown(text) {
    if (!text) return "";
    let html = esc(text);

    // Code blocks (```lang\n...\n```)
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, function(_, lang, code) {
      return '<pre class="md-code-block"><code>' + code.replace(/<br>/g, "\n") + '</code></pre>';
    });

    // Inline code
    html = html.replace(/`([^`\n]+)`/g, '<code class="md-inline-code">$1</code>');

    // Headers
    html = html.replace(/^### (.+)$/gm, '<strong class="md-h3">$1</strong>');
    html = html.replace(/^## (.+)$/gm, '<strong class="md-h2">$1</strong>');
    html = html.replace(/^# (.+)$/gm, '<strong class="md-h1">$1</strong>');

    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/__(.+?)__/g, '<strong>$1</strong>');

    // Italic
    html = html.replace(/(?<!\w)\*([^*\n]+)\*(?!\w)/g, '<em>$1</em>');
    html = html.replace(/(?<!\w)_([^_\n]+)_(?!\w)/g, '<em>$1</em>');

    // Unordered lists
    html = html.replace(/^[\-\*] (.+)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>[\s\S]*?<\/li>)/g, '<ul>$1</ul>');
    html = html.replace(/<\/ul>\s*<ul>/g, '');

    // Ordered lists
    html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');

    // Links
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

    // Line breaks
    html = html.replace(/\n/g, "<br>");
    // Fix <br> inside pre blocks
    html = html.replace(/<pre class="md-code-block"><code>([\s\S]*?)<\/code><\/pre>/g, function(_, code) {
      return '<pre class="md-code-block"><code>' + code.replace(/<br>/g, "\n") + '</code></pre>';
    });

    return html;
  }

  function formatOutput(text) { return renderMarkdown(text); }

  // --- Unified Send ---
  async function sendMessage() {
    const text = $promptInput.value.trim();
    if (!text || isRunning) return;

    const mode = $execMode.value;
    const model = $promptModel.value;
    const provider = $promptProvider.value;
    const useStreaming = $toggleStream.checked;

    if (!model) { alert("No model selected."); return; }

    isRunning = true;
    $btnSend.disabled = true;
    $promptInput.disabled = true;
    $promptInput.value = "";
    $promptInput.style.height = "auto";

    addChatBubble("user", text);
    graphNodeStates = {};

    const fileCtx = getFileContextText();

    if (mode === "multi-agent") {
      // Multi-Agent: real team with tool-wielding sub-agents
      await runTeam(text, model, provider);
    } else if (mode === "agent") {
      // Single Agent: use agent runner
      await runSingleAgent(text, model, provider);
    } else {
      // Simple Prompt: streaming or graph
      if (useStreaming && streamWs && streamWs.readyState === WebSocket.OPEN) {
        addStreamingBubble();
        streamWs.send(JSON.stringify({
          prompt: text, model, provider,
          conversation_id: conversationId,
          file_context: fileCtx,
        }));
        // Handler in setupStreamHandler deals with the rest
        return; // Don't reset isRunning here — stream handler does it
      }
      await runGraphPrompt(text, model, provider, "chat", fileCtx);
    }
  }

  async function runGraphPrompt(text, model, provider, graphType, fileCtx) {
    const loadingBubble = document.createElement("div");
    loadingBubble.className = "chat-bubble assistant loading";
    loadingBubble.innerHTML = '<div class="chat-spinner"></div><span>Running...</span>';
    $chatMessages.appendChild(loadingBubble);
    $chatMessages.scrollTop = $chatMessages.scrollHeight;

    // Build full prompt
    let fullPrompt = text;
    if (fileCtx) fullPrompt = `${text}\n\n\`\`\`\n${fileCtx}\n\`\`\``;

    try {
      const resp = await fetch("/api/prompt", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: fullPrompt, model, provider, graph_type: graphType,
          conversation_id: conversationId, file_context: fileCtx,
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

  async function runTeam(text, model, provider) {
    addSystemBubble("Running multi-agent team (team-lead → backend-dev + frontend-dev)...");

    try {
      const resp = await fetch("/api/team/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task: text, model, provider }),
      });
      const data = await resp.json();

      if (data.success) {
        // Build steps from team outputs
        const steps = [];
        if (data.plan) steps.push({ node: "team-lead (plan)", output: data.plan });
        const outputs = data.agent_outputs || {};
        for (const [agent, output] of Object.entries(outputs)) {
          steps.push({ node: agent, output: output });
        }
        steps.push({ node: "team-lead (summary)", output: data.output });

        addChatBubble("assistant", {
          steps,
          usage: { output_tokens: data.total_tokens, model },
          elapsed_s: data.elapsed_s,
        });
      } else {
        addChatBubble("assistant", `Team error: ${data.error || "Unknown error"}`);
      }
    } catch (e) {
      addChatBubble("assistant", `Team run failed: ${e.message}`);
    }

    isRunning = false;
    $btnSend.disabled = false;
    $promptInput.disabled = false;
    $promptInput.focus();
  }

  async function runSingleAgent(text, model, provider) {
    addSystemBubble("Running single agent...");

    try {
      const resp = await fetch("/api/agent/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          agent: "team-lead", task: text, model, provider,
        }),
      });
      const data = await resp.json();

      if (data.success) {
        addChatBubble("assistant", {
          steps: [{ node: "agent", output: data.output }],
          usage: { output_tokens: data.total_tokens, model },
          elapsed_s: data.elapsed_s,
        });
      } else {
        addChatBubble("assistant", `Agent ${data.status}: ${data.error || data.output || "Failed"}`);
      }
    } catch (e) {
      addChatBubble("assistant", `Agent run failed: ${e.message}`);
    }

    isRunning = false;
    $btnSend.disabled = false;
    $promptInput.disabled = false;
    $promptInput.focus();
  }

  // --- Model Comparison ---
  async function runComparison() {
    const modelA = $compareModelA.value;
    const modelB = $compareModelB.value;
    const lastUserMsg = getLastUserMessage();

    if (!modelA || !modelB) { alert("Select 2 models"); return; }
    if (!lastUserMsg) { alert("Send a message first"); return; }

    $compareResults.innerHTML = '<div class="empty-state">Running...</div>';
    $btnCompare.disabled = true;

    const fileCtx = getFileContextText();

    try {
      const [respA, respB] = await Promise.all([
        fetch("/api/prompt", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt: lastUserMsg, model: modelA, provider: detectProvider(modelA), graph_type: "chat", file_context: fileCtx }) }).then(r => r.json()),
        fetch("/api/prompt", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt: lastUserMsg, model: modelB, provider: detectProvider(modelB), graph_type: "chat", file_context: fileCtx }) }).then(r => r.json()),
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
    } finally { $btnCompare.disabled = false; }
  }

  function detectProvider(modelName) { return modelName.includes("/") ? "openrouter" : "ollama"; }
  function getLastUserMessage() {
    const bubbles = $chatMessages.querySelectorAll(".chat-bubble.user");
    return bubbles.length ? bubbles[bubbles.length - 1].textContent : "";
  }

  // --- Interactive Graph ---
  function renderGraph() {
    const g = snapshot.graph;
    if (!g || (!g.nodes.length && !g.edges.length)) {
      $graphCanvas.innerHTML = '<div class="empty-state">Send a message to see the agent graph</div>';
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
        const isReplayable = !special && state === "done";
        html += `<div class="gnode ${state} ${special}" onclick="window._showNodeDetail('${esc(name)}')">`;
        html += `<div class="gnode-box">${esc(display)}</div>`;
        if (isReplayable) {
          html += `<div class="gnode-actions"><button class="btn-replay" onclick="event.stopPropagation(); window._replayNode('${esc(name)}')">replay</button></div>`;
        }
        html += `</div>`;
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
    // Open sidebar if hidden
    if ($sidebar.classList.contains("hidden")) toggleSidebar();
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

  // --- Replay Node ---
  window._replayNode = async function (name) {
    if (isRunning) return;
    isRunning = true;
    graphNodeStates[name] = "active";
    renderGraph();
    addSystemBubble(`Replaying node: ${name}...`);

    try {
      const resp = await fetch("/api/graph/replay", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ node: name }) });
      const data = await resp.json();
      graphNodeStates[name] = "done";
      renderGraph();
      if (data.success) {
        addChatBubble("assistant", { steps: [{ node: `${name} (replay)`, output: data.output }], elapsed_s: data.elapsed_s });
      } else {
        addChatBubble("assistant", `Replay error: ${data.error || "Unknown"}`);
      }
    } catch (e) { graphNodeStates[name] = "done"; renderGraph(); addChatBubble("assistant", `Replay failed: ${e.message}`); }
    isRunning = false;
  };

  // --- Reset Graph ---
  async function resetGraph() {
    try {
      await fetch("/api/graph/reset", { method: "POST" });
      snapshot.agents = {}; snapshot.tasks = []; snapshot.orchestrator_status = "idle";
      snapshot.graph = { nodes: [], edges: [] }; snapshot.total_tokens = 0; snapshot.total_cost_usd = 0;
      graphNodeStates = {}; events = []; activityCount = 0;
      $timeline.innerHTML = "";
      $agentActivity.innerHTML = '<div class="empty-state">Waiting for agents...</div>';
      renderHeader(); renderGraph(); renderAgentBadges(); renderAgentMessages();
      $detailView.innerHTML = '<div class="empty-state">Click a graph node or event</div>';
      addSystemBubble("Reset");
    } catch (e) { alert("Reset failed: " + e.message); }
  }

  // --- Toggle Sidebar ---
  function toggleSidebar() {
    $sidebar.classList.toggle("hidden");
    $btnToggleSidebar.classList.toggle("active");
  }

  // --- Agent Activity Panel ---
  let activityCount = 0;

  function addActivityItem(category, agent, desc, detail) {
    // Clear placeholder
    if (activityCount === 0) $agentActivity.innerHTML = "";
    activityCount++;

    const icons = { spawn: "S", step: "#", tool: "T", task: "D", complete: "✓", error: "!" };
    const el = document.createElement("div");
    el.className = `activity-item ${category}`;
    el.innerHTML = `
      <span class="activity-time">${new Date().toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
      <span class="activity-icon ${category}">${icons[category] || "·"}</span>
      <div class="activity-body">
        <span class="activity-agent">${esc(agent)}</span>
        <div class="activity-desc">${esc(desc)}</div>
        ${detail ? `<div class="activity-detail">${esc(detail)}</div>` : ""}
      </div>`;
    $agentActivity.appendChild(el);
    $agentActivity.scrollTop = $agentActivity.scrollHeight;
  }

  function routeToActivity(evt) {
    const t = evt.event_type;
    const d = evt.data || {};
    const agent = evt.agent_name || "";

    if (t === "agent.spawn") {
      addActivityItem("spawn", agent, "Agent spawned", d.provider || "");
    } else if (t === "agent.step") {
      addActivityItem("step", agent, `Step ${d.step || ""}`, d.model || "");
    } else if (t === "agent.tool_call") {
      const args = Object.entries(d.arguments || {}).map(([k, v]) => `${k}=${truncate(String(v), 40)}`).join(", ");
      addActivityItem("tool", agent, `→ ${d.tool_name || "tool"}`, args);
    } else if (t === "agent.tool_result") {
      const status = d.success ? "✓" : "✗";
      addActivityItem("tool", agent, `← ${d.tool_name || "tool"} ${status}`, truncate(d.output || "", 60));
    } else if (t === "cooperation.task_assigned") {
      addActivityItem("task", d.from_agent || "?", `Delegated to ${d.to_agent || "?"}`, truncate(d.description || "", 50));
    } else if (t === "cooperation.task_completed") {
      const status = d.success ? "completed" : "failed";
      addActivityItem(d.success ? "complete" : "error", d.from_agent || "?", `Task ${status}`, truncate(d.summary || "", 50));
    } else if (t === "agent.complete") {
      addActivityItem("complete", agent, "Completed", truncate(d.output || "", 50));
    } else if (t === "agent.error" || t === "agent.stalled") {
      addActivityItem("error", agent, d.error || "Error", "");
    }
  }

  // --- Event handling ---
  function handleEvent(evt) {
    events.push(evt);
    if (events.length > MAX_EVENTS) events = events.slice(-MAX_EVENTS);
    updateSnapshotFromEvent(evt);
    renderHeader();
    renderTimelineEvent(evt);
    renderAgentBadges();
    renderAgentMessages();

    // Feed agent activity panel
    routeToActivity(evt);

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
      renderAgentBadges();
      setTimeout(() => { activeSkills.delete(evt.data.tool_name); renderAgentBadges(); }, 3000);
      const tcEl = document.createElement("div");
      tcEl.className = "chat-bubble tool-call";
      tcEl.innerHTML = `<div class="tool-call-header">${esc(evt.agent_name)} &rarr; ${esc(evt.data.tool_name)}</div><pre class="tool-call-args">${esc(JSON.stringify(evt.data.arguments || {}, null, 2))}</pre>`;
      tcEl.id = `tc-${evt.data.tool_call_id || ""}`;
      $chatMessages.appendChild(tcEl);
      $chatMessages.scrollTop = $chatMessages.scrollHeight;
    }
    if (evt.event_type === "agent.tool_result" && evt.data.tool_call_id) {
      const tcEl = $(`tc-${evt.data.tool_call_id}`);
      if (tcEl) {
        const resEl = document.createElement("div");
        resEl.className = `tool-call-result ${evt.data.success ? "success" : "error"}`;
        resEl.textContent = truncate(evt.data.output || "", 300);
        tcEl.appendChild(resEl);
        $chatMessages.scrollTop = $chatMessages.scrollHeight;
      }
    }
    // Cache events
    if (evt.event_type === "cache.hit" || evt.event_type === "cache.miss") {
      renderCachePanel();
      appendCacheLog(evt);
    } else if (evt.event_type === "cache.stats") {
      renderCachePanel();
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
    else if (t === "agent.stalled" && snapshot.agents[evt.agent_name]) snapshot.agents[evt.agent_name].status = "error";
    else if (t === "cooperation.task_assigned") {
      snapshot.tasks.push({ task_id: evt.data.task_id, from_agent: evt.data.from_agent, to_agent: evt.data.to_agent, description: evt.data.description || "", status: "pending", priority: evt.data.priority || "normal" });
    } else if (t === "cooperation.task_completed") {
      const task = snapshot.tasks.find((tk) => tk.task_id === evt.data.task_id);
      if (task) task.status = evt.data.success ? "completed" : "failed";
    } else if (t === "cache.hit") {
      snapshot.cache.hits = (snapshot.cache.hits || 0) + 1;
      updateCacheRate();
    } else if (t === "cache.miss") {
      snapshot.cache.misses = (snapshot.cache.misses || 0) + 1;
      updateCacheRate();
    } else if (t === "cache.stats") {
      snapshot.cache = evt.data.cache_stats || snapshot.cache;
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
    renderCachePanel();
  }

  function updateCacheRate() {
    const c = snapshot.cache;
    const total = (c.hits || 0) + (c.misses || 0);
    c.hit_rate = total > 0 ? c.hits / total : 0;
  }

  function renderCachePanel() {
    const c = snapshot.cache || {};
    const rate = ((c.hit_rate || 0) * 100).toFixed(1);
    if ($cacheHits) $cacheHits.textContent = formatNumber(c.hits || 0);
    if ($cacheMisses) $cacheMisses.textContent = formatNumber(c.misses || 0);
    if ($cacheEvictions) $cacheEvictions.textContent = formatNumber(c.evictions || 0);
    if ($cacheRate) $cacheRate.textContent = rate + "%";
    if ($cacheBarFill) $cacheBarFill.style.width = rate + "%";
    if ($cacheHitRate) {
      const total = (c.hits || 0) + (c.misses || 0);
      $cacheHitRate.textContent = total > 0 ? rate + "%" : "-";
    }
  }

  function appendCacheLog(evt) {
    if (!$cacheLog) return;
    const isHit = evt.event_type === "cache.hit";
    const el = document.createElement("div");
    el.className = `cache-log-entry ${isHit ? "hit" : "miss"}`;
    const node = evt.data.node_name || evt.data.key?.slice(0, 12) || "";
    el.innerHTML = `<span class="cache-log-icon">${isHit ? "HIT" : "MISS"}</span> <span class="cache-log-node">${esc(node)}</span> <span class="cache-log-time">${formatTime(evt.timestamp)}</span>`;
    $cacheLog.appendChild(el);
    if ($cacheLog.children.length > 50) $cacheLog.removeChild($cacheLog.firstChild);
    $cacheLog.scrollTop = $cacheLog.scrollHeight;
  }

  function renderTimelineEvent(evt) {
    const filter = $filterType.value;
    if (filter !== "all" && !evt.event_type.startsWith(filter)) return;
    const cat = eventCategory(evt.event_type);
    const icons = { agent: "A", graph: "G", cooperation: "C", cache: "$", metrics: "M", orchestrator: "O" };
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
    if (type.startsWith("cache")) return "cache";
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
      case "cache.hit": return `cache hit${d.node_name ? ` [${d.node_name}]` : ""}`;
      case "cache.miss": return `cache miss${d.node_name ? ` [${d.node_name}]` : ""}`;
      case "cache.stats": return `hit rate: ${((d.cache_stats?.hit_rate || 0) * 100).toFixed(1)}%`;
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
  $btnSend.addEventListener("click", sendMessage);
  $promptInput.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); } });
  $promptInput.addEventListener("input", () => {
    $promptInput.style.height = "auto";
    $promptInput.style.height = Math.min($promptInput.scrollHeight, 120) + "px";
  });
  $promptProvider.addEventListener("change", updateModelSelector);
  $execMode.addEventListener("change", updateStreamToggle);
  function updateStreamToggle() {
    const show = $execMode.value === "prompt";
    document.querySelector(".stream-toggle").style.display = show ? "" : "none";
  }
  updateStreamToggle();
  $btnAttach.addEventListener("click", openFilePicker);
  $btnClearCtx.addEventListener("click", () => { attachedFiles = []; renderAttachedFiles(); });
  $btnClosePicker.addEventListener("click", () => $filePickerModal.classList.add("hidden"));
  $btnNewChat.addEventListener("click", startNewConversation);
  $btnOllamaPull.addEventListener("click", pullModel);
  $ollamaPullInput.addEventListener("keydown", (e) => { if (e.key === "Enter") pullModel(); });
  $btnCompare.addEventListener("click", runComparison);
  $btnResetGraph.addEventListener("click", resetGraph);
  $btnToggleSidebar.addEventListener("click", toggleSidebar);

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
        if (data.usage) snapshot.total_tokens = (snapshot.total_tokens || 0) + (data.usage.output_tokens || 0);
        renderHeader();
        isRunning = false;
        $btnSend.disabled = false;
        $promptInput.disabled = false;
        $promptInput.focus();
      } else if (data.type === "error") {
        const bubble = $("streaming-bubble");
        if (bubble) bubble.remove();
        addChatBubble("assistant", `Error: ${data.error}`);
        isRunning = false;
        $btnSend.disabled = false;
        $promptInput.disabled = false;
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

  function initStream() {
    connectStream();
    const check = setInterval(() => {
      if (streamWs && streamWs.readyState === WebSocket.OPEN) { clearInterval(check); setupStreamHandler(); }
    }, 200);
  }
  initStream();

  const origConnectStream = connectStream;
  connectStream = function () {
    origConnectStream();
    setTimeout(() => {
      const check = setInterval(() => {
        if (streamWs && streamWs.readyState === WebSocket.OPEN) { clearInterval(check); setupStreamHandler(); }
      }, 200);
    }, 100);
  };
})();
