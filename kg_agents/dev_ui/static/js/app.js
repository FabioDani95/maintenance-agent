/* ============================================================
   Maintenance Workspace — vanilla JS
   Backend: kg_agents FastAPI under /v1/kg-agents
   ============================================================ */

const API = "/v1/kg-agents";

const State = {
  instanceId: null,
  sessionId: null,
  productMeta: null,
  status: null,
  logsSummary: null,
  // last chat response & derived
  lastResponse: null,
  lastIntent: null,
  evidenceMode: "top",
  // selection in action plan
  selectedActionId: null,
  pickedOutcome: null,
  // chart
  chart: null,
  selectedSignal: null,
  trendsRangeHours: 24,
  // ui
  activeTab: "action-plan",
  density: "operator",
  rightCollapsed: false,
  // KG
  kgInited: false,
  // recent actions log
  recentActions: [],
};

// ============== Utilities ==============
const $  = sel => document.querySelector(sel);
const $$ = sel => Array.from(document.querySelectorAll(sel));

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const child of [].concat(children)) {
    if (child == null) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function toast(msg, kind = "info", ms = 2400) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (kind === "error" ? " error" : "");
  setTimeout(() => t.classList.add("hidden"), ms);
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// Replace [MANUAL:title:page] tokens with clickable links (DigiFactor parity)
function processManualTokens(text) {
  if (!text) return text;
  return text.replace(/\[MANUAL:([^:\]]+):(\d+)\]/g, (_, title, page) => {
    const url = `/manuals/${encodeURIComponent(title)}.pdf#page=${page}`;
    return `[${title} — p. ${page}](${url})`;
  });
}

// Strip markdown formatting from short strings (used as plain values in
// inputs, titles, tooltips where we want the bare text).
function stripMarkdown(s) {
  if (!s) return "";
  return String(s)
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1");
}

// Inline markdown — bold/code/links only, no block elements (paragraphs, lists).
// Use for clarification labels, option chips, button content.
function renderMarkdownInline(src) {
  if (!src) return "";
  let s = escapeHtml(processManualTokens(src));
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, href) => {
    const isPdf = /\.pdf(\?|#|$)/i.test(href);
    return isPdf
      ? `<a href="${href}" data-pdf="1">${label}</a>`
      : `<a href="${href}" target="_blank" rel="noopener">${label}</a>`;
  });
  return s;
}

// Tiny markdown renderer (paragraphs, bold, code, lists, links)
// Normalize inline numbered lists: "1. Foo. 2. Bar. 3. Baz" → newline-separated.
// The backend sometimes concatenates steps on a single line; we want each
// "N. …" on its own line so the markdown list regex kicks in.
function normalizeInlineNumberedList(s) {
  if (!s) return s;
  // Insert newline before " N. " (or " N) " or "; N. ") that follows
  // sentence-ending punctuation or whitespace, when several such markers
  // appear on the same line.
  return s.replace(/([^\n])\s+(?=\d+[.)]\s)/g, (match, prev, offset, full) => {
    // Cheap heuristic: only split if the *line* contains ≥2 "N." markers.
    const lineStart = full.lastIndexOf("\n", offset) + 1;
    const lineEnd = full.indexOf("\n", offset);
    const line = full.slice(lineStart, lineEnd === -1 ? full.length : lineEnd);
    const count = (line.match(/(?:^|\s)\d+[.)]\s/g) || []).length;
    if (count < 2) return match;
    return prev + "\n";
  });
}

function renderMarkdown(src) {
  if (!src) return "";
  let s = escapeHtml(processManualTokens(src));
  // Break "1. A 2. B 3. C" same-line lists into proper newline-separated items
  s = normalizeInlineNumberedList(s);
  // Horizontal rules (---, ***, ___ on their own line)
  s = s.replace(/(?:^|\n)\s*(?:---|\*\*\*|___)\s*(?=\n|$)/g, "\n<hr/>");
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  // Italic: single asterisks (avoid matching inside already-replaced <strong>)
  s = s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
  // Open PDF links in overlay; external links in new tab
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, href) => {
    const isPdf = /\.pdf(\?|#|$)/i.test(href);
    if (isPdf) return `<a href="${href}" data-pdf="1">${label}</a>`;
    return `<a href="${href}" target="_blank" rel="noopener">${label}</a>`;
  });
  // headings
  s = s.replace(/^###\s+(.*)$/gm, "<h3>$1</h3>");
  // lists
  s = s.replace(/(?:^|\n)((?:[-*]\s+.+\n?)+)/g, (m, blk) => {
    const items = blk.trim().split(/\n/).map(l => l.replace(/^[-*]\s+/, ""));
    return "\n<ul>" + items.map(i => `<li>${i}</li>`).join("") + "</ul>";
  });
  s = s.replace(/(?:^|\n)((?:\d+\.\s+.+\n?)+)/g, (m, blk) => {
    const items = blk.trim().split(/\n/).map(l => l.replace(/^\d+\.\s+/, ""));
    return "\n<ol>" + items.map(i => `<li>${i}</li>`).join("") + "</ol>";
  });
  // paragraphs
  s = s.split(/\n{2,}/).map(p => {
    if (/^<(h3|ul|ol|p|table)/.test(p.trim())) return p;
    return "<p>" + p.replace(/\n/g, "<br/>") + "</p>";
  }).join("\n");
  return s;
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toISOString().slice(0, 16).replace("T", " ");
}

function shortId(id) { return id ? String(id).slice(0, 8) : "—"; }

function logAction(label) {
  const now = new Date();
  State.recentActions.unshift({ time: now.toTimeString().slice(0,5), label });
  State.recentActions = State.recentActions.slice(0, 8);
  renderRecentActions();
}

// ============== API wrappers ==============
async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let detail;
    try { detail = await res.json(); } catch { detail = await res.text(); }
    const msg = (detail && (detail.detail || detail.error)) || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json();
}

const apiGet  = (p)       => api(p);
const apiPost = (p, body) => api(p, { method: "POST", body: JSON.stringify(body || {}) });

// ============== Boot ==============
document.addEventListener("DOMContentLoaded", boot);

async function boot() {
  wireGlobalUI();
  await loadInstances();
  if (!State.instanceId) return;
  await loadInstanceContext(State.instanceId);
}

