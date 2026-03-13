/** Agent Orchestrator Dashboard v0.3.0
 * Unified interface: Multi-Agent / Single Agent / Simple Prompt
 * Default: Multi-Agent + Cloud (OpenRouter)
 */

(function () {
  "use strict";

  // --- State ---
  let ws = null;
  let streamWs = null;
  /** Safely set a property on an object, preventing prototype pollution. */
  function safeSet(obj, key, value) {
    if (typeof key !== "string" || key === "__proto__" || key === "constructor" || key === "prototype") return;
    Object.defineProperty(obj, key, { value: value, writable: true, enumerable: true, configurable: true });
  }
  /** Safely get a property from an object, preventing prototype pollution reads. */
  function safeGet(obj, key) {
    if (key === "__proto__" || key === "constructor" || key === "prototype") return undefined;
    return Object.prototype.hasOwnProperty.call(obj, key) ? obj[key] : undefined;
  }
  let snapshot = {
    orchestrator_status: "idle",
    agents: Object.create(null),
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
  let graphNodeStates = Object.create(null);
  let lastTokenSpeed = 0;
  let conversationId = null;
  let attachedFiles = [];
  let allModels = { ollama: [], openrouter: [] };
  const MAX_EVENTS = 500;
  let cumulativeUsage = null;

  // --- Running state management ---
  function setRunning(running) {
    isRunning = running;
    $btnSend.disabled = running;
    $promptInput.disabled = running;
    if (running) {
      snapshot.orchestrator_status = "running";
    } else {
      snapshot.orchestrator_status = "completed";
      $promptInput.focus();
      // Refresh cumulative stats after task completes
      fetchUsageStats();
      // Auto-reset to idle after 3s
      setTimeout(() => {
        if (!isRunning) { snapshot.orchestrator_status = "idle"; renderHeader(); }
      }, 3000);
    }
    renderHeader();
  }

  // --- DOM refs ---
  const $ = (id) => document.getElementById(id);
  const $status = $("status-badge");
  const $tokens = $("total-tokens");
  const $cost = $("total-cost");
  const $tokenSpeed = $("token-speed");
  const $wsIndicator = $("ws-indicator");
  const $agentBadges = $("agent-badges");
  const $graphCanvas = $("graph-canvas");
  const $graphSvg = $("graph-svg");
  const $graphEmpty = $("graph-empty");
  const $interactionTimeline = $("interaction-timeline");
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
  const $cumulTokens = $("cumul-tokens");
  const $cumulCost = $("cumul-cost");
  const $cumulRequests = $("cumul-requests");
  const $cumulSpeed = $("cumul-speed");
  const $dbIndicator = $("db-indicator");
  const $cacheHitRate = $("cache-hit-rate");
  const $cacheHits = $("cache-hits");
  const $cacheMisses = $("cache-misses");
  const $cacheEvictions = $("cache-evictions");
  const $cacheRate = $("cache-rate");
  const $cacheBarFill = $("cache-bar-fill");
  const $cacheLog = $("cache-log");
  const $cacheEntries = $("cache-entries");
  const $cacheSavedTokens = $("cache-saved-tokens");
  const $btnClearCache = $("btn-clear-cache");
  const $btnHistory = $("btn-history");
  const $historyModal = $("history-modal");
  const $historySessions = $("history-sessions");
  const $historyDetail = $("history-detail");
  const $btnCloseHistory = $("btn-close-history");

  // --- WebSocket management ---
  // Track page unload to prevent reconnection during refresh
  let pageUnloading = false;
  window.addEventListener("beforeunload", () => {
    pageUnloading = true;
    if (ws) { ws.onclose = null; ws.close(); }
    if (streamWs) { streamWs.onclose = null; streamWs.close(); }
  });

  // --- Event WebSocket ---
  function connect() {
    if (pageUnloading) return;
    if (ws && ws.readyState <= WebSocket.OPEN) { ws.onclose = null; ws.close(); }

    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws`);
    ws.onopen = () => { $wsIndicator.className = "ws-dot connected"; };
    ws.onclose = (e) => {
      $wsIndicator.className = "ws-dot disconnected";
      // Only reconnect on unexpected close (not page unload or server replacement)
      if (!pageUnloading && e.code !== 1001) setTimeout(connect, 2000);
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
    if (pageUnloading) return;
    if (streamWs && streamWs.readyState <= WebSocket.OPEN) { streamWs.onclose = null; streamWs.close(); }

    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    streamWs = new WebSocket(`${proto}//${location.host}/ws/stream`);
    streamWs.onclose = (e) => {
      if (!pageUnloading && e.code !== 1001) setTimeout(connectStream, 3000);
    };
    streamWs.onerror = () => streamWs.close();
  }

  // --- Load Models ---
  async function loadModels() {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10000);
      const resp = await fetch("/api/models", { signal: controller.signal });
      clearTimeout(timeout);
      if (!resp.ok) {
        console.error("loadModels: HTTP", resp.status);
        $promptModel.innerHTML = `<option value="">Error ${resp.status}</option>`;
        return;
      }
      const data = await resp.json();
      allModels = data;
      updateModelSelector();
      updateCompareSelectors();
    } catch (e) {
      console.error("loadModels failed:", e);
      $promptModel.innerHTML = '<option value="">Failed to load models</option>';
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

    if (provider === "openrouter") {
      // Sort: paid first, then free
      const paid = models.filter(m => !m.name.includes(":free"));
      const free = models.filter(m => m.name.includes(":free"));

      if (paid.length) {
        const grpPaid = document.createElement("optgroup");
        grpPaid.label = "Paid models";
        paid.forEach(m => {
          const opt = document.createElement("option");
          opt.value = m.name;
          opt.textContent = `${m.name} (${m.size})`;
          opt.style.color = "#f0b060";
          grpPaid.appendChild(opt);
        });
        $promptModel.appendChild(grpPaid);
      }
      if (free.length) {
        const grpFree = document.createElement("optgroup");
        grpFree.label = "Free models";
        free.forEach(m => {
          const opt = document.createElement("option");
          opt.value = m.name;
          opt.textContent = `${m.name} (${m.size})`;
          opt.style.color = "#7ee07e";
          grpFree.appendChild(opt);
        });
        $promptModel.appendChild(grpFree);
      }
    } else {
      models.forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m.name;
        opt.textContent = `${m.name} (${m.size})`;
        $promptModel.appendChild(opt);
      });

      // Auto-select best model
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

  // --- Agent Badges (hierarchical, grouped by category) ---
  const CATEGORY_LABELS = {
    "general": "General",
    "software-engineering": "Software Eng",
    "data-science": "Data Science",
    "finance": "Finance",
    "marketing": "Marketing",
    "tooling": "Tooling",
  };
  const CATEGORY_COLORS = {
    "general": "var(--accent)",
    "software-engineering": "var(--cyan)",
    "data-science": "var(--purple)",
    "finance": "var(--green)",
    "marketing": "var(--pink)",
    "tooling": "var(--orange)",
  };

  function renderAgentBadges() {
    if (!agentRegistry || !agentRegistry.agents) {
      $agentBadges.innerHTML = "";
      return;
    }
    const categories = agentRegistry.categories || {};
    // If no categories, fall back to flat list
    if (!Object.keys(categories).length) {
      $agentBadges.innerHTML = safeHTML(agentRegistry.agents.map((a) => {
        const status = getAgentStatus(a.name);
        return `<span class="agent-badge ${esc(status)}"><span class="agent-dot"></span>${esc(a.name)}</span>`;
      }).join(""));
      return;
    }
    // Category order
    const order = ["general", "software-engineering", "data-science", "finance", "marketing", "tooling"];
    const sortedCats = Object.keys(categories).sort((a, b) => {
      const ia = order.indexOf(a), ib = order.indexOf(b);
      return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
    });

    let html = "";
    for (const cat of sortedCats) {
      const agents = categories[cat];
      if (!agents || !agents.length) continue;
      const color = CATEGORY_COLORS[cat] || "var(--text-dim)";
      const label = CATEGORY_LABELS[cat] || cat;
      html += `<div class="agent-category" style="--cat-color: ${color}">`;
      html += `<span class="agent-cat-label">${esc(label)}</span>`;
      for (const a of agents) {
        const status = getAgentStatus(a.name);
        const desc = a.description ? a.description.split(" — ")[1] || a.description : "";
        html += `<span class="agent-badge-mini ${status}" title="${esc(a.name)}: ${esc(desc)}">`;
        html += `<span class="agent-dot-mini"></span>${esc(a.name)}`;
        html += `</span>`;
      }
      html += `</div>`;
    }
    $agentBadges.innerHTML = safeHTML(html);
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

  // --- Interaction tracking ---
  let interactions = [];

  function addInteraction(from, to, desc, status) {
    const item = { from, to, desc: desc || "", status: status || "pending", time: Date.now() };
    interactions.push(item);
    if (interactions.length > 50) interactions = interactions.slice(-50);
    renderInteractionTimeline();
    animateEdge(from, to);
    return item;
  }

  function updateInteraction(from, to, status) {
    const item = interactions.find(i => i.from === from && i.to === to && i.status === "pending");
    if (item) item.status = status;
    renderInteractionTimeline();
  }

  let renderedInteractionCount = 0;

  function renderInteractionTimeline() {
    if (!$interactionTimeline) return;
    if (!interactions.length) { $interactionTimeline.innerHTML = ""; renderedInteractionCount = 0; return; }

    // Only append new items (avoid full re-render to keep scroll smooth)
    const startIdx = Math.max(0, renderedInteractionCount);
    const newItems = interactions.slice(startIdx);

    // Trim old items if too many in DOM
    while ($interactionTimeline.children.length > 50) {
      $interactionTimeline.removeChild($interactionTimeline.firstChild);
    }

    const arrowSvg = `<svg viewBox="0 0 16 12" fill="none"><path d="M1 6h12M10 2l4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

    newItems.forEach(i => {
      const t = new Date(i.time);
      const ts = t.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
      const el = document.createElement("div");
      el.className = "interaction-item";
      el.dataset.from = i.from;
      el.dataset.to = i.to;
      el.innerHTML = safeHTML(`
        <span class="interaction-time">${esc(ts)}</span>
        <span class="interaction-agents">
          <span class="interaction-from">${esc(i.from)}</span>
          <span class="interaction-arrow-icon">${arrowSvg}</span>
          <span class="interaction-to">${esc(i.to)}</span>
        </span>
        <span class="interaction-desc">${esc(truncate(i.desc, 50))}</span>
        <span class="interaction-status ${esc(i.status)}"></span>`);
      $interactionTimeline.appendChild(el);
    });

    renderedInteractionCount = interactions.length;

    // Update status dots on existing items for changed interactions
    interactions.forEach(i => {
      const els = $interactionTimeline.querySelectorAll(`.interaction-item[data-from="${i.from}"][data-to="${i.to}"]`);
      const last = els[els.length - 1];
      if (last) {
        const dot = last.querySelector(".interaction-status");
        if (dot) dot.className = `interaction-status ${i.status}`;
      }
    });

    // Auto-scroll to bottom after DOM update
    requestAnimationFrame(() => {
      $interactionTimeline.scrollTop = $interactionTimeline.scrollHeight;
    });
  }

  function renderAgentMessages() {
    const tasks = snapshot.tasks || [];
    tasks.forEach(t => {
      const existing = interactions.find(i => i.from === (t.from_agent || "?") && i.to === (t.to_agent || "?") && i.desc === (t.description || ""));
      if (!existing) {
        addInteraction(t.from_agent || "?", t.to_agent || "?", t.description || "", t.status || "pending");
      }
    });
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
      const costs = content.agent_costs || {};
      if (steps.length) {
        steps.forEach((step) => {
          const ac = costs[step.node];
          const costTag = ac ? `<span class="chat-step-cost">${formatNumber(ac.tokens || 0)} tok &middot; $${(ac.cost_usd || 0).toFixed(4)}</span>` : "";
          html += `<div class="chat-step"><span class="chat-step-label">${esc(step.node)}${costTag}</span><div class="chat-step-text md-content">${renderMarkdown(step.output || "")}</div></div>`;
        });
      } else if (content.output) {
        html = `<div class="chat-step-text md-content">${renderMarkdown(content.output)}</div>`;
      }
      if (content.usage || content.elapsed_s) {
        const elapsed = content.elapsed_s || 0;
        const outTok = (content.usage && content.usage.output_tokens) || 0;
        const speed = elapsed > 0 ? (outTok / elapsed).toFixed(1) : 0;
        const model = (content.usage && content.usage.model) || "";
        const totalCost = Object.values(costs).reduce((s, c) => s + (c.cost_usd || 0), 0);
        const costStr = totalCost > 0 ? ` &middot; $${totalCost.toFixed(4)}` : "";
        html += `<div class="chat-usage">${outTok} tok &middot; ${speed} tok/s &middot; ${esc(model)} &middot; ${elapsed}s${costStr}</div>`;
      }
      bubble.innerHTML = safeHTML(html);
    } else if (role === "assistant") {
      const text = typeof content === "string" ? content : JSON.stringify(content);
      bubble.innerHTML = safeHTML(`<div class="md-content">${renderMarkdown(text)}</div>`);
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
    setRunning(true);
    $promptInput.value = "";
    $promptInput.style.height = "auto";

    addChatBubble("user", text);
    graphNodeStates = Object.create(null);

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

    setRunning(false);
  }

  async function runTeam(text, model, provider) {
    addSystemBubble("Running multi-agent team (team-lead → backend-dev + frontend-dev)...");

    try {
      const resp = await fetch("/api/team/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task: text, model, provider }),
      });
      const contentType = resp.headers.get("content-type") || "";
      if (!resp.ok || !contentType.includes("application/json")) {
        const errText = await resp.text();
        throw new Error(resp.status === 502 || resp.status === 504
          ? `Gateway timeout (${resp.status}) — the team run took too long. Try a simpler task or fewer agents.`
          : `Server error ${resp.status}: ${errText.slice(0, 200)}`);
      }
      const data = await resp.json();

      // Show fallback log if any
      const fbLog = data.fallback_log || [];
      if (fbLog.length > 0) {
        const fbHtml = fbLog.map(f => {
          const icon = f.status === "ok" ? "&#10003;" : "&#10007;";
          const cls = f.status === "ok" ? "fb-ok" : "fb-fail";
          return `<span class="fb-entry ${cls}">${icon} ${esc(f.agent || "")} → ${esc(f.model)} [${f.status}] ${esc(f.detail || "")}</span>`;
        }).join("");
        addSystemBubble("Fallback log:");
        const fbBubble = document.createElement("div");
        fbBubble.className = "chat-bubble system fallback-log";
        fbBubble.innerHTML = fbHtml;
        $chatMessages.appendChild(fbBubble);
      }

      if (data.success) {
        // Build steps from team outputs
        const steps = [];
        if (data.plan) {
          steps.push({ node: "team-lead (plan)", output: data.plan });
          // Create interactions for the plan delegation
          const outputs = data.agent_outputs || {};
          for (const agent of Object.keys(outputs)) {
            addInteraction("team-lead", agent, "delegated task", "completed");
          }
        }
        const outputs = data.agent_outputs || {};
        for (const [agent, output] of Object.entries(outputs)) {
          steps.push({ node: agent, output: output });
          addInteraction(agent, "team-lead", "task result", "completed");
        }
        steps.push({ node: "team-lead (summary)", output: data.output });

        addChatBubble("assistant", {
          steps,
          agent_costs: data.agent_costs || {},
          usage: { output_tokens: data.total_tokens, model },
          elapsed_s: data.elapsed_s,
        });
      } else {
        addChatBubble("assistant", `Team error: ${data.error || "Unknown error"}`);
      }
    } catch (e) {
      addChatBubble("assistant", `Team run failed: ${e.message}`);
    }

    setRunning(false);
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
      const contentType = resp.headers.get("content-type") || "";
      if (!resp.ok || !contentType.includes("application/json")) {
        const errText = await resp.text();
        throw new Error(resp.status === 502 || resp.status === 504
          ? `Gateway timeout (${resp.status}) — the agent run took too long.`
          : `Server error ${resp.status}: ${errText.slice(0, 200)}`);
      }
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

    setRunning(false);
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

  // --- Interactive SVG Graph ---
  const AGENT_COLORS = {
    "team-lead": "#58a6ff",
    "backend-dev": "#3fb950",
    "frontend-dev": "#f778ba",
    "devops": "#d29922",
    "platform-engineer": "#bc8cff",
    "ai-engineer": "#39d2c0",
    "scout": "#f85149",
    "__start__": "#bc8cff",
    "__end__": "#3fb950",
  };
  let svgNodePositions = {};

  function getNodeColor(name) {
    if (AGENT_COLORS[name]) return AGENT_COLORS[name];
    const colors = ["#58a6ff", "#3fb950", "#f778ba", "#d29922", "#bc8cff", "#39d2c0", "#f85149"];
    let hash = 0;
    for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
    return colors[Math.abs(hash) % colors.length];
  }

  function getStatusColor(state) {
    if (state === "active") return "#58a6ff";
    if (state === "done") return "#3fb950";
    if (state === "error") return "#f85149";
    return "#30363d";
  }

  function renderGraph() {
    const g = snapshot.graph;
    const hasGraph = g && (g.nodes.length || g.edges.length);

    // Only show SVG graph when a task is running (has graph nodes/edges)
    // Agent listing is already shown in the top badges bar
    if (!hasGraph) {
      $graphSvg.style.display = "none";
      $graphEmpty.style.display = "";
      return;
    }

    $graphSvg.style.display = "";
    $graphEmpty.style.display = "none";

    let nodes = g.nodes;
    let edges = g.edges;

    const layers = computeLayers(nodes, edges);
    const canvasRect = $graphCanvas.getBoundingClientRect();
    const W = canvasRect.width || 600;
    const H = canvasRect.height || 200;

    const nodeW = 110;
    const nodeH = 36;
    const layerGap = 60;
    const nodeGap = 16;

    // Compute positions
    const positions = Object.create(null);
    const totalLayerH = layers.length * nodeH + (layers.length - 1) * layerGap;
    let startY = Math.max(10, (H - totalLayerH) / 2);

    layers.forEach((layer, li) => {
      const totalLayerW = layer.length * nodeW + (layer.length - 1) * nodeGap;
      let startX = Math.max(10, (W - totalLayerW) / 2);
      layer.forEach((name, ni) => {
        safeSet(positions, name, {
          x: startX + ni * (nodeW + nodeGap),
          y: startY + li * (nodeH + layerGap),
          w: nodeW, h: nodeH,
        });
      });
    });
    svgNodePositions = positions;

    // Build SVG
    const svgNS = "http://www.w3.org/2000/svg";
    $graphSvg.innerHTML = "";
    $graphSvg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    $graphSvg.setAttribute("width", W);
    $graphSvg.setAttribute("height", H);

    // Defs for arrowheads
    const defs = document.createElementNS(svgNS, "defs");
    edges.forEach((e) => {
      const targets = e.target ? [e.target] : e.routes || [];
      targets.forEach((t) => {
        const color = getNodeColor(e.source);
        const markerId = `arrow-${e.source}-${t}`.replace(/[^a-zA-Z0-9-_]/g, "_");
        const marker = document.createElementNS(svgNS, "marker");
        marker.setAttribute("id", markerId);
        marker.setAttribute("viewBox", "0 0 10 10");
        marker.setAttribute("refX", "8"); marker.setAttribute("refY", "5");
        marker.setAttribute("markerWidth", "8"); marker.setAttribute("markerHeight", "8");
        marker.setAttribute("orient", "auto-start-reverse");
        const path = document.createElementNS(svgNS, "path");
        path.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
        path.setAttribute("fill", color);
        path.style.opacity = "0.7";
        marker.appendChild(path);
        defs.appendChild(marker);
      });
    });
    $graphSvg.appendChild(defs);

    // Draw edges
    edges.forEach((e) => {
      const targets = e.target ? [e.target] : e.routes || [];
      targets.forEach((t) => {
        const from = positions[e.source];
        const to = positions[t];
        if (!from || !to) return;

        const color = getNodeColor(e.source);
        const markerId = `arrow-${e.source}-${t}`.replace(/[^a-zA-Z0-9-_]/g, "_");
        const g = document.createElementNS(svgNS, "g");
        g.classList.add("svg-edge");
        g.dataset.from = e.source;
        g.dataset.to = t;

        const x1 = from.x + from.w / 2;
        const y1 = from.y + from.h;
        const x2 = to.x + to.w / 2;
        const y2 = to.y;

        // Use a curved path if nodes are on same layer or far apart horizontally
        const dx = Math.abs(x2 - x1);
        if (dx > 10) {
          const path = document.createElementNS(svgNS, "path");
          const midY = (y1 + y2) / 2;
          path.setAttribute("d", `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`);
          path.setAttribute("stroke", color);
          path.setAttribute("stroke-width", "1.5");
          path.setAttribute("fill", "none");
          path.setAttribute("opacity", "0.5");
          path.setAttribute("marker-end", `url(#${markerId})`);
          g.appendChild(path);
        } else {
          const line = document.createElementNS(svgNS, "line");
          line.setAttribute("x1", x1); line.setAttribute("y1", y1);
          line.setAttribute("x2", x2); line.setAttribute("y2", y2);
          line.setAttribute("stroke", color);
          line.setAttribute("stroke-width", "1.5");
          line.setAttribute("opacity", "0.5");
          line.setAttribute("marker-end", `url(#${markerId})`);
          g.appendChild(line);
        }

        $graphSvg.appendChild(g);
      });
    });

    // Draw nodes
    nodes.forEach((name) => {
      const pos = positions[name];
      if (!pos) return;
      const state = graphNodeStates[name] || "";
      const color = getNodeColor(name);
      const statusColor = getStatusColor(state);
      const display = name === "__start__" ? "START" : name === "__end__" ? "END" : name;

      const g = document.createElementNS(svgNS, "g");
      g.classList.add("svg-agent-node");
      g.setAttribute("transform", `translate(${pos.x}, ${pos.y})`);

      // Background rect
      const rect = document.createElementNS(svgNS, "rect");
      rect.setAttribute("width", pos.w);
      rect.setAttribute("height", pos.h);
      rect.setAttribute("fill", `${color}15`);
      rect.setAttribute("stroke", `${color}60`);
      if (state === "active") {
        rect.setAttribute("fill", `${color}30`);
        rect.setAttribute("stroke", color);
        rect.setAttribute("stroke-width", "2.5");
      } else if (state === "done") {
        rect.setAttribute("fill", "rgba(63,185,80,0.12)");
        rect.setAttribute("stroke", "rgba(63,185,80,0.5)");
      } else if (state === "error") {
        rect.setAttribute("fill", "rgba(248,81,73,0.12)");
        rect.setAttribute("stroke", "rgba(248,81,73,0.5)");
      }
      g.appendChild(rect);

      // Status dot
      const dot = document.createElementNS(svgNS, "circle");
      dot.classList.add("agent-status-dot");
      dot.setAttribute("cx", 12); dot.setAttribute("cy", pos.h / 2);
      dot.setAttribute("r", 4);
      dot.setAttribute("fill", statusColor);
      if (state === "active") {
        const anim = document.createElementNS(svgNS, "animate");
        anim.setAttribute("attributeName", "opacity");
        anim.setAttribute("values", "1;0.4;1");
        anim.setAttribute("dur", "1.5s");
        anim.setAttribute("repeatCount", "indefinite");
        dot.appendChild(anim);
      }
      g.appendChild(dot);

      // Label
      const text = document.createElementNS(svgNS, "text");
      text.setAttribute("x", pos.w / 2);
      text.setAttribute("y", pos.h / 2 + 1);
      text.setAttribute("text-anchor", "middle");
      text.setAttribute("dominant-baseline", "middle");
      text.setAttribute("fill", state === "done" ? "#3fb950" : state === "error" ? "#f85149" : color);
      text.setAttribute("font-size", "11");
      text.textContent = display;
      g.appendChild(text);

      // Agent role subtitle (from registry)
      if (agentRegistry && agentRegistry.agents) {
        const agentInfo = agentRegistry.agents.find(a => a.name === name);
        if (agentInfo && agentInfo.role) {
          const role = document.createElementNS(svgNS, "text");
          role.classList.add("agent-role");
          role.setAttribute("x", pos.w / 2);
          role.setAttribute("y", pos.h + 12);
          role.setAttribute("text-anchor", "middle");
          role.textContent = truncate(agentInfo.role, 18);
          g.appendChild(role);
        }
      }

      g.addEventListener("click", () => window._showNodeDetail(name));
      $graphSvg.appendChild(g);
    });
  }

  function animateEdge(from, to) {
    if (!$graphSvg) return;
    const edge = $graphSvg.querySelector(`.svg-edge[data-from="${from}"][data-to="${to}"]`);
    if (!edge) {
      // Try to find by creating a temporary visual arrow between nodes
      const fromPos = svgNodePositions[from];
      const toPos = svgNodePositions[to];
      if (!fromPos || !toPos) return;
      const svgNS = "http://www.w3.org/2000/svg";
      const color = getNodeColor(from);
      const g = document.createElementNS(svgNS, "g");
      g.classList.add("svg-edge", "animating", "active");
      g.dataset.from = from;
      g.dataset.to = to;
      const x1 = fromPos.x + fromPos.w / 2;
      const y1 = fromPos.y + fromPos.h;
      const x2 = toPos.x + toPos.w / 2;
      const y2 = toPos.y;
      const line = document.createElementNS(svgNS, "line");
      line.setAttribute("x1", x1); line.setAttribute("y1", y1);
      line.setAttribute("x2", x2); line.setAttribute("y2", y2);
      line.setAttribute("stroke", color);
      line.setAttribute("stroke-width", "2.5");
      line.setAttribute("opacity", "0.8");
      g.appendChild(line);
      // Insert before nodes (so it's behind them)
      const firstNode = $graphSvg.querySelector(".svg-agent-node");
      if (firstNode) $graphSvg.insertBefore(g, firstNode);
      else $graphSvg.appendChild(g);
      setTimeout(() => { g.classList.remove("animating"); g.classList.remove("active"); }, 3000);
      return;
    }
    edge.classList.add("animating", "active");
    const el = edge.querySelector("line, path");
    if (el) {
      el.setAttribute("opacity", "0.9");
      el.setAttribute("stroke-width", "2.5");
    }
    setTimeout(() => {
      edge.classList.remove("animating", "active");
      if (el) {
        el.setAttribute("opacity", "0.5");
        el.setAttribute("stroke-width", "1.5");
      }
    }, 3000);
  }

  function computeLayers(nodes, edges) {
    const inDeg = Object.create(null);
    const adj = Object.create(null);
    nodes.forEach((n) => { safeSet(inDeg, n, 0); safeSet(adj, n, []); });
    edges.forEach((e) => {
      const targets = e.target ? [e.target] : e.routes || [];
      targets.forEach((t) => { if (safeGet(adj, e.source)) safeGet(adj, e.source).push(t); if (safeGet(inDeg, t) !== undefined) safeSet(inDeg, t, safeGet(inDeg, t) + 1); });
    });
    const layers = [];
    const visited = new Set();
    let queue = nodes.filter((n) => safeGet(inDeg, n) === 0);
    if (!queue.length && nodes.length) queue = [nodes[0]];
    while (queue.length && visited.size < nodes.length) {
      layers.push([...queue]);
      queue.forEach((n) => visited.add(n));
      const next = [];
      queue.forEach((n) => (safeGet(adj, n) || []).forEach((t) => { if (!visited.has(t)) { safeSet(inDeg, t, (safeGet(inDeg, t) || 0) - 1); if ((safeGet(inDeg, t) || 0) <= 0 && !next.includes(t)) next.push(t); } }));
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
    safeSet(graphNodeStates, name, "active");
    renderGraph();
    addSystemBubble(`Replaying node: ${name}...`);

    try {
      const resp = await fetch("/api/graph/replay", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ node: name }) });
      const data = await resp.json();
      safeSet(graphNodeStates, name, "done");
      renderGraph();
      if (data.success) {
        addChatBubble("assistant", { steps: [{ node: `${name} (replay)`, output: data.output }], elapsed_s: data.elapsed_s });
      } else {
        addChatBubble("assistant", `Replay error: ${data.error || "Unknown"}`);
      }
    } catch (e) { safeSet(graphNodeStates, name, "done"); renderGraph(); addChatBubble("assistant", `Replay failed: ${e.message}`); }
    setRunning(false);
  };

  // --- Reset Graph ---
  async function resetGraph() {
    try {
      await fetch("/api/graph/reset", { method: "POST" });
      snapshot.agents = Object.create(null); snapshot.tasks = []; snapshot.orchestrator_status = "idle";
      snapshot.graph = { nodes: [], edges: [] }; snapshot.total_tokens = 0; snapshot.total_cost_usd = 0;
      graphNodeStates = Object.create(null); events = []; activityCount = 0; interactions = []; renderedInteractionCount = 0;
      $timeline.innerHTML = "";
      $agentActivity.innerHTML = '<div class="empty-state">Waiting for agents...</div>';
      if ($interactionTimeline) $interactionTimeline.innerHTML = "";
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

  // --- Sidebar Resize (drag handle) ---
  (function initSidebarResize() {
    const handle = $("sidebar-resize-handle");
    if (!handle) return;
    let dragging = false;
    let startX = 0;
    let startW = 0;

    handle.addEventListener("mousedown", (e) => {
      dragging = true;
      startX = e.clientX;
      startW = $sidebar.offsetWidth;
      handle.classList.add("dragging");
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      e.preventDefault();
    });

    document.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      const delta = startX - e.clientX;
      const newW = Math.max(200, Math.min(800, startW + delta));
      $sidebar.style.width = newW + "px";
    });

    document.addEventListener("mouseup", () => {
      if (!dragging) return;
      dragging = false;
      handle.classList.remove("dragging");
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    });
  })();

  // --- Section Resize (graph & input) ---
  (function initSectionResize() {
    const graphSection = $("section-graph");
    const inputBar = $("input-bar");
    const resizeGraph = $("resize-graph");
    const resizeInput = $("resize-input");

    function makeDraggable(handle, getTarget, prop, min, max, invert) {
      if (!handle) return;
      let dragging = false;
      let startY = 0;
      let startH = 0;

      handle.addEventListener("mousedown", (e) => {
        const target = getTarget();
        if (!target) return;
        dragging = true;
        startY = e.clientY;
        startH = target.offsetHeight;
        handle.classList.add("dragging");
        document.body.style.cursor = "ns-resize";
        document.body.style.userSelect = "none";
        e.preventDefault();
      });

      document.addEventListener("mousemove", (e) => {
        if (!dragging) return;
        const target = getTarget();
        if (!target) return;
        const delta = invert ? (startY - e.clientY) : (e.clientY - startY);
        const newH = Math.max(min, Math.min(max, startH + delta));
        target.style.height = newH + "px";
        target.style.minHeight = newH + "px";
        target.style.maxHeight = newH + "px";
        // Re-render graph SVG to fit new size
        if (target === graphSection) renderGraph();
      });

      document.addEventListener("mouseup", () => {
        if (!dragging) return;
        dragging = false;
        handle.classList.remove("dragging");
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        // Save size
        const target = getTarget();
        if (target) {
          try { localStorage.setItem("ao_resize_" + target.id, target.offsetHeight); } catch(e) {}
        }
      });
    }

    makeDraggable(resizeGraph, () => graphSection, "height", 60, 500, false);
    makeDraggable(resizeInput, () => inputBar, "height", 60, 400, true);

    // Restore saved sizes
    try {
      const savedGraph = localStorage.getItem("ao_resize_section-graph");
      if (savedGraph && graphSection) {
        const h = parseInt(savedGraph);
        if (h >= 60) { graphSection.style.height = h + "px"; graphSection.style.minHeight = h + "px"; graphSection.style.maxHeight = h + "px"; }
      }
      const savedInput = localStorage.getItem("ao_resize_input-bar");
      if (savedInput && inputBar) {
        const h = parseInt(savedInput);
        if (h >= 60) { inputBar.style.height = h + "px"; inputBar.style.minHeight = h + "px"; inputBar.style.maxHeight = h + "px"; }
      }
    } catch(e) {}
  })();

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
      if (agent && agent !== "team-lead") {
        addInteraction("team-lead", agent, "spawned agent", "running");
      }
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
      addInteraction(d.from_agent || "?", d.to_agent || "?", d.description || "task delegation", "running");
    } else if (t === "cooperation.task_completed") {
      const status = d.success ? "completed" : "failed";
      addActivityItem(d.success ? "complete" : "error", d.from_agent || "?", `Task ${status}`, truncate(d.summary || "", 50));
      updateInteraction(d.from_agent || "?", d.to_agent || "?", status);
      addInteraction(d.to_agent || "?", d.from_agent || "?", d.summary || "task result", status);
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
      graphNodeStates = Object.create(null);
      renderGraph();
    } else if (evt.event_type === "graph.node.enter") {
      safeSet(graphNodeStates, evt.node_name || evt.data.node, "active");
      renderGraph();
    } else if (evt.event_type === "graph.node.exit") {
      safeSet(graphNodeStates, evt.node_name || evt.data.node, "done");
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
    const agentName = evt.agent_name;
    snapshot.event_count = (snapshot.event_count || 0) + 1;
    if (t === "orchestrator.start") snapshot.orchestrator_status = "running";
    else if (t === "orchestrator.end") snapshot.orchestrator_status = evt.data.success ? "completed" : "failed";
    else if (t === "agent.spawn") {
      safeSet(snapshot.agents, agentName, { name: agentName, status: "running", steps: 0, tokens: 0, cost_usd: 0, provider: evt.data.provider || "", role: evt.data.role || "", tools: evt.data.tools || [] });
    } else if (t === "agent.step" && safeGet(snapshot.agents, agentName)) safeGet(snapshot.agents, agentName).steps += 1;
    else if (t === "agent.complete" && safeGet(snapshot.agents, agentName)) safeGet(snapshot.agents, agentName).status = "completed";
    else if (t === "agent.error" && safeGet(snapshot.agents, agentName)) safeGet(snapshot.agents, agentName).status = "error";
    else if (t === "agent.stalled" && safeGet(snapshot.agents, agentName)) safeGet(snapshot.agents, agentName).status = "error";
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
      if (agentName && safeGet(snapshot.agents, agentName)) {
        safeGet(snapshot.agents, agentName).tokens = evt.data.agent_tokens || 0;
        safeGet(snapshot.agents, agentName).cost_usd = evt.data.agent_cost_usd || 0;
      }
    }
  }

  // --- Cumulative usage from DB ---
  async function fetchUsageStats() {
    try {
      const resp = await fetch("/api/usage");
      const data = await resp.json();
      cumulativeUsage = data;
      renderCumulativeMetrics();
    } catch (e) {
      cumulativeUsage = null;
      renderCumulativeMetrics();
    }
  }

  function renderCumulativeMetrics() {
    if (!cumulativeUsage) {
      if ($cumulTokens) $cumulTokens.textContent = "-";
      if ($cumulCost) $cumulCost.textContent = "-";
      if ($cumulRequests) $cumulRequests.textContent = "-";
      if ($cumulSpeed) $cumulSpeed.textContent = "- tok/s";
      if ($dbIndicator) $dbIndicator.className = "db-dot disconnected";
      return;
    }
    if ($cumulTokens) $cumulTokens.textContent = formatNumber(cumulativeUsage.total_tokens || 0);
    if ($cumulCost) $cumulCost.textContent = `$${(cumulativeUsage.total_cost_usd || 0).toFixed(3)}`;
    if ($cumulRequests) $cumulRequests.textContent = String(cumulativeUsage.total_requests || 0);
    if ($cumulSpeed) $cumulSpeed.textContent = cumulativeUsage.avg_speed > 0 ? `${cumulativeUsage.avg_speed} tok/s` : "- tok/s";
    if ($dbIndicator) $dbIndicator.className = cumulativeUsage.db_connected ? "db-dot connected" : "db-dot disconnected";
    // Update session speed from server (more accurate than client-side)
    if (cumulativeUsage.session_speed > 0) {
      lastTokenSpeed = cumulativeUsage.session_speed;
      renderHeader();
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
    if ($cacheEntries) $cacheEntries.textContent = (c.entries || 0) + " entries";
    if ($cacheSavedTokens) {
      const saved = c.total_saved_tokens || 0;
      $cacheSavedTokens.textContent = saved > 0 ? formatNumber(saved) + " tokens saved" : "0 tokens saved";
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
  /** Sanitize HTML string — strips scripts and event handlers for safe innerHTML use. */
  function safeHTML(html) {
    const tpl = document.createElement("template");
    tpl.innerHTML = html;
    const content = tpl.content;
    content.querySelectorAll("script,iframe,object,embed,link[rel=import]").forEach(el => el.remove());
    content.querySelectorAll("*").forEach(el => {
      for (const attr of [...el.attributes]) {
        if (attr.name.startsWith("on") || attr.value.includes("javascript:")) el.removeAttribute(attr.name);
      }
    });
    const wrapper = document.createElement("div");
    wrapper.appendChild(content.cloneNode(true));
    return wrapper.innerHTML;
  }
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
  $btnCompare.addEventListener("click", runComparison);
  $btnResetGraph.addEventListener("click", resetGraph);
  $btnToggleSidebar.addEventListener("click", toggleSidebar);

  // Cache clear button
  if ($btnClearCache) {
    $btnClearCache.addEventListener("click", async () => {
      try {
        const resp = await fetch("/api/cache/clear", { method: "POST", headers: authHeaders() });
        if (resp.ok) {
          snapshot.cache = { hits: 0, misses: 0, hit_rate: 0, evictions: 0, entries: 0, total_saved_tokens: 0 };
          renderCachePanel();
          if ($cacheLog) $cacheLog.innerHTML = "";
        }
      } catch (e) { /* ignore */ }
    });
  }
  window.addEventListener("resize", () => renderGraph());

  // --- OpenRouter Pricing ---
  const $pricingList = $("pricing-list");
  const $pricingSearch = $("pricing-search");
  const $btnRefreshPricing = $("btn-refresh-pricing");
  let pricingData = [];

  async function loadPricing() {
    if (!$pricingList) return;
    $pricingList.innerHTML = '<div class="empty-state">Loading...</div>';
    try {
      const resp = await fetch("/api/openrouter/pricing");
      const data = await resp.json();
      pricingData = data.models || [];
      renderPricing();
    } catch (e) {
      $pricingList.innerHTML = `<div class="empty-state">Error: ${esc(e.message)}</div>`;
    }
  }

  function renderPricing() {
    if (!$pricingList) return;
    const q = ($pricingSearch ? $pricingSearch.value : "").toLowerCase();
    const filtered = q ? pricingData.filter((m) => m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q)) : pricingData;
    if (!filtered.length) {
      $pricingList.innerHTML = '<div class="empty-state">No models found</div>';
      return;
    }
    // Show max 50 to keep it fast
    const show = filtered.slice(0, 50);
    $pricingList.innerHTML = `<div class="pricing-table">
      <div class="pricing-row pricing-header">
        <span class="pricing-model">Model</span>
        <span class="pricing-cost">In $/M</span>
        <span class="pricing-cost">Out $/M</span>
      </div>
      ${show.map((m) => `<div class="pricing-row${m.is_free ? " pricing-free" : ""}">
        <span class="pricing-model" title="${esc(m.id)}">${esc(m.name)}</span>
        <span class="pricing-cost">${m.is_free ? "free" : "$" + m.input_per_m.toFixed(2)}</span>
        <span class="pricing-cost">${m.is_free ? "free" : "$" + m.output_per_m.toFixed(2)}</span>
      </div>`).join("")}
    </div>
    <div class="pricing-footer">${filtered.length} models${filtered.length > 50 ? " (showing 50)" : ""}</div>`;
  }

  if ($btnRefreshPricing) $btnRefreshPricing.addEventListener("click", loadPricing);
  if ($pricingSearch) {
    let debounceTimer;
    $pricingSearch.addEventListener("input", () => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(renderPricing, 200);
    });
  }

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
        setRunning(false);
      } else if (data.type === "error") {
        const bubble = $("streaming-bubble");
        if (bubble) bubble.remove();
        addChatBubble("assistant", `Error: ${data.error}`);
        setRunning(false);
      }
    };
  }

  // --- Session Restore ---
  async function restoreSessionHistory() {
    try {
      const resp = await fetch("/api/session/history");
      const data = await resp.json();
      if (!data.records || !data.records.length) return;

      for (const rec of data.records) {
        const type = rec.job_type;
        if (type === "prompt" || type === "stream") {
          addChatBubble("user", rec.prompt || "");
          const result = rec.result || {};
          if (result.success !== false && result.output) {
            addChatBubble("assistant", result.output);
          } else if (result.error) {
            addChatBubble("assistant", `Error: ${result.error}`);
          }
          if (result.tokens) snapshot.total_tokens += result.tokens;
        } else if (type === "agent_run") {
          addChatBubble("user", rec.task || "");
          const result = rec.result || {};
          if (result.success) {
            addChatBubble("assistant", {
              steps: [{ node: rec.agent || "agent", output: result.output }],
              usage: { output_tokens: result.total_tokens, model: rec.model },
              elapsed_s: result.elapsed_s,
            });
          } else {
            addChatBubble("assistant", `Error: ${result.error || "Failed"}`);
          }
          if (result.total_tokens) snapshot.total_tokens += result.total_tokens;
          if (result.total_cost_usd) snapshot.total_cost_usd += result.total_cost_usd;
        } else if (type === "team_run") {
          addChatBubble("user", rec.task || "");
          const result = rec.result || {};
          if (result.success) {
            addChatBubble("assistant", {
              steps: [{ node: "team (summary)", output: result.output }],
              agent_costs: result.agent_costs || {},
              usage: { output_tokens: result.total_tokens, model: rec.model },
              elapsed_s: result.elapsed_s,
            });
          } else {
            addChatBubble("assistant", `Error: ${result.error || "Failed"}`);
          }
          if (result.total_tokens) snapshot.total_tokens += result.total_tokens;
          if (result.total_cost_usd) snapshot.total_cost_usd += result.total_cost_usd;
        }
      }
      renderHeader();
    } catch (e) { /* ignore restore errors */ }
  }

  // --- Collapsible Sections ---
  function initCollapsible() {
    document.querySelectorAll(".btn-collapse").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const section = document.getElementById(btn.dataset.target);
        if (!section) return;
        section.classList.toggle("collapsed");
        // Save state
        const collapsed = {};
        document.querySelectorAll(".collapsible-section").forEach((s) => {
          collapsed[s.id] = s.classList.contains("collapsed");
        });
        try { localStorage.setItem("ao_collapsed", JSON.stringify(collapsed)); } catch(e) {}
      });
    });
    // Also allow clicking the section header row to toggle
    document.querySelectorAll(".section-header").forEach((hdr) => {
      hdr.addEventListener("click", (e) => {
        // Don't toggle if clicking on controls (select, button other than collapse)
        if (e.target.closest("select, .logs-controls button, .btn-graph-ctrl, .agent-badges")) return;
        const btn = hdr.querySelector(".btn-collapse");
        if (btn) btn.click();
      });
    });
    // Restore state
    try {
      const saved = JSON.parse(localStorage.getItem("ao_collapsed") || "{}");
      for (const [id, isCollapsed] of Object.entries(saved)) {
        const section = document.getElementById(id);
        if (section && isCollapsed) section.classList.add("collapsed");
      }
    } catch(e) {}
  }

  // --- Job History Modal ---
  if ($btnHistory) $btnHistory.addEventListener("click", openHistory);
  if ($btnCloseHistory) $btnCloseHistory.addEventListener("click", () => $historyModal.classList.add("hidden"));
  if ($historyModal) $historyModal.addEventListener("click", (e) => { if (e.target === $historyModal) $historyModal.classList.add("hidden"); });

  async function openHistory() {
    $historyModal.classList.remove("hidden");
    $historySessions.innerHTML = '<div class="empty-state">Loading...</div>';
    $historyDetail.innerHTML = '<div class="empty-state">Select a session</div>';
    try {
      const resp = await fetch("/api/jobs/list");
      const data = await resp.json();
      renderSessionList(data.sessions || []);
    } catch (e) {
      $historySessions.innerHTML = `<div class="empty-state">Error: ${esc(e.message)}</div>`;
    }
  }

  function renderSessionList(sessions) {
    if (!sessions.length) {
      $historySessions.innerHTML = '<div class="empty-state">No sessions yet</div>';
      return;
    }
    $historySessions.innerHTML = sessions.map((s) => {
      const label = s.first_prompt || "(no prompt)";
      const badge = s.is_current ? ' <span class="badge running">current</span>' : "";
      const ts = s.session_id.replace(/_/g, " ").slice(0, 15);
      const delBtn = s.is_current ? "" : `<button class="btn-session-delete" data-sid="${esc(s.session_id)}" title="Delete session">&times;</button>`;
      return `<div class="history-session-item${s.is_current ? " active" : ""}" data-sid="${esc(s.session_id)}">
        <div class="history-session-meta">${esc(ts)}${badge}${delBtn}</div>
        <div class="history-session-prompt">${esc(label)}</div>
        <div class="history-session-stats">${s.records} records &middot; ${s.files} files</div>
      </div>`;
    }).join("");
    $historySessions.querySelectorAll(".history-session-item").forEach((el) => {
      el.addEventListener("click", (e) => {
        if (e.target.classList.contains("btn-session-delete")) return;
        $historySessions.querySelectorAll(".history-session-item").forEach((x) => x.classList.remove("selected"));
        el.classList.add("selected");
        loadSessionDetail(el.dataset.sid);
      });
    });
    $historySessions.querySelectorAll(".btn-session-delete").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteSession(btn.dataset.sid);
      });
    });
  }

  async function loadSessionDetail(sessionId) {
    $historyDetail.innerHTML = '<div class="empty-state">Loading...</div>';
    try {
      const resp = await fetch(`/api/jobs/${encodeURIComponent(sessionId)}`);
      const data = await resp.json();
      if (data.error) {
        $historyDetail.innerHTML = `<div class="empty-state">${esc(data.error)}</div>`;
        return;
      }
      renderSessionDetail(sessionId, data.records || []);
    } catch (e) {
      $historyDetail.innerHTML = `<div class="empty-state">Error: ${esc(e.message)}</div>`;
    }
  }

  function renderSessionDetail(sessionId, records) {
    if (!records.length) {
      $historyDetail.innerHTML = '<div class="empty-state">Empty session</div>';
      return;
    }
    const actions = `<div class="history-actions">
      <button class="btn-history-load" data-sid="${esc(sessionId)}">Load into chat</button>
      <button class="btn-history-switch" data-sid="${esc(sessionId)}">Switch &amp; continue</button>
    </div>`;
    const rows = records.map((r) => {
      const prompt = r.prompt || r.task || "";
      const result = r.result || {};
      const output = result.output || result.error || "";
      const icon = r.job_type === "team_run" ? "T" : r.job_type === "agent_run" ? "A" : "P";
      const tokens = result.total_tokens || result.tokens || 0;
      const cost = result.total_cost_usd ? "$" + result.total_cost_usd.toFixed(4) : "";
      return `<div class="history-record">
        <div class="history-record-header">
          <span class="history-record-type">${icon}</span>
          <span class="history-record-num">#${r.job_number}</span>
          <span class="history-record-type-label">${esc(r.job_type)}</span>
          ${tokens ? `<span class="history-record-tokens">${formatNumber(tokens)} tok</span>` : ""}
          ${cost ? `<span class="history-record-cost">${cost}</span>` : ""}
        </div>
        ${prompt ? `<div class="history-record-prompt">${esc(truncate(prompt, 200))}</div>` : ""}
        ${output ? `<div class="history-record-output">${esc(truncate(output, 300))}</div>` : ""}
      </div>`;
    }).join("");
    $historyDetail.innerHTML = actions + rows;

    // Wire up action buttons
    $historyDetail.querySelector(".btn-history-load")?.addEventListener("click", () => loadSessionIntoChat(sessionId));
    $historyDetail.querySelector(".btn-history-switch")?.addEventListener("click", () => switchToSession(sessionId));
  }

  async function loadSessionIntoChat(sessionId) {
    $historyModal.classList.add("hidden");
    $chatMessages.innerHTML = "";
    snapshot.total_tokens = 0;
    snapshot.total_cost_usd = 0;
    try {
      // Restore conversation memory on the backend so follow-up messages have context
      const restoreResp = await fetch(`/api/jobs/${encodeURIComponent(sessionId)}/restore`, { method: "POST" });
      const restoreData = await restoreResp.json();
      if (restoreData.success && restoreData.conversation_id) {
        conversationId = restoreData.conversation_id;
        addSystemBubble(
          `Session restored (${restoreData.messages_restored} messages). Conversation memory active.`
        );
      } else {
        conversationId = null;
      }

      const resp = await fetch(`/api/jobs/${encodeURIComponent(sessionId)}`);
      const data = await resp.json();
      const records = data.records || [];
      for (const rec of records) {
        const type = rec.job_type;
        if (type === "prompt" || type === "stream") {
          addChatBubble("user", rec.prompt || "");
          const result = rec.result || {};
          if (result.success !== false && result.output) {
            addChatBubble("assistant", result.output);
          } else if (result.error) {
            addChatBubble("assistant", `Error: ${result.error}`);
          }
          if (result.tokens) snapshot.total_tokens += result.tokens;
        } else if (type === "agent_run") {
          addChatBubble("user", rec.task || "");
          const result = rec.result || {};
          if (result.success) {
            addChatBubble("assistant", {
              steps: [{ node: rec.agent || "agent", output: result.output }],
              usage: { output_tokens: result.total_tokens, model: rec.model },
              elapsed_s: result.elapsed_s,
            });
          } else {
            addChatBubble("assistant", `Error: ${result.error || "Failed"}`);
          }
          if (result.total_tokens) snapshot.total_tokens += result.total_tokens;
          if (result.total_cost_usd) snapshot.total_cost_usd += result.total_cost_usd;
        } else if (type === "team_run") {
          addChatBubble("user", rec.task || "");
          const result = rec.result || {};
          if (result.success) {
            addChatBubble("assistant", {
              steps: [{ node: "team (summary)", output: result.output }],
              agent_costs: result.agent_costs || {},
              usage: { output_tokens: result.total_tokens, model: rec.model },
              elapsed_s: result.elapsed_s,
            });
          } else {
            addChatBubble("assistant", `Error: ${result.error || "Failed"}`);
          }
          if (result.total_tokens) snapshot.total_tokens += result.total_tokens;
          if (result.total_cost_usd) snapshot.total_cost_usd += result.total_cost_usd;
        }
      }
      renderHeader();
    } catch (e) {
      addChatBubble("assistant", `Error loading session: ${e.message}`);
    }
  }

  async function switchToSession(sessionId) {
    try {
      const resp = await fetch(`/api/jobs/${encodeURIComponent(sessionId)}/switch`, { method: "POST" });
      const data = await resp.json();
      if (!data.success) {
        alert("Failed to switch session: " + (data.error || "Unknown error"));
        return;
      }
      // Load session into chat view
      await loadSessionIntoChat(sessionId);
    } catch (e) {
      alert("Error switching session: " + e.message);
    }
  }

  async function deleteSession(sessionId) {
    if (!confirm(`Delete session ${sessionId}?\nFiles will be removed but metrics are preserved.`)) return;
    try {
      const resp = await fetch(`/api/jobs/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
      const data = await resp.json();
      if (data.success) {
        // Refresh the session list
        openHistory();
      } else {
        alert("Failed to delete: " + (data.error || "Unknown error"));
      }
    } catch (e) {
      alert("Error deleting session: " + e.message);
    }
  }

  // --- Explorer ---
  const $btnExplorer = $("btn-explorer");
  const $explorerModal = $("explorer-modal");
  const $explorerSessions = $("explorer-sessions");
  const $explorerFiles = $("explorer-files");
  const $explorerPreview = $("explorer-preview");
  const $btnCloseExplorer = $("btn-close-explorer");
  const $btnExplorerDownload = $("btn-explorer-download");
  let explorerCurrentSession = null;

  if ($btnExplorer) $btnExplorer.addEventListener("click", openExplorer);
  if ($btnCloseExplorer) $btnCloseExplorer.addEventListener("click", () => $explorerModal.classList.add("hidden"));
  if ($explorerModal) $explorerModal.addEventListener("click", (e) => { if (e.target === $explorerModal) $explorerModal.classList.add("hidden"); });
  if ($btnExplorerDownload) $btnExplorerDownload.addEventListener("click", () => {
    if (explorerCurrentSession) {
      const a = document.createElement("a");
      a.href = `/api/jobs/${encodeURIComponent(explorerCurrentSession)}/download`;
      a.download = `session_${explorerCurrentSession}.zip`;
      a.click();
    }
  });

  async function openExplorer() {
    $explorerModal.classList.remove("hidden");
    $explorerSessions.innerHTML = '<div class="empty-state">Loading...</div>';
    $explorerFiles.innerHTML = '<div class="empty-state">Select a session</div>';
    $explorerPreview.innerHTML = '<div class="empty-state">Select a file</div>';
    $btnExplorerDownload.disabled = true;
    explorerCurrentSession = null;
    try {
      const resp = await fetch("/api/jobs/list");
      const data = await resp.json();
      renderExplorerSessions(data.sessions || []);
    } catch (e) {
      $explorerSessions.innerHTML = `<div class="empty-state">Error: ${esc(e.message)}</div>`;
    }
  }

  function renderExplorerSessions(sessions) {
    if (!sessions.length) {
      $explorerSessions.innerHTML = '<div class="empty-state">No sessions</div>';
      return;
    }
    $explorerSessions.innerHTML = sessions.map((s) => {
      const label = s.first_prompt || "(no prompt)";
      const ts = s.session_id.replace(/_/g, " ").slice(0, 15);
      return `<div class="explorer-session-item" data-sid="${esc(s.session_id)}">
        <div class="explorer-session-prompt">${esc(label)}</div>
        <div class="explorer-session-meta">${esc(ts)} &middot; ${s.records} rec &middot; ${s.files} files</div>
      </div>`;
    }).join("");
    $explorerSessions.querySelectorAll(".explorer-session-item").forEach((el) => {
      el.addEventListener("click", () => {
        $explorerSessions.querySelectorAll(".explorer-session-item").forEach((x) => x.classList.remove("active"));
        el.classList.add("active");
        loadExplorerFiles(el.dataset.sid);
      });
    });
  }

  async function loadExplorerFiles(sessionId) {
    explorerCurrentSession = sessionId;
    $btnExplorerDownload.disabled = false;
    $explorerFiles.innerHTML = '<div class="empty-state">Loading...</div>';
    $explorerPreview.innerHTML = '<div class="empty-state">Select a file</div>';
    try {
      const resp = await fetch(`/api/jobs/${encodeURIComponent(sessionId)}/files`);
      const data = await resp.json();
      renderExplorerFiles(sessionId, data.files || []);
    } catch (e) {
      $explorerFiles.innerHTML = `<div class="empty-state">Error: ${esc(e.message)}</div>`;
    }
  }

  function fileIcon(name) {
    const ext = name.split(".").pop().toLowerCase();
    const icons = { json: "\ud83d\udccb", py: "\ud83d\udc0d", js: "\ud83d\udfe8", md: "\ud83d\udcd6", txt: "\ud83d\udcc4", html: "\ud83c\udf10", css: "\ud83c\udfa8", yml: "\u2699\ufe0f", yaml: "\u2699\ufe0f", sh: "\ud83d\udcdf", log: "\ud83d\udcc3" };
    return icons[ext] || "\ud83d\udcc4";
  }

  function formatBytes(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  function detectLanguage(name) {
    const ext = name.split(".").pop().toLowerCase();
    const map = { py: "python", js: "javascript", json: "json", md: "markdown", html: "xml", css: "css", yml: "yaml", yaml: "yaml", sh: "bash", ts: "typescript", sql: "sql", xml: "xml", toml: "ini", cfg: "ini", txt: "plaintext", log: "plaintext" };
    return map[ext] || "plaintext";
  }

  function renderExplorerFiles(sessionId, files) {
    if (!files.length) {
      $explorerFiles.innerHTML = '<div class="empty-state">Empty session</div>';
      return;
    }
    $explorerFiles.innerHTML = files.map((f) => {
      return `<div class="explorer-file-item" data-name="${esc(f.name)}">
        <span class="explorer-file-icon">${fileIcon(f.name)}</span>
        <span class="explorer-file-name" title="${esc(f.name)}">${esc(f.name)}</span>
        <span class="explorer-file-size">${formatBytes(f.size)}</span>
      </div>`;
    }).join("");
    $explorerFiles.querySelectorAll(".explorer-file-item").forEach((el) => {
      el.addEventListener("click", () => {
        $explorerFiles.querySelectorAll(".explorer-file-item").forEach((x) => x.classList.remove("active"));
        el.classList.add("active");
        loadExplorerPreview(sessionId, el.dataset.name);
      });
    });
  }

  async function loadExplorerPreview(sessionId, filename) {
    $explorerPreview.innerHTML = '<div class="empty-state">Loading...</div>';
    try {
      const resp = await fetch(`/api/jobs/${encodeURIComponent(sessionId)}/files/${encodeURIComponent(filename)}`);
      const data = await resp.json();
      if (data.error) {
        $explorerPreview.innerHTML = `<div class="empty-state">${esc(data.error)}</div>`;
        return;
      }
      const lang = detectLanguage(filename);
      const downloadUrl = `/api/jobs/${encodeURIComponent(sessionId)}/files/${encodeURIComponent(filename)}`;
      $explorerPreview.innerHTML = `<div class="explorer-preview-header">
        <span class="explorer-preview-filename">${esc(filename)}</span>
        <span class="explorer-preview-size">${formatBytes(data.size)}</span>
        <button class="btn-explorer-download-file" data-url="${esc(downloadUrl)}">Download</button>
      </div><pre><code class="${esc(lang)}">${esc(data.content)}</code></pre>`;
      // Apply syntax highlighting
      const codeEl = $explorerPreview.querySelector("code");
      if (codeEl && window.hljs) window.hljs.highlightElement(codeEl);
      // Wire download button
      $explorerPreview.querySelector(".btn-explorer-download-file")?.addEventListener("click", (e) => {
        const blob = new Blob([data.content], { type: "text/plain" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = filename;
        a.click();
        URL.revokeObjectURL(a.href);
      });
    } catch (e) {
      $explorerPreview.innerHTML = `<div class="empty-state">Error: ${esc(e.message)}</div>`;
    }
  }

  // --- User Auth & Admin ---
  let currentUser = null;

  const $userMenu = $("user-menu");
  const $userName = $("user-name");
  const $userRole = $("user-role");
  const $btnAdmin = $("btn-admin");
  const $btnLogout = $("btn-logout");
  const $adminModal = $("admin-modal");
  const $btnCloseAdmin = $("btn-close-admin");
  const $adminUsersBody = $("admin-users-body");
  const $adminPendingBody = $("admin-pending-body");
  const $pendingSection = $("pending-section");
  const $pendingCount = $("pending-count");
  const $btnAdminAdd = $("btn-admin-add");
  const $adminNewLogin = $("admin-new-login");
  const $adminNewRole = $("admin-new-role");

  async function fetchCurrentUser() {
    try {
      const resp = await fetch("/auth/me");
      if (!resp.ok) return;
      const data = await resp.json();
      if (!data.authenticated) return;
      currentUser = data;
      $userName.textContent = data.name || data.github_login || data.email;
      $userRole.textContent = data.role;
      $userRole.className = "user-role-badge " + data.role;
      $userMenu.classList.remove("hidden");
      if (data.role === "admin") {
        $btnAdmin.classList.remove("hidden");
        fetchPendingCount();
      }
    } catch (e) { /* not authenticated */ }
  }

  async function fetchPendingCount() {
    try {
      const resp = await fetch("/api/admin/pending");
      if (!resp.ok) return;
      const data = await resp.json();
      const count = (data.pending || []).length;
      if (count > 0) {
        $btnAdmin.innerHTML = `Admin <span class="pending-badge-header">${count}</span>`;
      } else {
        $btnAdmin.textContent = "Admin";
      }
    } catch (e) { /* ignore */ }
  }

  $btnLogout.addEventListener("click", async () => {
    await fetch("/auth/logout", { method: "POST" });
    location.href = "/login";
  });

  $btnAdmin.addEventListener("click", () => {
    $adminModal.classList.remove("hidden");
    loadAdminUsers();
    loadPendingRequests();
  });

  $btnCloseAdmin.addEventListener("click", () => {
    $adminModal.classList.add("hidden");
  });

  async function loadAdminUsers() {
    try {
      const resp = await fetch("/api/admin/users");
      if (!resp.ok) return;
      const data = await resp.json();
      renderAdminUsers(data.users || []);
    } catch (e) {
      $adminUsersBody.innerHTML = '<tr><td colspan="6">Failed to load users</td></tr>';
    }
  }

  function renderAdminUsers(users) {
    $adminUsersBody.innerHTML = "";
    for (const u of users) {
      const tr = document.createElement("tr");
      const isActive = u.active !== false;
      tr.innerHTML = `
        <td><strong>${esc(u.github_login)}</strong></td>
        <td>${esc(u.name || "-")}</td>
        <td>${esc(u.email || "-")}</td>
        <td>
          <select class="admin-role-select" data-login="${esc(u.github_login)}">
            <option value="viewer" ${u.role === "viewer" ? "selected" : ""}>Viewer</option>
            <option value="developer" ${u.role === "developer" ? "selected" : ""}>Developer</option>
            <option value="admin" ${u.role === "admin" ? "selected" : ""}>Admin</option>
          </select>
        </td>
        <td class="${isActive ? "active-yes" : "active-no"}">${isActive ? "Yes" : "No"}</td>
        <td class="admin-actions">
          ${isActive
            ? `<button class="btn-deactivate" data-login="${esc(u.github_login)}">Deactivate</button>`
            : `<button class="btn-activate" data-login="${esc(u.github_login)}">Activate</button>`
          }
        </td>
      `;
      $adminUsersBody.appendChild(tr);
    }

    // Role change handlers
    $adminUsersBody.querySelectorAll(".admin-role-select").forEach((sel) => {
      sel.addEventListener("change", async () => {
        const login = sel.dataset.login;
        const role = sel.value;
        await fetch(`/api/admin/users/${encodeURIComponent(login)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role }),
        });
        loadAdminUsers();
      });
    });

    // Deactivate/Activate handlers
    $adminUsersBody.querySelectorAll(".btn-deactivate").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const login = btn.dataset.login;
        await fetch(`/api/admin/users/${encodeURIComponent(login)}`, { method: "DELETE" });
        loadAdminUsers();
      });
    });

    $adminUsersBody.querySelectorAll(".btn-activate").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const login = btn.dataset.login;
        await fetch("/api/admin/users", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ github_login: login, role: "viewer" }),
        });
        loadAdminUsers();
      });
    });
  }

  async function loadPendingRequests() {
    try {
      const resp = await fetch("/api/admin/pending");
      if (!resp.ok) return;
      const data = await resp.json();
      const pending = data.pending || [];
      $pendingCount.textContent = pending.length;
      if (pending.length > 0) {
        $pendingSection.classList.remove("hidden");
        renderPendingRequests(pending);
      } else {
        $pendingSection.classList.add("hidden");
      }
    } catch (e) {
      $pendingSection.classList.add("hidden");
    }
  }

  function renderPendingRequests(pending) {
    $adminPendingBody.innerHTML = "";
    for (const p of pending) {
      const tr = document.createElement("tr");
      const ago = timeAgo(p.requested_at);
      tr.innerHTML = `
        <td><strong>${esc(p.github_login)}</strong></td>
        <td>${esc(p.name || "-")}</td>
        <td>${esc(p.email || "-")}</td>
        <td>${ago}</td>
        <td class="admin-actions">
          <select class="pending-role-select" data-login="${esc(p.github_login)}">
            <option value="viewer">Viewer</option>
            <option value="developer" selected>Developer</option>
            <option value="admin">Admin</option>
          </select>
          <button class="btn-approve" data-login="${esc(p.github_login)}">Approve</button>
          <button class="btn-reject" data-login="${esc(p.github_login)}">Reject</button>
        </td>
      `;
      $adminPendingBody.appendChild(tr);
    }

    $adminPendingBody.querySelectorAll(".btn-approve").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const login = btn.dataset.login;
        const row = btn.closest("tr");
        const role = row.querySelector(".pending-role-select").value;
        await fetch(`/api/admin/pending/${encodeURIComponent(login)}/approve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role }),
        });
        loadPendingRequests();
        loadAdminUsers();
        fetchPendingCount();
      });
    });

    $adminPendingBody.querySelectorAll(".btn-reject").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const login = btn.dataset.login;
        await fetch(`/api/admin/pending/${encodeURIComponent(login)}`, { method: "DELETE" });
        loadPendingRequests();
        fetchPendingCount();
      });
    });
  }

  function timeAgo(ts) {
    const diff = Math.floor(Date.now() / 1000 - ts);
    if (diff < 60) return "just now";
    if (diff < 3600) return Math.floor(diff / 60) + "m ago";
    if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
    return Math.floor(diff / 86400) + "d ago";
  }

  function esc(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }

  $btnAdminAdd.addEventListener("click", async () => {
    const login = $adminNewLogin.value.trim();
    const role = $adminNewRole.value;
    if (!login) return;
    await fetch("/api/admin/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ github_login: login, role }),
    });
    $adminNewLogin.value = "";
    loadAdminUsers();
  });

  // --- Init ---
  // Sequential loading to avoid HTTP/1.1 connection slot exhaustion.
  // Models load FIRST (highest priority), then remaining fetches, then WebSockets.
  initCollapsible();
  (async function init() {
    // Phase 1: Models — must complete before anything else
    await loadModels();

    // Phase 2: Other HTTP fetches (parallel, max 5 at once to leave 1 slot free)
    await Promise.all([
      loadAgents(),
      fetchCurrentUser(),
      loadPresets(),
      fetchUsageStats(),
      startNewConversation(),
    ]);

    // Phase 3: Secondary fetches + WebSockets (all HTTP fetches done, slots are free)
    restoreSessionHistory();

    fetch("/api/events?limit=200")
      .then((r) => r.json())
      .then((data) => { events = data; data.forEach((e) => renderTimelineEvent(e)); })
      .catch(() => {})
      .finally(() => connect());

    fetch("/api/snapshot")
      .then((r) => r.json())
      .then((data) => { snapshot = data; renderHeader(); renderGraph(); renderAgentMessages(); })
      .catch(() => {});

    connectStream();
    const check = setInterval(() => {
      if (streamWs && streamWs.readyState === WebSocket.OPEN) { clearInterval(check); setupStreamHandler(); }
    }, 200);
  })();

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