function wireGlobalUI() {
  // Initial density class
  document.body.classList.add("density-operator");

  // Sidebar collapse
  $("#sidebar-toggle").addEventListener("click", () => {
    document.body.classList.toggle("sidebar-collapsed");
  });

  // Tab buttons
  $$(".tab-btn").forEach(b => b.addEventListener("click", () => setActiveTab(b.dataset.tab)));
  $$(".qn-btn").forEach(b => b.addEventListener("click", () => setActiveTab(b.dataset.tab)));
  // Density
  $("#density-select").addEventListener("change", e => {
    State.density = e.target.value;
    document.body.classList.toggle("density-operator", State.density === "operator");
    document.body.classList.toggle("density-service",  State.density === "service");
  });
  // Right collapse
  $("#right-toggle").addEventListener("click", () => {
    State.rightCollapsed = !State.rightCollapsed;
    document.body.classList.toggle("right-collapsed", State.rightCollapsed);
  });
  // Instance select
  $("#instance-select").addEventListener("change", e => loadInstanceContext(e.target.value));
  // Composer
  $("#composer-send").addEventListener("click", onSend);
  $("#composer-input").addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSend(); }
  });
  // CTAs
  $("#new-case-btn").addEventListener("click", onNewCase);
  $("#cta-next-issue").addEventListener("click", onNextIssue);
  $("#ap-next-btn").addEventListener("click", onNextIssue);
  const resetZoom = $("#chart-reset-zoom");
  if (resetZoom) resetZoom.addEventListener("click", () => State.chart && State.chart.resetZoom());
  $("#cta-similar").addEventListener("click", () => {
    const issue = State.lastResponse?.current_issue;
    if (!issue) return;
    $("#composer-input").value = `Has "${issue.failure_mode_name}" happened before? Show me past cases.`;
    onSend();
  });
  $("#cta-workorders").addEventListener("click", () => setActiveTab("manuals-wo", { sub: "wo" }));
  $("#cta-reset").addEventListener("click", onResetCase);
  // Evidence mode
  $$("#evidence-mode .seg-btn").forEach(b => b.addEventListener("click", () => {
    $$("#evidence-mode .seg-btn").forEach(x => x.classList.toggle("active", x === b));
    State.evidenceMode = b.dataset.mode;
    renderEvidence();
  }));
  // History mode
  $$("#history-mode .seg-btn").forEach(b => b.addEventListener("click", () => {
    $$("#history-mode .seg-btn").forEach(x => x.classList.toggle("active", x === b));
    runHistoryQuery();
  }));
  // History filters
  $("#hf-apply").addEventListener("click", () => runHistoryQuery(0));
  $("#hf-clear").addEventListener("click", () => {
    ["hf-q","hf-severity","hf-status","hf-event-category","hf-maintenance-type","hf-from","hf-to"]
      .forEach(id => { const e = $("#"+id); e.value = ""; });
    runHistoryQuery(0);
  });
  // Outcome buttons
  $$(".outcome-btn").forEach(b => b.addEventListener("click", () => onLogOutcome(b.dataset.outcome, b)));
  // Trends range
  $$("#trends-range .seg-btn").forEach(b => b.addEventListener("click", () => {
    $$("#trends-range .seg-btn").forEach(x => x.classList.toggle("active", x === b));
    State.trendsRangeHours = parseInt(b.dataset.range, 10);
    renderTrends();
  }));
  $("#trends-signal-select").addEventListener("change", e => {
    State.selectedSignal = e.target.value;
    renderTrends();
  });
  // M&WO subtabs
  $$("#mwo-mode .seg-btn").forEach(b => b.addEventListener("click", () => {
    $$("#mwo-mode .seg-btn").forEach(x => x.classList.toggle("active", x === b));
    const sub = b.dataset.sub;
    $("#mwo-manuals").classList.toggle("hidden", sub !== "manuals");
    $("#mwo-wo").classList.toggle("hidden", sub !== "wo");
  }));
  // Reload caches
  $("#reload-btn").addEventListener("click", onReloadCaches);
  // Advanced rail
  $("#open-advanced-btn").addEventListener("click", () => {
    $("#density-select").value = "service";
    State.density = "service";
    document.body.classList.remove("density-operator");
    setActiveTab("advanced");
  });
  // PDF close
  $("#pdf-close-btn").addEventListener("click", () => {
    $("#pdf-overlay").classList.add("hidden");
    $("#pdf-iframe").src = "about:blank";
  });

  // Chat panel input (right side)
  const chatInput = $("#chat-input");
  const chatSend = $("#chat-send");
  if (chatSend) chatSend.addEventListener("click", onChatSend);
  if (chatInput) chatInput.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onChatSend(); }
  });

  // Column resizers
  initColumnResizers();

  // Global delegate for PDF links inside markdown (chat / runbook / etc.)
  document.addEventListener("click", e => {
    const a = e.target.closest && e.target.closest('a[data-pdf="1"]');
    if (!a) return;
    e.preventDefault();
    openManual(a.getAttribute("href"), a.textContent);
  });
}

function onChatSend() {
  const inp = $("#chat-input");
  const v = (inp.value || "").trim();
  if (!v) return;
  $("#composer-input").value = v;
  inp.value = "";
  onSend();
}

function initColumnResizers() {
  const workspace = $("#workspace");
  if (!workspace) return;
  let active = null;

  // Snapshot the current grid template into resolved pixel sizes so that
  // dragging one column doesn't redistribute space across the others.
  function freezeColumns() {
    const cs = getComputedStyle(workspace).gridTemplateColumns.split(/\s+/).map(parseFloat);
    if (cs.length === 5) {
      workspace.style.gridTemplateColumns = `${cs[0]}px ${cs[1]}px ${cs[2]}px ${cs[3]}px ${cs[4]}px`;
    }
  }

  $$(".col-resizer").forEach(r => {
    r.addEventListener("mousedown", e => {
      freezeColumns();
      active = {
        which: r.dataset.resize,
        startX: e.clientX,
        rect: workspace.getBoundingClientRect(),
        cols: getComputedStyle(workspace).gridTemplateColumns.split(/\s+/).map(parseFloat),
      };
      r.classList.add("dragging");
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      e.preventDefault();
    });
  });

  document.addEventListener("mousemove", e => {
    if (!active) return;
    const dx = e.clientX - active.startX;
    const [c0, g0, c1, g1, c2] = active.cols;
    let left = c0, center = c1, right = c2;
    if (active.which === "left") {
      left = Math.max(160, Math.min(c0 + dx, c0 + c1 - 360));
      center = c0 + c1 - left;
    } else if (active.which === "right") {
      // dragging right edge: center grows when dx > 0, right column shrinks
      center = Math.max(360, Math.min(c1 + dx, c1 + c2 - 200));
      right = c1 + c2 - center;
    }
    workspace.style.gridTemplateColumns = `${left}px ${g0}px ${center}px ${g1}px ${right}px`;
    if (State.chart) State.chart.resize();
  });

  document.addEventListener("mouseup", () => {
    if (!active) return;
    $$(".col-resizer").forEach(r => r.classList.remove("dragging"));
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    active = null;
  });
}

// ============== Tab management ==============
function setActiveTab(name, opts = {}) {
  State.activeTab = name;
  $$(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  $$(".qn-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab-panel").forEach(p => p.classList.toggle("active", p.dataset.panel === name));
  if (name === "advanced") loadKGGraph();
  if (name === "trends") renderTrends();
  if (name === "history") { if (!State._historyLoaded) { runHistoryQuery(0); State._historyLoaded = true; } }
  if (name === "manuals-wo" && opts.sub) {
    $$("#mwo-mode .seg-btn").forEach(x => {
      const on = x.dataset.sub === opts.sub;
      x.classList.toggle("active", on);
    });
    $("#mwo-manuals").classList.toggle("hidden", opts.sub !== "manuals");
    $("#mwo-wo").classList.toggle("hidden", opts.sub !== "wo");
  }
}

// ============== Instance loading ==============
async function loadInstances() {
  try {
    const data = await apiGet("/instances");
    const sel = $("#instance-select");
    sel.innerHTML = "";
    (data.instances || []).forEach(inst => {
      const opt = el("option", { value: inst.id }, [inst.name + " — " + inst.agent_name]);
      sel.appendChild(opt);
    });
    if (data.instances && data.instances.length) {
      // Default: prefer the seeded IRC5 instance (id = "irc5-default-instance",
      // which has symptom_embeddings.json). Fallback to any IRC5-named
      // instance, then to the first available.
      const preferred = data.instances.find(i => i.id === "irc5-default-instance")
        || data.instances.find(i => (i.name || "").trim().toLowerCase() === "irc5")
        || data.instances.find(i => /irc5/i.test(i.name || ""))
        || data.instances[0];
      State.instanceId = preferred.id;
      sel.value = State.instanceId;
      const bcInst = $("#bc-instance");
      if (bcInst) bcInst.textContent = preferred.name;
      const bcPlant = $("#bc-plant");
      if (bcPlant && preferred.agent_name) bcPlant.textContent = preferred.agent_name;
    }
  } catch (e) {
    toast("Failed to load instances: " + e.message, "error");
  }
}

async function loadInstanceContext(instanceId) {
  State.instanceId = instanceId;
  // Update breadcrumb instance label
  const sel = $("#instance-select");
  const opt = sel?.querySelector(`option[value="${instanceId}"]`);
  if (opt) {
    const bcInst = $("#bc-instance");
    if (bcInst) bcInst.textContent = opt.textContent.split(" — ")[0] || opt.textContent;
  }
  // Restore last session for this instance (if persisted), so a page refresh
  // continues the current chat instead of creating a new one in the list.
  State.sessionId = restoreSessionId();
  State.lastResponse = null;
  State.selectedActionId = null;
  State._historyLoaded = false;
  $("#thread").innerHTML = "";
  $("#ap-empty").classList.remove("hidden");
  $("#ap-runbook").classList.add("hidden");
  $("#evidence-list").innerHTML = '<div class="panel-empty">No log evidence yet. Run a diagnosis or search history.</div>';

  try {
    const [prod, status, summary, sessions, instMeta] = await Promise.all([
      apiGet(`/instances/${instanceId}/product-info`).catch(() => null),
      apiGet(`/instances/${instanceId}/status`).catch(() => null),
      apiGet(`/instances/${instanceId}/logs/summary`).catch(() => null),
      apiGet(`/instances/${instanceId}/chat-sessions?limit=8`).catch(() => null),
      apiGet(`/instances/${instanceId}`).catch(() => null),
    ]);
    State.productMeta = prod;
    State.status = status;
    State.logsSummary = summary;
    State.instanceMeta = instMeta;
    renderIdentity();
    renderHealth();
    renderKGStats();
    renderChips();
    renderRecentSessions(sessions?.sessions || []);
    populateHistoryFilters();
    renderCaseCard();
    discoverInstanceManuals();
  } catch (e) {
    toast("Failed to load instance context: " + e.message, "error");
  }
}

function renderIdentity() {
  const p = State.productMeta || {};
  $("#product-name").textContent = p.product_name || "—";
  $("#product-type").textContent = p.product_type || "—";
  $("#product-short").textContent = p.product_short_name || "—";
  $("#ontology-version").textContent = State.status?.ontology_version ? `ontology v${State.status.ontology_version}` : "";
}

function renderHealth() {
  const s = State.logsSummary;
  const open = s?.open_events || 0;
  const sev = s?.severity_distribution || {};
  const hasCrit = (sev["4"] || sev[4] || 0) > 0;
  const hasWarn = (sev["3"] || sev[3] || 0) > 0;
  const pill = $("#health-pill");
  pill.className = "status-pill";
  if (hasCrit) { pill.classList.add("crit"); pill.textContent = "Critical"; }
  else if (hasWarn || open > 0) { pill.classList.add("warn"); pill.textContent = "Warning"; }
  else { pill.classList.add("ok"); pill.textContent = "OK"; }
  $("#asset-meta-open").textContent = `${open} open event${open === 1 ? "" : "s"}`;
  $("#asset-meta-updated").textContent = s ? `${s.row_count} logs indexed` : "no log data";
}

function renderKGStats() {
  const m = State.instanceMeta || {};
  const s = State.status || {};
  const n = m.node_count ?? s.total_nodes ?? "—";
  const e = m.relationship_count ?? s.total_relationships ?? "—";
  const nEl = $("#kg-node-count"); if (nEl) nEl.textContent = n;
  const eEl = $("#kg-edge-count"); if (eEl) eEl.textContent = e;
}

function renderChips() {
  const chips = State.productMeta?.suggested_symptoms || [];
  // Center composer chips
  const wrap = $("#composer-chips");
  if (wrap) {
    wrap.innerHTML = "";
    chips.forEach(c => {
      wrap.appendChild(el("span", {
        class: "chip",
        onclick: () => { $("#composer-input").value = c.query; $("#composer-input").focus(); }
      }, [c.label]));
    });
  }
  // Right chat panel chips (mirror)
  const chatWrap = $("#chat-chips");
  if (chatWrap) {
    chatWrap.innerHTML = "";
    chips.forEach(c => {
      chatWrap.appendChild(el("span", {
        class: "chip",
        onclick: () => { $("#chat-input").value = c.query; $("#chat-input").focus(); }
      }, [c.label]));
    });
  }
}

function relativeTime(iso) {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (isNaN(t)) return iso;
  const diff = (Date.now() - t) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return Math.floor(diff / 60) + " min ago";
  if (diff < 86400) return Math.floor(diff / 3600) + " h ago";
  if (diff < 604800) return Math.floor(diff / 86400) + " d ago";
  return new Date(iso).toISOString().slice(0, 10);
}

function truncate(s, n) {
  if (!s) return "";
  const trimmed = String(s).replace(/\s+/g, " ").trim();
  return trimmed.length > n ? trimmed.slice(0, n - 1) + "…" : trimmed;
}

function renderRecentSessions(sessions) {
  const list = $("#recent-sessions");
  list.innerHTML = "";
  if (!sessions || !sessions.length) {
    list.appendChild(el("div", { class: "recent-empty" }, ["No chats yet"]));
    return;
  }

  // Real chats only: user message present + at least 2 turns logged
  // (i.e. one exchange — otherwise it's a half-baked session from a refresh).
  let candidates = sessions.filter(s =>
    (s.first_user_message || "").trim().length > 0 &&
    (s.message_count || 0) >= 2
  );

  // Always keep the currently active session even if it has only 1 msg yet.
  if (State.sessionId && !candidates.some(s => s.session_id === State.sessionId)) {
    const active = sessions.find(s => s.session_id === State.sessionId);
    if (active && (active.first_user_message || "").trim()) candidates.unshift(active);
  }

  // Sort newest first
  candidates.sort((a, b) => (b.last_message_at || "").localeCompare(a.last_message_at || ""));

  // Dedupe by normalized first_user_message: keep most recent occurrence
  const seen = new Set();
  const real = [];
  for (const s of candidates) {
    const key = (s.first_user_message || "").trim().toLowerCase().replace(/\s+/g, " ");
    if (seen.has(key)) continue;
    seen.add(key);
    real.push(s);
    if (real.length >= 8) break;
  }

  if (!real.length) {
    list.appendChild(el("div", { class: "recent-empty" }, ["No chats yet"]));
    return;
  }

  real.forEach(s => {
    const item = el("div", {
      class: "recent-item" + (s.session_id === State.sessionId ? " active" : ""),
      onclick: () => resumeSession(s.session_id),
      title: s.first_user_message || "",
    }, [
      el("div", { class: "ri-row" }, [
        el("i", { class: "fa fa-comment ri-icon" }),
        el("div", { class: "ri-first" }, [truncate(s.first_user_message, 42) || "(empty chat)"]),
      ]),
      el("div", { class: "ri-meta" }, [
        el("span", {}, [`${s.message_count} msg`]),
        el("span", { class: "ri-dot" }, ["·"]),
        el("span", {}, [relativeTime(s.last_message_at)]),
      ]),
    ]);
    list.appendChild(item);
  });
}

async function resumeSession(sessionId) {
  try {
    const data = await apiGet(`/instances/${State.instanceId}/chat-sessions/${sessionId}`);
    State.sessionId = sessionId;
    persistSessionId();
    $("#thread").innerHTML = "";
    (data.messages || []).forEach(m => appendThread(m.role, m.content));
    $("#meta-session").textContent = shortId(sessionId);
    toast("Session resumed");
    logAction(`resumed session ${shortId(sessionId)}`);
  } catch (e) {
    toast("Failed to resume: " + e.message, "error");
  }
}

function populateHistoryFilters() {
  const s = State.logsSummary;
  if (!s) return;
  // event categories: derived from top signatures (best-effort fallback)
  // Severity / status / etc. left as static dropdowns; values from API are dynamic but cardinality is bounded.
  const statusOpts = ["open", "in_progress", "closed", "resolved"];
  const evCatOpts = ["alarm", "warning", "event", "maintenance", "info"];
  const mtOpts = ["corrective", "preventive", "inspection", "calibration"];
  fillSelect("hf-status", statusOpts);
  fillSelect("hf-event-category", evCatOpts);
  fillSelect("hf-maintenance-type", mtOpts);
}
function fillSelect(id, opts) {
  const sel = $("#"+id);
  // keep first (Any) option
  const first = sel.querySelector("option");
  sel.innerHTML = "";
  sel.appendChild(first);
  opts.forEach(v => sel.appendChild(el("option", { value: v }, [v])));
}

// ============== Chat / case flow ==============
function onNewCase() {
  clearSessionId();
  State.sessionId = null;
  State.lastResponse = null;
  State.selectedActionId = null;
  $("#thread").innerHTML = "";
  $("#ap-empty").classList.remove("hidden");
  $("#ap-runbook").classList.add("hidden");
  $("#evidence-list").innerHTML = '<div class="panel-empty">No log evidence yet.</div>';
  $("#meta-session").textContent = "—";
  renderCaseCard();
  toast("New case started");
}

async function onSend() {
  const inp = $("#composer-input");
  const message = (inp.value || "").trim();
  if (!message) return;
  if (!State.instanceId) { toast("Pick an asset first", "error"); return; }

  inp.value = "";
  appendThread("user", message);
  const placeholder = appendTypingIndicator();

  const mode = $("#chat-mode-select").value;
  try {
    const body = { message, session_id: State.sessionId || undefined, mode };
    const resp = await apiPost(`/instances/${State.instanceId}/chat`, body);
    if (placeholder) placeholder.remove();
    State.sessionId = resp.session_id;
    persistSessionId();
    handleChatResponse(resp);
  } catch (e) {
    if (placeholder) {
      placeholder.className = "msg assistant";
      placeholder.textContent = "Error: " + e.message;
    }
    toast(e.message, "error");
  }
}

function sessionKey() { return State.instanceId ? `kgua_session_${State.instanceId}` : null; }
function persistSessionId() {
  const k = sessionKey();
  if (k && State.sessionId) try { localStorage.setItem(k, State.sessionId); } catch {}
}
function restoreSessionId() {
  const k = sessionKey();
  if (!k) return null;
  try { return localStorage.getItem(k); } catch { return null; }
}
function clearSessionId() {
  const k = sessionKey();
  if (k) try { localStorage.removeItem(k); } catch {}
}

async function onNextIssue() {
  if (!State.sessionId) return;
  const mode = $("#chat-mode-select").value;
  const placeholder = appendTypingIndicator();
  try {
    const resp = await apiPost(`/instances/${State.instanceId}/next-issue`, { session_id: State.sessionId, mode });
    if (placeholder) placeholder.remove();
    handleChatResponse(resp);
    logAction("next possible cause");
  } catch (e) {
    if (placeholder) {
      placeholder.className = "msg assistant";
      placeholder.textContent = "Error: " + e.message;
    }
    toast(e.message, "error");
  }
}

async function onResetCase() {
  if (!State.sessionId) return;
  try {
    await apiPost(`/instances/${State.instanceId}/reset`, { session_id: State.sessionId });
    onNewCase();
    logAction("reset case");
  } catch (e) { toast(e.message, "error"); }
}

function handleChatResponse(resp) {
  State.lastResponse = resp;
  State.lastIntent = resp.intent || "troubleshooting_current";
  appendThread("assistant", resp.reply || "(no reply)", resp);
  $("#meta-session").textContent = shortId(resp.session_id);
  $("#meta-intent").textContent = resp.intent || "—";
  $("#meta-last").textContent = new Date().toTimeString().slice(0, 8);
  $("#meta-timing").textContent = resp.timings?.total_s ? resp.timings.total_s + "s" : "—";
  logAction(`chat: ${resp.intent || "—"}${resp.awaiting_clarification ? " (clarify)" : ""}`);
  renderCaseCard();
  renderActionPlan();
  renderEvidence();
  // Manuals + WO derive from log_evidence
  renderManualsAndWO();
  // Trends: re-populate signal list from telemetry
  primeTrendsFromResponse(resp);
  // Intent-aware default tab
  routeIntentToTab(resp);
}

function routeIntentToTab(resp) {
  const intent = resp.intent;
  if (resp.awaiting_clarification) {
    setActiveTab("action-plan");
    return;
  }
  if (intent === "log_history_search") setActiveTab("evidence");
  else if (intent === "log_analytics") setActiveTab("history");
  else if (intent === "work_order_lookup") setActiveTab("manuals-wo", { sub: "wo" });
  else if (intent === "hybrid_diagnosis_with_history") setActiveTab("action-plan");
  else setActiveTab("action-plan");
}

// ============== Case card ==============
function renderCaseCard() {
  const resp = State.lastResponse;
  const intentChip = $("#intent-chip");
  intentChip.className = "intent-chip";
  if (!resp) {
    intentChip.textContent = "no case";
    $("#case-issue-name").textContent = "Ask something to start a diagnosis.";
    $("#case-component").textContent = "";
    ["cta-next-issue","cta-similar","cta-workorders","cta-reset"].forEach(id => $("#"+id).disabled = true);
    return;
  }
  intentChip.textContent = (resp.intent || "—").replace(/_/g, " ");
  if (resp.intent) intentChip.classList.add("intent-" + resp.intent);

  const issue = resp.current_issue;
  if (issue) {
    $("#case-issue-name").textContent = issue.failure_mode_name || "(unnamed failure mode)";
    $("#case-component").textContent = issue.component_name ? `Component: ${issue.component_name}` : "";
  } else if (resp.awaiting_clarification) {
    $("#case-issue-name").textContent = "Awaiting clarification";
    $("#case-component").textContent = "";
  } else {
    $("#case-issue-name").textContent = "(no failure mode resolved)";
    $("#case-component").textContent = "";
  }

  $("#cta-next-issue").disabled = !resp.has_more_issues;
  $("#cta-similar").disabled    = !issue;
  $("#cta-workorders").disabled = !(resp.log_evidence && resp.log_evidence.length);
  $("#cta-reset").disabled      = !State.sessionId;

  // total/issue counter
  if (resp.issue_number && resp.total_issues) {
    $("#confidence-chip").textContent = `${resp.issue_number}/${resp.total_issues}`;
    $("#confidence-chip").classList.remove("hidden");
  } else {
    $("#confidence-chip").classList.add("hidden");
  }
}

// ============== Action Plan rendering ==============
function renderActionPlan() {
  const resp = State.lastResponse;
  if (!resp) { $("#ap-empty").classList.remove("hidden"); $("#ap-runbook").classList.add("hidden"); return; }
  $("#ap-empty").classList.add("hidden");
  $("#ap-runbook").classList.remove("hidden");

  const issue = resp.current_issue;
  $("#ap-issue-name").innerHTML = renderMarkdownInline(issue?.failure_mode_name || (resp.awaiting_clarification ? "Need a bit more info" : "Diagnosis"));
  $("#ap-issue-sub").innerHTML = issue?.component_name ? "Component: " + renderMarkdownInline(issue.component_name) : "";
  $("#ap-reply").innerHTML = renderMarkdown(resp.reply);

  // Inline "Next possible cause" affordance when the backend has more causes
  const nextWrap = $("#ap-next-wrap");
  if (resp.has_more_issues && !resp.awaiting_clarification) {
    nextWrap.classList.remove("hidden");
    const n = resp.issue_number, t = resp.total_issues;
    $("#ap-next-meta").textContent = (n && t)
      ? `Showing cause ${n} of ${t}. Try the next most likely cause if this doesn't fit.`
      : `More possible causes available.`;
  } else {
    nextWrap.classList.add("hidden");
  }

  // Clarification
  const clarWrap = $("#ap-clarify");
  if (resp.awaiting_clarification) {
    clarWrap.classList.remove("hidden");
    $("#ap-clarify-q").innerHTML = renderMarkdown(resp.clarification_question || "Please clarify:");
    const opts = $("#ap-clarify-opts");
    opts.innerHTML = "";
    (resp.clarification_options || []).forEach(o => {
      const btn = el("button", {
        class: "clarify-opt",
        title: stripMarkdown(o.description || ""),
        onclick: () => { $("#composer-input").value = stripMarkdown(o.label); onSend(); }
      });
      btn.innerHTML = renderMarkdownInline(o.label || "");
      opts.appendChild(btn);
    });
  } else {
    clarWrap.classList.add("hidden");
  }

  // Action options
  const actWrap = $("#ap-actions-wrap");
  const opts = issue?.action_options || [];
  if (opts.length) {
    actWrap.classList.remove("hidden");
    const list = $("#ap-action-options");
    list.innerHTML = "";
    opts.forEach((opt, i) => {
      const card = el("div", { class: "action-card" + (i === 0 ? " selected" : "") });
      if (i === 0 && !State.selectedActionId) State.selectedActionId = opt.action_id;
      card.appendChild(el("div", { class: "action-name" }, [opt.action_name]));
      if (opt.source_title || opt.source_reference) {
        const src = el("div", { class: "action-source" });
        if (opt.source_title) src.appendChild(document.createTextNode(opt.source_title + " "));
        if (opt.source_reference) {
          src.appendChild(el("a", {
            onclick: () => openManual(opt.source_reference, opt.source_title)
          }, [opt.source_reference]));
        }
        card.appendChild(src);
      }
      if (opt.instruction_text) {
        const instr = el("div", { class: "action-instr markdown-body" });
        instr.innerHTML = renderMarkdown(opt.instruction_text);
        card.appendChild(instr);
      }
      if (opt.stats) {
        const s = opt.stats;
        const wrap = el("div", { class: "action-stats" });
        if (s.total_uses) {
          const pct = Math.round(s.success_rate_pct || 0);
          const tone = pct >= 75 ? "ok" : pct >= 50 ? "warn" : "bad";
          const label = `${pct}% · ${s.total_uses} ${s.total_uses === 1 ? "run" : "runs"}`;
          wrap.appendChild(el("span", { class: `stats-pill stats-${tone}` }, [label]));
          if (s.avg_duration_min) {
            wrap.appendChild(el("span", { class: "stats-meta" }, [`${s.avg_duration_min.toFixed(0)} min avg`]));
          }
        } else {
          wrap.appendChild(el("span", { class: "stats-pill stats-new" }, ["new — no history yet"]));
        }
        card.appendChild(wrap);
      }
      card.appendChild(el("div", { class: "action-pick" }, [
        el("button", {
          class: "btn btn-sm",
          onclick: () => {
            State.selectedActionId = opt.action_id;
            $$(".action-card").forEach((c, j) => c.classList.toggle("selected", c === card));
          }
        }, ["Select this action"])
      ]));
      list.appendChild(card);
    });
  } else {
    actWrap.classList.add("hidden");
  }

  // Reset outcome UI
  $$(".outcome-btn").forEach(b => b.classList.remove("picked"));
  $("#outcome-feedback").value = "";
  $("#outcome-status").textContent = "";
  State.pickedOutcome = null;
}

async function onLogOutcome(outcome, btn) {
  if (!State.sessionId) { toast("No active session", "error"); return; }
  State.pickedOutcome = outcome;
  $$(".outcome-btn").forEach(b => b.classList.toggle("picked", b === btn));
  const feedback = $("#outcome-feedback").value.trim();
  const body = {
    session_id: State.sessionId,
    outcome,
    user_feedback: feedback,
    selected_action_id: State.selectedActionId || undefined,
  };
  $("#outcome-status").textContent = "Saving…";
  try {
    const res = await apiPost(`/instances/${State.instanceId}/log-outcome`, body);
    $("#outcome-status").textContent = `Saved — path success now ${res.stats.success_rate_pct.toFixed(0)}% (${res.stats.total_uses} uses)`;
    logAction(`outcome: ${outcome}`);
  } catch (e) {
    $("#outcome-status").textContent = "Failed: " + e.message;
  }
}

// ============== Evidence rendering ==============
function renderEvidence() {
  const list = $("#evidence-list");
  const evidence = State.lastResponse?.log_evidence || [];
  if (!evidence.length) {
    list.innerHTML = '<div class="panel-empty">No log evidence in the latest response.</div>';
    $("#evidence-meta").textContent = "";
    return;
  }
  $("#evidence-meta").textContent = `${evidence.length} signatures`;
  list.innerHTML = "";

  evidence.forEach(m => {
    const top = m.top_match_log || {};
    const recent = m.most_recent_log || {};
    const card = el("article", { class: "ev-card" });
    card.appendChild(el("div", { class: "ev-card-head" }, [
      el("span", {
        class: "ev-sig",
        onclick: () => {
          $("#hf-q").value = m.event_signature_id || "";
          setActiveTab("history");
          runHistoryQuery(0);
        }
      }, [m.event_signature_id || "(no sig)"]),
      el("span", { class: "ev-count" }, [`${m.occurrence_count || 0}×`]),
    ]));

    const primary = (State.evidenceMode === "recent") ? recent : top;
    card.appendChild(el("div", { class: "ev-title" }, [primary.title || primary.event_name || "(untitled)"]));
    if (primary.body) card.appendChild(el("div", { class: "ev-body" }, [primary.body]));

    const meta = el("div", { class: "ev-meta" });
    if (primary.occurred_at) meta.appendChild(el("span", {}, [fmtDate(primary.occurred_at)]));
    if (m.first_seen_at) meta.appendChild(el("span", {}, [`first ${fmtDate(m.first_seen_at)}`]));
    if (m.last_seen_at) meta.appendChild(el("span", {}, [`last ${fmtDate(m.last_seen_at)}`]));
    if (primary.component_name_raw) meta.appendChild(el("span", {}, [primary.component_name_raw]));
    if (m.linked_failure_mode_id) meta.appendChild(el("span", {}, [`FM: ${m.linked_failure_mode_id}`]));
    if (primary.work_order_id) {
      meta.appendChild(el("a", {
        onclick: () => setActiveTab("manuals-wo", { sub: "wo" })
      }, [`WO ${primary.work_order_id}`]));
    }
    if (primary.action_taken) meta.appendChild(el("span", {}, [`action: ${primary.action_taken}`]));
    if (primary.outcome) meta.appendChild(el("span", {}, [`outcome: ${primary.outcome}`]));
    card.appendChild(meta);

    if (State.evidenceMode === "all" && m.all_log_ids && m.all_log_ids.length > 1) {
      const hist = el("div", { class: "ev-history" });
      hist.appendChild(el("div", { class: "rail-label" }, [`Occurrences (${m.all_log_ids.length})`]));
      hist.appendChild(el("div", { class: "ev-body" }, [m.all_log_ids.join(", ")]));
      card.appendChild(hist);
    }

    if (m.rerank_rationale) {
      card.appendChild(el("div", { class: "ev-body", style: "margin-top:6px;font-style:italic;color:var(--text-faint)" },
        [m.rerank_rationale]));
    }

    list.appendChild(card);
  });
}

// ============== History (browse + semantic) ==============
async function runHistoryQuery(offset = 0) {
  if (!State.instanceId) return;
  const tbody = $("#history-tbody");
  const mode = $$("#history-mode .seg-btn").find(b => b.classList.contains("active"))?.dataset.mode || "browse";
  tbody.innerHTML = '<tr><td colspan="7" class="td-empty">Loading…</td></tr>';
  try {
    if (mode === "browse") {
      const params = new URLSearchParams();
      const q = $("#hf-q").value.trim();        if (q) params.set("q", q);
      const sev = $("#hf-severity").value;       if (sev) params.set("severity_min", sev);
      const st = $("#hf-status").value;          if (st) params.set("status", st);
      const ec = $("#hf-event-category").value;  if (ec) params.set("event_category", ec);
      const mt = $("#hf-maintenance-type").value;if (mt) params.set("maintenance_type", mt);
      const df = $("#hf-from").value;            if (df) params.set("date_from", df);
      const dt = $("#hf-to").value;              if (dt) params.set("date_to", dt);
      params.set("limit", "50");
      params.set("offset", String(offset || 0));
      const data = await apiGet(`/instances/${State.instanceId}/logs?` + params.toString());
      renderHistoryRows(data.items || []);
      renderHistoryPager(data.total, data.offset, data.limit);
    } else {
      const q = $("#hf-q").value.trim();
      if (!q) { tbody.innerHTML = '<tr><td colspan="7" class="td-empty">Type a query for semantic search.</td></tr>'; return; }
      const body = {
        query: q,
        date_from: $("#hf-from").value || undefined,
        date_to: $("#hf-to").value || undefined,
        event_category: $("#hf-event-category").value || undefined,
        maintenance_type: $("#hf-maintenance-type").value || undefined,
        status: $("#hf-status").value || undefined,
        severity_min: $("#hf-severity").value ? parseInt($("#hf-severity").value, 10) : undefined,
        limit: 20,
        use_llm_rerank: true,
      };
      const data = await apiPost(`/instances/${State.instanceId}/log-search`, body);
      // Flatten matches to their top_match_log
      const rows = (data.matches || []).map(m => m.top_match_log);
      renderHistoryRows(rows);
      $("#history-pager").innerHTML = `<span>${data.match_count} signature matches (top occurrence shown)</span>`;
    }
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="7" class="td-empty">Error: ${escapeHtml(e.message)}</td></tr>`;
  }
}

function renderHistoryRows(rows) {
  const tbody = $("#history-tbody");
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="td-empty">No results.</td></tr>';
    return;
  }
  tbody.innerHTML = "";
  rows.forEach(r => {
    const tr = el("tr");
    const sevN = r.severity_number || 0;
    tr.appendChild(el("td", {}, [fmtDate(r.occurred_at)]));
    tr.appendChild(el("td", {}, [el("span", { class: `sev-pill sev-${sevN}` }, [String(sevN)])]));
    tr.appendChild(el("td", {}, [r.title || r.event_name || ""]));
    tr.appendChild(el("td", {}, [r.component_name_raw || ""]));
    tr.appendChild(el("td", {}, [r.status || ""]));
    tr.appendChild(el("td", {}, [r.work_order_id || ""]));
    tr.appendChild(el("td", {}, [r.outcome || ""]));
    tr.addEventListener("click", () => showLogDetail(r.log_id));
    tr.style.cursor = "pointer";
    tbody.appendChild(tr);
  });
}

function renderHistoryPager(total, offset, limit) {
  const wrap = $("#history-pager");
  wrap.innerHTML = "";
  const start = Math.min(total, offset + 1);
  const end = Math.min(total, offset + limit);
  wrap.appendChild(el("span", {}, [`${start}–${end} of ${total}`]));
  if (offset > 0)
    wrap.appendChild(el("button", { class: "btn btn-sm", onclick: () => runHistoryQuery(Math.max(0, offset - limit)) }, ["← Prev"]));
  if (end < total)
    wrap.appendChild(el("button", { class: "btn btn-sm", onclick: () => runHistoryQuery(offset + limit) }, ["Next →"]));
}

async function showLogDetail(logId) {
  if (!logId) return;
  try {
    const r = await apiGet(`/instances/${State.instanceId}/logs/${logId}`);
    const msg = [
      r.title || r.event_name,
      r.occurred_at,
      r.body,
      r.action_taken ? "Action: " + r.action_taken : "",
      r.outcome ? "Outcome: " + r.outcome : "",
      r.work_order_id ? "WO: " + r.work_order_id : "",
    ].filter(Boolean).join("\n\n");
    alert(msg);
  } catch (e) { toast(e.message, "error"); }
}

// ============== Trends ==============
function primeTrendsFromResponse(resp) {
  const tel = resp?.telemetry;
  const sel = $("#trends-signal-select");
  sel.innerHTML = "";
  if (!tel || !tel.signals) {
    sel.appendChild(el("option", { value: "" }, ["(no signals)"]));
    State.selectedSignal = null;
    return;
  }
  Object.keys(tel.signals).forEach(name => sel.appendChild(el("option", { value: name }, [name])));
  if (!State.selectedSignal || !tel.signals[State.selectedSignal]) {
    State.selectedSignal = Object.keys(tel.signals)[0] || null;
  }
  sel.value = State.selectedSignal || "";
}

function renderTrends() {
  const tel = State.lastResponse?.telemetry;
  const canvas = $("#chart-main");
  const banner = $("#trends-banner");
  const sigName = State.selectedSignal;

  // The backend stores tel.signals[name] as a flat array of {t, v} points.
  const sigArray = sigName && tel?.signals?.[sigName];
  const hasData = Array.isArray(sigArray) && sigArray.length > 0;

  $("#chart-main-title").textContent = sigName || "No signal selected";
  if (banner) banner.classList.toggle("hidden", !!hasData);
  if (!hasData) {
    if (State.chart) { State.chart.destroy(); State.chart = null; }
    $("#chart-main-sub").textContent = "";
    $("#chart-kpis").innerHTML = "";
    $("#chart-markers").innerHTML = '<div class="panel-empty"><i class="fa fa-chart-area panel-empty-icon"></i><div>Run a diagnosis to see linked measurements.</div></div>';
    return;
  }

  // Parse points
  const points = sigArray
    .map(p => ({ x: new Date(p.t || p.timestamp), y: Number(p.v ?? p.value) }))
    .filter(p => !isNaN(p.x.getTime()) && !isNaN(p.y));

  // Window by selected range (relative to the latest data point, not "now",
  // because the seed CSVs are not real-time).
  let filtered = points;
  if (points.length) {
    const latest = points[points.length - 1].x.getTime();
    const cutoff = latest - State.trendsRangeHours * 3600 * 1000;
    filtered = points.filter(p => p.x.getTime() >= cutoff);
    if (!filtered.length) filtered = points; // fallback: show everything
  }

  // KPI tiles from server-side stats
  const stats = tel.stats?.[sigName] || {};
  const kpiWrap = $("#chart-kpis");
  kpiWrap.innerHTML = "";
  const mkKpi = (label, value, extra = "") => el("div", { class: "kpi" + extra }, [
    el("span", { class: "kpi-label" }, [label]),
    el("span", { class: "kpi-value" }, [value]),
  ]);
  if (stats.last != null)  kpiWrap.appendChild(mkKpi("Last",  String(stats.last)));
  if (stats.mean != null)  kpiWrap.appendChild(mkKpi("Mean",  String(stats.mean)));
  if (stats.min != null)   kpiWrap.appendChild(mkKpi("Min",   String(stats.min)));
  if (stats.max != null)   kpiWrap.appendChild(mkKpi("Max",   String(stats.max)));
  if (stats.std != null)   kpiWrap.appendChild(mkKpi("σ",     String(stats.std)));
  if (stats.trend) {
    const arrow = stats.trend === "rising" ? "↑" : stats.trend === "falling" ? "↓" : "→";
    kpiWrap.appendChild(mkKpi("Trend", `${arrow} ${stats.trend}`, ` trend-${stats.trend}`));
  }

  // Sub-label: range covered + point count
  if (filtered.length) {
    const t0 = filtered[0].x.toISOString().slice(0, 16).replace("T", " ");
    const t1 = filtered[filtered.length - 1].x.toISOString().slice(0, 16).replace("T", " ");
    $("#chart-main-sub").textContent = `${filtered.length} samples · ${t0} → ${t1} UTC`;
  } else {
    $("#chart-main-sub").textContent = "";
  }

  if (State.chart) State.chart.destroy();
  State.chart = new Chart(canvas, {
    type: "line",
    data: {
      datasets: [{
        label: sigName,
        data: filtered,
        borderColor: "#2563EB",
        backgroundColor: "rgba(37,99,235,.10)",
        borderWidth: 1.6,
        pointRadius: 0,
        pointHoverRadius: 4,
        pointHoverBackgroundColor: "#2563EB",
        tension: 0.25,
        fill: true,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 250 },
      interaction: { mode: "nearest", axis: "x", intersect: false },
      scales: {
        x: {
          type: "time",
          time: { tooltipFormat: "yyyy-MM-dd HH:mm:ss", displayFormats: { hour: "HH:mm", day: "MM-dd" } },
          title: { display: true, text: "Time", color: "#64748B", font: { size: 11, weight: "600" } },
          ticks: { color: "#64748B", maxRotation: 0, autoSkipPadding: 16 },
          grid: { color: "#E2E8F0" },
        },
        y: {
          title: { display: true, text: sigName, color: "#64748B", font: { size: 11, weight: "600" } },
          ticks: { color: "#64748B" },
          grid: { color: "#E2E8F0" },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#0F172A", titleColor: "#fff", bodyColor: "#E2E8F0",
          borderColor: "#334155", borderWidth: 1, padding: 10,
          callbacks: { label: ctx => ` ${sigName}: ${ctx.parsed.y}` },
        },
        zoom: {
          pan:  { enabled: true, mode: "x", modifierKey: null },
          zoom: {
            wheel: { enabled: true, speed: 0.08 },
            pinch: { enabled: true },
            drag:  { enabled: false },
            mode: "x",
          },
          limits: { x: { minRange: 60 * 1000 } },
        },
      },
      onDoubleClick: () => State.chart && State.chart.resetZoom(),
    },
  });
  // Native dblclick fallback (Chart.js doesn't proxy onDoubleClick by default)
  canvas.ondblclick = () => State.chart && State.chart.resetZoom();

  // Markers from log_evidence
  const wrap = $("#chart-markers");
  wrap.innerHTML = "";
  const ev = State.lastResponse?.log_evidence || [];
  if (!ev.length) wrap.innerHTML = '<div class="panel-empty">No event markers.</div>';
  ev.forEach(m => {
    const t = m.most_recent_log?.occurred_at || m.last_seen_at;
    const line = el("div", { class: "mk" }, [
      `${fmtDate(t)} — ${m.top_match_log?.title || m.event_signature_id} (${m.occurrence_count}×)`
    ]);
    wrap.appendChild(line);
  });
}

// ============== Manuals & WO ==============
function renderManualsAndWO() {
  const evidence = State.lastResponse?.log_evidence || [];
  const issue = State.lastResponse?.current_issue;

  // Manuals: derive from action_options sources (keeps reference manuals above intact)
  const manuals = $("#manuals-cited") || $("#mwo-manuals");
  manuals.innerHTML = "";
  const sources = (issue?.action_options || []).filter(o => o.source_title || o.source_reference);
  if (!sources.length) {
    manuals.appendChild(el("div", { class: "manuals-section-label" }, ["Cited in current diagnosis"]));
    manuals.appendChild(el("div", { class: "panel-empty" }, [
      el("i", { class: "fa fa-quote-left panel-empty-icon" }),
      el("div", {}, ["No manual excerpts cited yet."])
    ]));
  } else {
    manuals.appendChild(el("div", { class: "manuals-section-label" }, ["Cited in current diagnosis"]));
    sources.forEach(o => {
      const c = el("div", { class: "manual-card" });
      c.appendChild(el("div", { class: "mc-title" }, [o.source_title || o.source_reference || "Source"]));
      if (o.source_reference) {
        const src = el("div", { class: "mc-src" });
        src.appendChild(el("a", { onclick: () => openManual(o.source_reference, o.source_title) }, [o.source_reference]));
        c.appendChild(src);
      }
      if (o.instruction_text) c.appendChild(el("div", { class: "mc-excerpt" }, [o.instruction_text]));
      manuals.appendChild(c);
    });
  }

  // Work orders: derive from log_evidence
  const wo = $("#mwo-wo");
  wo.innerHTML = "";
  const woMap = new Map();
  evidence.forEach(m => {
    const candidates = [m.top_match_log, m.most_recent_log];
    candidates.forEach(c => {
      if (!c || !c.work_order_id) return;
      if (!woMap.has(c.work_order_id)) woMap.set(c.work_order_id, c);
    });
  });
  if (!woMap.size) {
    wo.appendChild(el("div", { class: "panel-empty" }, ["No work orders referenced in the latest log evidence."]));
    return;
  }
  woMap.forEach((r, woId) => {
    const c = el("div", { class: "wo-card" });
    c.appendChild(el("div", { class: "wo-head" }, [
      el("span", { class: "wo-id" }, [woId]),
      el("span", { class: "wo-meta" }, [
        [r.maintenance_type, r.status, r.outcome].filter(Boolean).join(" · ") || "—"
      ]),
    ]));
    c.appendChild(el("div", { class: "wo-title" }, [r.title || r.event_name || "(untitled)"]));
    const meta = [];
    if (r.actual_duration_min) meta.push(`${r.actual_duration_min} min`);
    if (r.action_taken) meta.push(r.action_taken);
    if (meta.length) c.appendChild(el("div", { class: "wo-body" }, [meta.join(" — ")]));
    wo.appendChild(c);
  });
}

// Discover which PDF manuals exist for the current instance, via HEAD probes
// against the static /manuals/ mount. We can't list the directory, so we
// generate likely filenames from product metadata.
async function discoverInstanceManuals() {
  const wrap = $("#manuals-reference");
  if (!wrap) return;
  wrap.innerHTML = "";
  const p = State.productMeta || {};
  const candidates = [];
  const push = (name) => {
    if (!name) return;
    const trimmed = name.trim();
    if (!trimmed) return;
    if (!candidates.includes(trimmed)) candidates.push(trimmed);
  };
  push(p.product_short_name);
  push(p.product_name);
  if (p.product_name) push(p.product_name + " manual");
  if (p.product_name) push(p.product_name + " series manual");
  // Known products in the seed
  if (/bambu/i.test(p.product_name || "")) push("Bambu Lab P1 series manual");
  if (/irc/i.test(p.product_short_name || p.product_name || "")) push("IRC5");

  const found = [];
  await Promise.all(candidates.map(async name => {
    const url = `/manuals/${encodeURIComponent(name)}.pdf`;
    try {
      const res = await fetch(url, { method: "HEAD" });
      if (res.ok) found.push({ name, url });
    } catch {}
  }));

  if (!found.length) return;
  wrap.appendChild(el("div", { class: "manuals-section-label" }, ["Reference manuals"]));
  found.forEach(m => {
    const card = el("div", { class: "ref-manual" }, [
      el("div", { class: "ref-manual-info" }, [
        el("div", { class: "ref-manual-icon" }, [el("i", { class: "fa fa-file-pdf" })]),
        el("div", { class: "ref-manual-meta" }, [
          el("div", { class: "ref-manual-name" }, [m.name + ".pdf"]),
          el("div", { class: "ref-manual-sub" }, [p.product_type || "Reference document"]),
        ]),
      ]),
      el("div", { class: "ref-manual-actions" }, [
        el("button", {
          class: "btn btn-sm btn-primary",
          onclick: () => openManual(m.url, m.name + ".pdf")
        }, [el("i", { class: "fa fa-book-open" }), " Open"]),
        el("a", {
          class: "btn btn-sm btn-ghost",
          href: m.url, target: "_blank", rel: "noopener",
          title: "Open in new tab"
        }, [el("i", { class: "fa fa-external-link-alt" })]),
      ]),
    ]);
    wrap.appendChild(card);
  });
}

function openManual(ref, title) {
  if (!ref) return;
  let url;
  if (ref.startsWith("http")) url = ref;
  else if (ref.startsWith("/manuals/")) url = ref;
  else url = `/manuals/${ref}`;
  $("#pdf-overlay").classList.remove("hidden");
  $("#pdf-title").textContent = title || decodeURIComponent(url.split("/").pop().split("#")[0]);
  $("#pdf-iframe").src = url;
}

// ============== Thread + recent actions ==============
function appendThread(role, text, extra) {
  const wrap = $("#thread");
  const node = el("div", { class: "msg " + role });
  if (role === "assistant") {
    node.innerHTML = renderMarkdown(text);
    if (extra) {
      if (extra.issue_number && extra.total_issues) {
        node.appendChild(el("div", { class: "msg-issue-counter" },
          [`Possible cause ${extra.issue_number} of ${extra.total_issues}`]));
      }
      const scores = extra.highlight?.scores || extra.scores;
      if (scores && Object.keys(scores).length) {
        const top = Math.max(...Object.values(scores));
        const pct = Math.round(top * 100);
        const color = top >= 0.75 ? "var(--green-500)" : top >= 0.55 ? "var(--yellow-400)" : "var(--red-400)";
        const wrapBar = el("div", { class: "msg-confidence" }, [
          `Match confidence: ${pct}%`,
          el("div", { class: "msg-confidence-bar" }, [
            el("div", { class: "msg-confidence-fill", style: `width:${pct}%;background:${color}` })
          ])
        ]);
        node.appendChild(wrapBar);
      }
    }
  } else {
    node.textContent = text;
  }
  wrap.appendChild(node);
  wrap.scrollTop = wrap.scrollHeight;
  return node;
}

function appendTypingIndicator() {
  const wrap = $("#thread");
  const node = el("div", { class: "msg typing" }, [
    el("span", { class: "dots" }, [
      el("span", { class: "dot" }), el("span", { class: "dot" }), el("span", { class: "dot" }),
    ]),
    el("span", { class: "label" }, ["Thinking…"]),
  ]);
  wrap.appendChild(node);
  wrap.scrollTop = wrap.scrollHeight;
  return node;
}

function renderRecentActions() {
  const ul = $("#recent-actions");
  ul.innerHTML = "";
  State.recentActions.forEach(a => {
    ul.appendChild(el("li", {}, [
      el("span", { class: "ra-time" }, [a.time]),
      a.label,
    ]));
  });
}

// ============== Reload ==============
async function onReloadCaches() {
  if (!State.instanceId) return;
  try {
    await apiPost(`/instances/${State.instanceId}/reload`, {});
    toast("Caches reloaded");
    await loadInstanceContext(State.instanceId);
  } catch (e) { toast(e.message, "error"); }
}

// ============== Advanced (KG) ==============
async function loadKGGraph() {
  if (State.kgInited) return;
  if (!State.instanceId) return;
  try {
    const data = await apiGet(`/instances/${State.instanceId}/graph-data`);
    const nodes = new vis.DataSet((data.nodes || []).map(n => ({
      id: n.id,
      label: n.label,
      group: n.group,
      title: n.title,
      color: data.color_map?.[n.group] || "#5a6678",
      shape: "dot",
      size: 12,
    })));
    const edges = new vis.DataSet((data.edges || []).map(e => ({
      from: e.from, to: e.to, label: e.label,
      arrows: "to", color: { color: "#2a313d", highlight: "#6ea8ff" },
      font: { color: "#97a0b0", size: 9 },
    })));
    const container = $("#kg-network");
    new vis.Network(container, { nodes, edges }, {
      nodes: { font: { color: "#e6e9ef", size: 11 } },
      physics: { stabilization: { iterations: 80 } },
      interaction: { hover: true },
    });
    const lg = $("#kg-legend");
    lg.innerHTML = "";
    Object.entries(data.color_map || {}).forEach(([k, v]) => {
      lg.appendChild(el("span", { class: "lg-item" }, [
        el("span", { class: "lg-dot", style: `background:${v}` }), k,
      ]));
    });
    State.kgInited = true;
  } catch (e) {
    toast("KG load failed: " + e.message, "error");
  }
}
