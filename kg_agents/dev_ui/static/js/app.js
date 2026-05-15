/* ============================================================
   Kg-agents — Mission Control
   Backend: kg_agents FastAPI under /v1/kg-agents
   ============================================================ */

const API = "/v1/kg-agents";

/* ---------- State ---------- */
const State = {
  instanceId: null,
  productMeta: null,
  status: null,
  logsSummary: null,
  instanceMeta: null,
  sessions: [],        // from GET /chat-sessions (for left rail)
  cases: [],           // [{id, kind, question, ts, response, sessionId, mode, outcome}]
  activeCaseId: null,
  activeIntent: "diagnose",
  savedFilterOn: false,
  kgInited: false,
  kgNetwork: null,
};

/* ---------- Utilities ---------- */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function stripMarkdown(s) {
  if (!s) return "";
  return String(s)
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1");
}

function renderMarkdownInline(src) {
  if (!src) return "";
  let s = escapeHtml(src);
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
  return s;
}

function renderMarkdown(src) {
  if (!src) return "";
  let s = escapeHtml(src);
  s = s.replace(/(?:^|\n)\s*(?:---|\*\*\*|___)\s*(?=\n|$)/g, "\n<hr/>");
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
  s = s.replace(/^###\s+(.*)$/gm, "<h3>$1</h3>");
  // [MANUAL:title:page] → clickable chip that opens in inspector
  s = s.replace(/\[MANUAL:([^\]:]+):(\d+)\]/g, (m, title, page) =>
    `<button class="manual-link" data-manual-title="${escapeHtml(title)}" data-manual-page="${page}">` +
    `<i class="fa fa-book-open"></i> §${page} <span>${escapeHtml(title)}</span></button>`
  );
  s = s.replace(/(?:^|\n)((?:[-*]\s+.+\n?)+)/g, (m, blk) => {
    const items = blk.trim().split(/\n/).map(l => l.replace(/^[-*]\s+/, ""));
    return "\n<ul>" + items.map(i => `<li>${i}</li>`).join("") + "</ul>";
  });
  s = s.replace(/(?:^|\n)((?:\d+\.\s+.+\n?)+)/g, (m, blk) => {
    const items = blk.trim().split(/\n/).map(l => l.replace(/^\d+\.\s+/, ""));
    return "\n<ol>" + items.map(i => `<li>${i}</li>`).join("") + "</ol>";
  });
  s = s.split(/\n{2,}/).map(p => {
    if (/^<(h3|ul|ol|p|hr)/.test(p.trim())) return p;
    return "<p>" + p.replace(/\n/g, "<br/>") + "</p>";
  }).join("\n");
  return s;
}

function manualUrl(title, page) {
  return "/manuals/" + encodeURIComponent(title + ".pdf") + (page ? "#page=" + page : "");
}

function pageFromRef(ref) {
  if (!ref) return null;
  const s = String(ref).trim();
  if (!s || s.startsWith("http")) return null;
  if (/^\d+$/.test(s)) return s;
  const m = s.match(/(\d+)/);
  return m ? m[1] : null;
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toISOString().slice(0, 16).replace("T", " ");
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
  const t = String(s).replace(/\s+/g, " ").trim();
  return t.length > n ? t.slice(0, n - 1) + "…" : t;
}

function toast(msg, ms = 2400) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), ms);
}

/* ---------- Mini sparkline SVG ---------- */
function sparkSVG(values, opts = {}) {
  const w = opts.width || 56, h = opts.height || 16;
  const stroke = opts.stroke || "#2563EB";
  const fill = opts.fill || "rgba(37,99,235,.12)";
  if (!values || !values.length) return "";
  const min = Math.min(...values), max = Math.max(...values);
  const range = max - min || 1;
  const stepX = w / (values.length - 1 || 1);
  const points = values.map((v, i) => {
    const x = i * stepX;
    const y = h - ((v - min) / range) * (h - 2) - 1;
    return [x, y];
  });
  const path = points.map((p, i) => (i === 0 ? "M" : "L") + p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ");
  const area = path + ` L ${w},${h} L 0,${h} Z`;
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
    <path d="${area}" fill="${fill}" stroke="none"/>
    <path d="${path}" fill="none" stroke="${stroke}" stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round"/>
  </svg>`;
}

/* ---------- API wrappers ---------- */
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
const apiGet  = p       => api(p);
const apiPost = (p, b)  => api(p, { method: "POST", body: JSON.stringify(b || {}) });
const apiDel  = p       => api(p, { method: "DELETE" });

/* ---------- Bookmark store (localStorage) ---------- */
const BookmarkStore = {
  key: "mc.bookmarks",
  get() { try { return new Set(JSON.parse(localStorage.getItem(this.key) || "[]")); } catch { return new Set(); } },
  save(s) { try { localStorage.setItem(this.key, JSON.stringify([...s])); } catch {} },
  has(id) { return this.get().has(id); },
  toggle(id) {
    const s = this.get();
    s.has(id) ? s.delete(id) : s.add(id);
    this.save(s);
    return s.has(id);
  },
};

/* ---------- Session persistence ---------- */
function sessionKey() { return State.instanceId ? `kgua_last_${State.instanceId}` : null; }
function persistSessionId(id) { const k = sessionKey(); if (k && id) try { localStorage.setItem(k, id); } catch {} }
function restoreSessionId() { const k = sessionKey(); try { return k ? localStorage.getItem(k) : null; } catch { return null; } }
function clearSessionId() { const k = sessionKey(); if (k) try { localStorage.removeItem(k); } catch {} }

/* ============================================================
   BOOT
   ============================================================ */
document.addEventListener("DOMContentLoaded", boot);

async function boot() {
  setupComposer();
  setupResizers();
  setupInspector();
  setupSidebar();

  // Close any open case menus on outside click
  document.addEventListener("click", e => {
    if (!e.target.closest(".case-menu") && !e.target.closest("[data-act='menu']")) {
      document.querySelectorAll(".case-menu").forEach(m => { m.hidden = true; });
    }
  });

  // new-case btn — clears all spine cards and resets to empty state
  $("#new-case-btn").addEventListener("click", () => {
    clearSessionId();
    State.cases = [];
    State.activeCaseId = null;
    // Clear continue-session tag if any
    const ta = $("#composer");
    delete ta.dataset.continueSessionId;
    renderSpine();
    renderSessions();
    ta.focus();
    toast("New case — type your question below");
  });

  // KG button in rail footer
  $("#open-kg-btn").addEventListener("click", () => openInspector("kg"));

  // Reload caches
  $("#reload-btn").addEventListener("click", async () => {
    if (!State.instanceId) return;
    try {
      await apiPost(`/instances/${State.instanceId}/reload`, {});
      toast("Caches reloaded");
      await loadInstanceContext(State.instanceId);
    } catch (e) { toast("Reload failed: " + e.message); }
  });

  await loadInstances();
  if (State.instanceId) await loadInstanceContext(State.instanceId);
}

function setupSidebar() {
  const toggle = $("#sidebar-toggle");
  if (toggle) toggle.addEventListener("click", () => document.body.classList.toggle("sidebar-collapsed"));
}

/* ============================================================
   INSTANCES
   ============================================================ */
async function loadInstances() {
  try {
    const data = await apiGet("/instances");
    const sel = $("#instance-select");
    sel.innerHTML = "";
    (data.instances || []).forEach(inst => {
      const opt = document.createElement("option");
      opt.value = inst.id;
      opt.textContent = inst.name + (inst.agent_name ? " — " + inst.agent_name : "");
      sel.appendChild(opt);
    });
    if (data.instances && data.instances.length) {
      const preferred = data.instances.find(i => i.id === "irc5-default-instance")
        || data.instances.find(i => /irc5/i.test(i.name || ""))
        || data.instances[0];
      State.instanceId = preferred.id;
      sel.value = State.instanceId;
    }
    sel.addEventListener("change", e => loadInstanceContext(e.target.value));
  } catch (e) {
    toast("Failed to load instances: " + e.message);
  }
}

async function loadInstanceContext(instanceId) {
  State.instanceId = instanceId;
  State.cases = [];
  State.activeCaseId = null;
  State.kgInited = false;
  if (State.kgNetwork) { State.kgNetwork.destroy(); State.kgNetwork = null; }

  // Reset spine
  renderSpine();

  try {
    const [status, summary, sessions, instMeta, prod] = await Promise.all([
      apiGet(`/instances/${instanceId}/status`).catch(() => null),
      apiGet(`/instances/${instanceId}/logs/summary`).catch(() => null),
      apiGet(`/instances/${instanceId}/chat-sessions?limit=20`).catch(() => null),
      apiGet(`/instances/${instanceId}`).catch(() => null),
      apiGet(`/instances/${instanceId}/product-info`).catch(() => null),
    ]);

    State.status       = status;
    State.logsSummary  = summary;
    State.instanceMeta = instMeta;
    State.productMeta  = prod;
    State.sessions     = (sessions?.sessions || [])
      .filter(s => (s.first_user_message || "").trim() && (s.message_count || 0) >= 1)
      .sort((a, b) => (b.last_message_at || "").localeCompare(a.last_message_at || ""));

    updateBreadcrumb();
    renderAssetPulse();
    renderSessions();

    // Try to resume persisted session silently
    const saved = restoreSessionId();
    if (saved) {
      try { await loadSession(saved, true); } catch { clearSessionId(); }
    }
  } catch (e) {
    toast("Failed to load context: " + e.message);
  }
}

function updateBreadcrumb() {
  const sel = $("#instance-select");
  const opt = sel?.querySelector(`option[value="${State.instanceId}"]`);
  const label = opt ? (opt.textContent.split(" — ")[0] || opt.textContent) : "—";
  const bcInst = $("#bc-instance");
  if (bcInst) bcInst.textContent = label;
  const bcPlant = $("#bc-plant");
  if (bcPlant && State.productMeta?.plant_name) bcPlant.textContent = State.productMeta.plant_name;
}

/* ============================================================
   ASSET PULSE
   ============================================================ */
function renderAssetPulse() {
  const s = State.logsSummary;
  const pill = $("#ap-status-pill");
  if (pill) {
    const open = s?.open_events || 0;
    const sev = s?.severity_distribution || {};
    const hasCrit = (sev["4"] || sev[4] || 0) > 0;
    const hasWarn = (sev["3"] || sev[3] || 0) > 0;
    if (hasCrit) {
      pill.className = "status-pill crit";
      pill.innerHTML = '<i class="fa fa-circle"></i> Critical fault';
    } else if (hasWarn || open > 0) {
      pill.className = "status-pill warn";
      pill.innerHTML = `<i class="fa fa-circle"></i> ${open} active fault${open === 1 ? "" : "s"}`;
    } else {
      pill.className = "status-pill ok";
      pill.innerHTML = '<i class="fa fa-circle"></i> Healthy';
    }
  }

  const m = State.instanceMeta || {};
  const st = State.status || {};
  const kg  = $("#ap-kg-nodes");
  const wo  = $("#ap-open-wo");
  const evs = $("#ap-events-7d");
  if (kg)  kg.textContent  = m.node_count ?? st.total_nodes ?? "—";
  if (wo)  wo.textContent  = s?.open_events ?? "—";
  if (evs) evs.textContent = s?.row_count ?? "—";
}

/* ============================================================
   SESSION RAIL
   ============================================================ */
function renderSessions() {
  const list = $("#session-list");
  const railLabel = $(".rail-block .rail-label");

  // Inject / update the "Saved" filter chip
  if (railLabel && !railLabel.querySelector(".saved-filter")) {
    const f = document.createElement("button");
    f.className = "saved-filter";
    f.innerHTML = '<i class="fa-regular fa-bookmark"></i><span>Saved</span>';
    f.addEventListener("click", () => {
      State.savedFilterOn = !State.savedFilterOn;
      f.classList.toggle("active", State.savedFilterOn);
      f.querySelector("i").className = State.savedFilterOn ? "fa-solid fa-bookmark" : "fa-regular fa-bookmark";
      renderSessions();
    });
    railLabel.appendChild(f);
  }

  list.innerHTML = "";
  const bookmarks = BookmarkStore.get();
  let ordered = State.sessions.slice(); // already newest-first
  if (State.savedFilterOn) {
    // match by sessionId stored in local cases, or by session_id on the server session
    ordered = ordered.filter(s => bookmarks.has(s.session_id));
  }

  if (!ordered.length) {
    const empty = document.createElement("div");
    empty.className = "session-empty";
    empty.innerHTML = State.savedFilterOn
      ? '<i class="fa-regular fa-bookmark"></i><div>No saved cases yet.<br/><small>Click ★ on any card to pin it.</small></div>'
      : '<div style="color:var(--slate-400);font-size:12px;padding:4px 0;">No cases yet. Send a message below.</div>';
    list.appendChild(empty);
    return;
  }

  ordered.forEach(s => {
    const kind = s.behavior_mode === "search_past_events" ? "past" : "diagnose";
    const inSpine = State.cases.some(c => c.sessionId === s.session_id);
    const kindMeta = kind === "past"
      ? { icon: "fa-clock-rotate-left", label: "Past events" }
      : { icon: "fa-stethoscope", label: "Diagnose" };

    const div = document.createElement("div");
    div.className = "session-card" + (inSpine ? " active" : "");
    div.dataset.kind = kind;
    div.dataset.sid = s.session_id;

    div.innerHTML = `
      <div class="sc-head">
        <span class="sc-kind"><i class="fa ${kindMeta.icon}"></i> ${kindMeta.label}</span>
        <span class="sc-time">${relativeTime(s.last_message_at || s.created_at)}</span>
      </div>
      <div class="sc-title">${escapeHtml(truncate(s.first_user_message || "(empty)", 72))}</div>
    `;

    div.addEventListener("click", () => {
      loadSession(s.session_id);
    });
    list.appendChild(div);
  });
}

/* ============================================================
   LOAD SESSION → add case card to spine
   ============================================================ */
async function loadSession(sessionId, silent = false) {
  // If already in spine, just scroll to it
  const existing = State.cases.find(c => c.sessionId === sessionId);
  if (existing) {
    scrollToCase(existing.id);
    return;
  }

  try {
    const data = await apiGet(`/instances/${State.instanceId}/chat-sessions/${sessionId}`);
    const messages = data.messages || [];
    const lastUser = [...messages].reverse().find(m => m.role === "user");
    const lastAssistant = [...messages].reverse().find(m => m.role === "assistant" && m.payload);

    if (!lastAssistant?.payload) {
      if (!silent) toast("Session has no completed response yet");
      return;
    }

    const resp = lastAssistant.payload;
    const question = lastUser?.content || data.first_user_message || "";
    const kind = resp.behavior_mode === "search_past_events" ? "past" : "diagnose";
    const ts = new Date(data.last_message_at || data.created_at || Date.now());
    const timeStr = ts.toTimeString().slice(0, 5);

    const caseObj = {
      id: "case-" + sessionId,
      kind,
      question,
      ts: timeStr,
      sessionId,
      response: resp,
      mode: "fast",
      outcome: null,
    };

    State.cases.push(caseObj);
    persistSessionId(sessionId);
    State.activeCaseId = caseObj.id;

    renderSpine();
    renderSessions();
    if (!silent) scrollToCase(caseObj.id);
  } catch (e) {
    if (!silent) toast("Failed to load session: " + e.message);
  }
}

/* ============================================================
   SPINE RENDER
   ============================================================ */
function renderSpine() {
  const root = $("#spine-scroll");
  root.innerHTML = "";

  if (!State.cases.length) {
    const empty = document.createElement("div");
    empty.className = "spine-empty";
    empty.id = "spine-empty";
    empty.innerHTML = `
      <div class="spine-empty-icon"><i class="fa fa-stethoscope"></i></div>
      <div class="spine-empty-title">Mission Control</div>
      <div class="spine-empty-text">Select an asset and describe the issue below, or search for past events. Each request becomes an independent case card.</div>
    `;
    root.appendChild(empty);
    return;
  }

  // Group by day
  let lastDay = null;
  State.cases.forEach(c => {
    const sessionMeta = State.sessions.find(s => s.session_id === c.sessionId);
    const dayLabel = getDayLabel(sessionMeta?.last_message_at || sessionMeta?.created_at);
    if (dayLabel !== lastDay) {
      const sep = document.createElement("div");
      sep.className = "day-sep";
      sep.textContent = dayLabel;
      root.appendChild(sep);
      lastDay = dayLabel;
    }
    root.appendChild(buildCase(c));
  });
}

function getDayLabel(iso) {
  if (!iso) return "Today";
  const d = new Date(iso);
  const now = new Date();
  const diff = (now.setHours(0,0,0,0) - d.setHours(0,0,0,0)) / 86400000;
  if (diff <= 0) return "Today";
  if (diff <= 1) return "Yesterday";
  return new Date(iso).toISOString().slice(0, 10);
}

function scrollToCase(caseId) {
  const el = document.getElementById(caseId);
  const root = $("#spine-scroll");
  if (el && root) {
    setTimeout(() => {
      root.scrollTo({ top: Math.max(0, el.offsetTop - 24), behavior: "smooth" });
      el.animate(
        [{ boxShadow: "0 0 0 4px rgba(37,99,235,.3)" }, { boxShadow: "0 6px 18px rgba(15,23,42,.10)" }],
        { duration: 900, easing: "ease-out" }
      );
    }, 60);
  }
}

/* ============================================================
   BUILD CASE CARD
   ============================================================ */
function buildCase(c) {
  const kindMeta = c.kind === "past"
    ? { icon: "fa-clock-rotate-left", label: "Past events" }
    : { icon: "fa-stethoscope", label: "Diagnose" };

  const resp = c.response;
  const isActive = c.id === State.activeCaseId;
  const isBookmarked = BookmarkStore.has(c.sessionId);

  // Confidence from response scores
  let confClass = "", confLabel = "";
  if (resp) {
    const scores = resp.highlight?.scores || resp.scores;
    if (scores && Object.keys(scores).length) {
      const top = Math.max(...Object.values(scores));
      const pct = Math.round(top * 100);
      confClass = top >= 0.70 ? "" : top >= 0.50 ? "med" : "low";
      confLabel = pct + "% match";
    }
  }

  // Sub-labels from intent / behavior_mode
  const subParts = [];
  if (resp?.intent) subParts.push(resp.intent.replace(/_/g, " "));
  if (resp?.current_issue?.component_name) subParts.push(resp.current_issue.component_name);

  const article = document.createElement("article");
  article.className = "case" + (isActive ? " active" : "") + (!resp ? " loading" : "");
  article.id = c.id;
  article.dataset.kind = c.kind;
  article.dataset.cid = c.id;

  article.innerHTML = `
    <header class="case-head">
      <div class="case-icon"><i class="fa ${kindMeta.icon}"></i></div>
      <div class="case-meta">
        <div class="case-kind-row">
          <span>${kindMeta.label}</span>
          ${confLabel ? `<span class="case-conf ${confClass}">${confLabel}</span>` : ""}
          ${c.kind === "diagnose" ? `
            <div class="mode-toggle" data-mode-toggle>
              <button class="mt-btn active" data-mode="fast"><i class="fa fa-bolt"></i> Fast</button>
              <button class="mt-btn" data-mode="non-fast"><i class="fa fa-route"></i> Guided</button>
            </div>` : ""}
          <span style="margin-left:auto;color:var(--slate-400);font-size:11px;font-weight:500;text-transform:none;letter-spacing:0">${c.ts}</span>
        </div>
        <div class="case-question">${escapeHtml(c.question)}</div>
        ${subParts.length ? `<div class="case-sub">${subParts.map((s, i) => i === 0 ? `<span>${escapeHtml(s)}</span>` : `<span class="dot">·</span><span>${escapeHtml(s)}</span>`).join("")}</div>` : ""}
      </div>
      <div class="case-actions">
        <button class="btn-icon case-bookmark ${isBookmarked ? "on" : ""}" title="Bookmark" data-act="bookmark">
          <i class="fa-${isBookmarked ? "solid" : "regular"} fa-bookmark"></i>
        </button>
        <div class="case-menu-wrap">
          <button class="btn-icon case-menu-trigger" title="More" data-act="menu">
            <i class="fa fa-ellipsis-vertical"></i>
          </button>
          <div class="case-menu" hidden>
            <button class="cm-item" data-menu="rerun"><i class="fa fa-rotate-right"></i> Re-run / ask again</button>
            <button class="cm-item" data-menu="copylink"><i class="fa fa-link"></i> Copy link</button>
            <button class="cm-item" data-menu="exportpdf"><i class="fa-regular fa-file-pdf"></i> Export PDF</button>
            <div class="cm-sep"></div>
            <button class="cm-item danger" data-menu="delete"><i class="fa fa-trash"></i> Delete case</button>
          </div>
        </div>
      </div>
    </header>
    <div class="case-body">
      ${resp ? (c.kind === "past" ? buildPastBody(c) : buildDiagnoseBody(c)) : ""}
    </div>
    ${resp && c.kind === "diagnose" ? buildDiagnoseFooter(c) : ""}
    ${resp && c.kind === "past" ? buildPastFooter(c) : ""}
  `;

  wireCase(article, c);
  return article;
}

/* ============================================================
   DIAGNOSE BODY
   ============================================================ */
function renderActionCard(a, isPrimary) {
  const pct = a.stats?.total_uses ? Math.round(a.stats.success_rate_pct || 0) : null;
  const page = pageFromRef(a.source_reference);
  const manualBtn = (a.source_title && page)
    ? `<button class="manual-link" data-manual-title="${escapeHtml(a.source_title)}" data-manual-page="${page}"><i class="fa fa-book-open"></i> §${page} <span>${escapeHtml(a.source_title)}</span></button>`
    : (a.source_title ? `<span class="action-source-text">${escapeHtml(a.source_title)}${a.source_reference ? " — " + escapeHtml(a.source_reference) : ""}</span>` : "");
  const stats = [
    pct !== null ? `<span class="stat ok"><b>${pct}%</b> success</span>` : "",
    a.stats?.total_uses ? `<span class="stat"><b>${a.stats.total_uses}</b> runs</span>` : `<span class="stat new">new</span>`,
    a.stats?.avg_duration_min ? `<span class="stat">≈ <b>${a.stats.avg_duration_min.toFixed(0)} min</b></span>` : "",
  ].filter(Boolean).join("");
  return `
    <div class="action-card ${isPrimary ? "primary" : "alt"}" data-action-id="${escapeHtml(a.action_id || "")}">
      <div class="action-card-name">${escapeHtml(a.action_name || "")}</div>
      ${a.instruction_text ? `<div class="action-card-instr markdown-body">${renderMarkdown(a.instruction_text)}</div>` : ""}
      <div class="action-card-foot">
        ${manualBtn}
        ${stats ? `<div class="action-stats">${stats}</div>` : ""}
      </div>
    </div>`;
}

function buildDiagnoseBody(c) {
  const resp = c.response;
  if (!resp) return "";

  const issue = resp.current_issue;
  const evidence = resp.log_evidence || [];
  const actions = issue?.action_options || [];
  const awaiting = resp.awaiting_clarification;

  /* CASE A — clarification needed */
  if (awaiting) {
    let html = `<div class="diag-section clarify-section">
      <div class="diag-section-label"><i class="fa fa-circle-question"></i> I need a quick clarification</div>
      <div class="clarify-question">${renderMarkdownInline(resp.clarification_question || "Please clarify:")}</div>`;
    if (resp.clarification_options?.length) {
      html += `<div class="clarify-options">` +
        resp.clarification_options.map(o => `
          <button class="clarify-opt" data-clarify="${escapeHtml(stripMarkdown(o.label || ""))}">
            <div class="clarify-opt-label">${renderMarkdownInline(o.label || "")}</div>
            ${o.description ? `<div class="clarify-opt-desc">${escapeHtml(o.description)}</div>` : ""}
          </button>`).join("") + `</div>`;
    }
    html += `</div>`;
    return html;
  }

  /* CASE B — fallback / no structured issue */
  if (!issue) {
    return `<div class="diag-fallback markdown-body">${renderMarkdown(resp.reply || "(no diagnosis)")}</div>`;
  }

  /* CASE C — structured diagnose */
  const evidenceCount = evidence.reduce((s, m) => s + (m.occurrence_count || 1), 0);
  // Aggregate resolved-vs-open from top matches
  let resolvedCount = 0, openCount = 0, lastSeen = null;
  evidence.forEach(m => {
    const top = m.top_match_log || m.top_match || {};
    const recent = m.most_recent_log || m.most_recent || {};
    const oc = (top.outcome || recent.outcome || "").toLowerCase();
    if (oc.includes("resolved") || oc === "ok" || oc === "closed") resolvedCount++;
    else if (oc) openCount++;
    const t = recent.occurred_at || top.occurred_at;
    if (t && (!lastSeen || t > lastSeen)) lastSeen = t;
  });
  const evidenceChip = evidence.length
    ? `<button class="cause-evidence-chip" data-act="show-evidence" title="Open past occurrences in inspector">
         <i class="fa fa-clock-rotate-left"></i>
         <b>${evidenceCount}</b> similar past event${evidenceCount === 1 ? "" : "s"}
         ${resolvedCount ? `<span class="ev-pill ok">${resolvedCount} resolved</span>` : ""}
         ${lastSeen ? `<span class="muted">last: ${relativeTime(lastSeen)}</span>` : ""}
         <i class="fa fa-arrow-right" style="font-size:9px;opacity:.5"></i>
       </button>` : "";

  // "Likely cause" callout
  const causeBlock = `
    <div class="diag-section cause-section">
      <div class="diag-section-label">
        <i class="fa fa-bullseye"></i> Likely cause
        ${resp.issue_number && resp.total_issues > 1
          ? `<span class="cause-pos">cause ${resp.issue_number} of ${resp.total_issues}</span>` : ""}
      </div>
      <div class="cause-name">${escapeHtml(issue.failure_mode_name || "—")}</div>
      ${issue.component_name ? `<div class="cause-component">on <strong>${escapeHtml(issue.component_name)}</strong></div>` : ""}
      ${evidenceChip}
    </div>`;

  // "Try this first" + alternatives
  let actionsBlock = "";
  if (actions.length) {
    const primary = renderActionCard(actions[0], true);
    const altsHtml = actions.length > 1
      ? `<details class="alt-actions">
           <summary><i class="fa fa-chevron-right"></i> ${actions.length - 1} alternative action${actions.length - 1 === 1 ? "" : "s"} to try</summary>
           <div class="alt-actions-list">${actions.slice(1).map(a => renderActionCard(a, false)).join("")}</div>
         </details>` : "";
    actionsBlock = `
      <div class="diag-section action-section">
        <div class="diag-section-label"><i class="fa fa-screwdriver-wrench"></i> Try this first</div>
        ${primary}
        ${altsHtml}
      </div>`;
  }

  return causeBlock + actionsBlock;
}

function buildDiagnoseFooter(c) {
  const resp = c.response;
  const hasMore = !!resp?.has_more_issues;

  if (c.outcome === "resolved") {
    return `
      <div class="case-foot foot-resolved">
        <span class="foot-state-label"><i class="fa-solid fa-circle-check"></i> Case resolved · feedback recorded</span>
        <div class="case-foot-right">
          <button class="ghost-btn" data-act="reopen"><i class="fa fa-rotate-left"></i> Reopen</button>
        </div>
      </div>`;
  }

  if (c.outcome === "not_resolved") {
    return `
      <div class="case-foot foot-not-resolved">
        <span class="foot-state-label"><i class="fa-solid fa-circle-xmark"></i> Marked not resolved</span>
        <div class="case-foot-right">
          ${hasMore ? `<button class="primary-btn" data-act="next-cause"><i class="fa fa-forward"></i> Try next likely cause</button>` : ""}
          <button class="ghost-btn" data-act="follow-up"><i class="fa fa-comments"></i> Ask follow-up</button>
        </div>
      </div>`;
  }

  // Default — awaiting outcome
  return `
    <div class="case-foot">
      <span class="foot-state-label">After running the action above:</span>
      <button class="outcome-btn ok" data-out="resolved">
        <i class="fa-solid fa-check"></i> It worked
      </button>
      <button class="outcome-btn ${hasMore ? "next" : "no"}" data-out="not_resolved">
        <i class="fa-solid fa-${hasMore ? "forward" : "xmark"}"></i>
        ${hasMore ? "Didn't work — try next" : "Didn't work"}
      </button>
      <div class="case-foot-right">
        <button class="ghost-btn" data-act="follow-up" title="Continue this case with a follow-up question">
          <i class="fa fa-comments"></i> Ask follow-up
        </button>
      </div>
    </div>`;
}

/* ============================================================
   PAST EVENTS BODY
   ============================================================ */
function buildPastBody(c) {
  const resp = c.response;
  if (!resp) return "";

  const evidence = resp.log_evidence || [];
  const summary = resp.metrics?.log_summary || null;
  const total = summary?.row_count ?? evidence.reduce((s, m) => s + (m.occurrence_count || 1), 0);
  const sigCount = summary?.top_event_signatures?.length ?? evidence.length;

  // Collect event rows
  const rows = [];
  evidence.forEach(m => {
    const top = m.top_match_log || m.top_match || {};
    const recent = m.most_recent_log || m.most_recent || {};
    if (top.log_id || top.occurred_at) rows.push({ ...top, _sig: m.event_signature_id, _count: m.occurrence_count });
    if (recent.log_id && recent.log_id !== top.log_id) rows.push({ ...recent, _sig: m.event_signature_id });
  });
  rows.sort((a, b) => (b.occurred_at || "").localeCompare(a.occurred_at || ""));
  const mostRecent = rows[0]?.occurred_at || evidence
    .map(m => m.last_seen_at || m.most_recent?.occurred_at || m.most_recent_log?.occurred_at || "")
    .filter(Boolean)
    .sort((a, b) => b.localeCompare(a))[0];

  const tableHtml = rows.length ? `
    <table class="events-table">
      <thead>
        <tr>
          <th>When</th><th>Sev</th><th>Title</th><th>Component</th><th>Action taken</th>
        </tr>
      </thead>
      <tbody>
        ${rows.slice(0, 12).map(r => `
          <tr>
            <td><span style="font-family:var(--font-mono);color:var(--slate-500);font-size:11px;">${fmtDate(r.occurred_at)}</span></td>
            <td><span class="sev-pill s${r.severity_number || 0}">${r.severity_number || "—"}</span></td>
            <td><div class="ev-title-cell">${escapeHtml(r.title || r.event_name || "")}</div></td>
            <td><span class="ev-comp">${escapeHtml(r.component_name_raw || "")}</span></td>
            <td>${escapeHtml(r.action_taken || "—")}</td>
          </tr>`).join("")}
      </tbody>
    </table>
  ` : `<div style="color:var(--slate-500);font-style:italic;padding:8px 0;">${renderMarkdown(resp.reply || "No events found.")}</div>`;

  return `
    <div class="past-summary">
      <div class="past-summary-card">
        <div class="psc-label">Total events</div>
        <div class="psc-value">${total}</div>
        <div class="psc-sub">${sigCount} distinct signature${sigCount !== 1 ? "s" : ""}</div>
      </div>
      <div class="past-summary-card">
        <div class="psc-label">Most recent</div>
        <div class="psc-value" style="font-size:13px;margin-top:6px;">${mostRecent ? fmtDate(mostRecent) : "—"}</div>
      </div>
      <div class="past-summary-card">
        <div class="psc-label">Top component</div>
        <div class="psc-value" style="font-size:12px;margin-top:6px;">${summary?.top_components?.[0]?.component_id || evidence[0]?.top_match_log?.component_name_raw || evidence[0]?.top_match?.component_name_raw || "—"}</div>
      </div>
    </div>
    ${tableHtml}
  `;
}

function buildPastFooter(c) {
  return `
    <div class="case-foot">
      <span class="case-foot-label">Want to act on this?</span>
      <button class="primary-btn" data-act="open-log-navigator">
        <i class="fa fa-table-list"></i> Open log navigator
      </button>
      <div class="case-foot-right">
        <button class="outcome-btn" data-act="spawn-diag">
          <i class="fa fa-stethoscope"></i> Start a diagnosis from a row
        </button>
        <button class="next-cause-cta" data-act="export-csv">
          <i class="fa fa-file-export"></i> Export CSV
        </button>
      </div>
    </div>
  `;
}

/* ============================================================
   WIRE CASE INTERACTIONS
   ============================================================ */
function wireCase(article, c) {
  // Resolve current selected action freshly from DOM each time
  const getSelectedActionId = () => {
    const sel = article.querySelector(".action-card.primary[data-action-id]")
             || article.querySelector(".action-card[data-action-id]");
    return sel?.dataset.actionId || c.response?.current_issue?.action_options?.[0]?.action_id || null;
  };

  // Refresh just the footer (without re-wiring — delegation handles it)
  const refreshFooter = () => {
    const f = article.querySelector(".case-foot");
    if (f) f.outerHTML = buildDiagnoseFooter(c);
  };

  article.addEventListener("click", async e => {
    // Mode toggle
    const mt = e.target.closest(".mt-btn");
    if (mt) {
      const toggle = mt.closest("[data-mode-toggle]");
      toggle.querySelectorAll(".mt-btn").forEach(b => b.classList.remove("active"));
      mt.classList.add("active");
      c.mode = mt.dataset.mode;
      toast(mt.dataset.mode === "fast"
        ? "Fast mode — one-shot recommendation"
        : "Guided mode — step-by-step questions");
      return;
    }

    // Manual link chip — must come BEFORE generic action-card handler
    const ml = e.target.closest(".manual-link");
    if (ml) {
      e.stopPropagation();
      openInspector("pdf", { title: ml.dataset.manualTitle, page: ml.dataset.manualPage });
      return;
    }

    // Outcome buttons (worked / didn't work)
    const out = e.target.closest("[data-out]");
    if (out) {
      const k = out.dataset.out; // "resolved" | "not_resolved"
      out.disabled = true;
      out.innerHTML = '<i class="fa fa-spinner fa-spin"></i> Saving…';
      const selId = getSelectedActionId();
      try {
        await apiPost(`/instances/${State.instanceId}/log-outcome`, {
          session_id: c.sessionId,
          outcome: k,
          selected_action_id: selId || undefined,
        });
        c.outcome = k;
        toast(k === "resolved" ? "✓ Marked as resolved" : "Marked as not resolved");
        refreshFooter();
        // Auto-fetch next likely cause if user said it didn't work and there is one
        if (k === "not_resolved" && c.response?.has_more_issues) {
          await handleNextCause(c, article);
        }
      } catch (err) {
        toast("Failed to save outcome: " + err.message);
        refreshFooter();
      }
      return;
    }

    // Reopen (resolved → undo)
    const reopen = e.target.closest("[data-act='reopen']");
    if (reopen) {
      c.outcome = null;
      refreshFooter();
      return;
    }

    // Follow-up — focus composer with this case's session_id
    const fu = e.target.closest("[data-act='follow-up']");
    if (fu) {
      const ta = $("#composer");
      const issueName = c.response?.current_issue?.failure_mode_name || "this issue";
      const compName  = c.response?.current_issue?.component_name;
      const action = c.response?.current_issue?.action_options?.[0]?.action_name;
      ta.value = `I tried "${action || "the suggested action"}" for ${issueName}${compName ? " on " + compName : ""}, but the symptom persists. What else should I check?`;
      ta.dispatchEvent(new Event("input"));
      ta.focus();
      // Tag composer with session id so onSend continues the case
      ta.dataset.continueSessionId = c.sessionId;
      toast("Edit the question, then press Enter to continue this case");
      return;
    }

    // Action select (primary swap)
    const actionEl = e.target.closest(".action-card[data-action-id]");
    if (actionEl) {
      article.querySelectorAll(".action-card").forEach(el => el.classList.remove("primary"));
      article.querySelectorAll(".action-card").forEach(el => el.classList.add("alt"));
      actionEl.classList.add("primary");
      actionEl.classList.remove("alt");
      return;
    }

    // Clarification option
    const clarify = e.target.closest(".clarify-opt");
    if (clarify) {
      const text = clarify.dataset.clarify;
      if (text) {
        $("#composer").value = text;
        $("#composer").dispatchEvent(new Event("input"));
        onSend(c.sessionId);
      }
      return;
    }

    // Evidence chip → open inspector with full past-occurrences panel
    const evi = e.target.closest("[data-act='show-evidence']");
    if (evi) {
      openInspector("evidence", { caseObj: c });
      return;
    }

    // Past events → open full log navigator in the inspector
    const navLogs = e.target.closest("[data-act='open-log-navigator']");
    if (navLogs) {
      openInspector("past", { caseObj: c });
      return;
    }

    // "Diagnose this" from a past-evidence row → spawn new case using row title
    const dthis = e.target.closest("[data-act='diagnose-row']");
    if (dthis) {
      const seed = dthis.dataset.seed || "";
      if (seed) {
        const ta = $("#composer");
        ta.value = seed;
        ta.dispatchEvent(new Event("input"));
        delete ta.dataset.continueSessionId;
        ta.focus();
        toast("Question prefilled — press Enter to spawn a new diagnose case");
      }
      return;
    }

    // Next possible cause (manual trigger)
    const nc = e.target.closest("[data-act='next-cause']");
    if (nc && !nc.disabled) {
      await handleNextCause(c, article);
      return;
    }

    // Spawn diagnosis from past card
    const sd = e.target.closest("[data-act='spawn-diag']");
    if (sd) { toast("Click a row in the table, then use 'Troubleshoot now'"); return; }

    // Export CSV
    const ex = e.target.closest("[data-act='export-csv']");
    if (ex) { toast("Export CSV — coming soon"); return; }

    // Bookmark
    const bm = e.target.closest("[data-act='bookmark']");
    if (bm) {
      const on = BookmarkStore.toggle(c.sessionId);
      const icon = bm.querySelector("i");
      icon.className = on ? "fa-solid fa-bookmark" : "fa-regular fa-bookmark";
      bm.classList.toggle("on", on);
      toast(on ? "★ Case bookmarked" : "Bookmark removed");
      renderSessions();
      return;
    }

    // Three-dots menu open
    const mtrig = e.target.closest("[data-act='menu']");
    if (mtrig) {
      e.stopPropagation();
      const menu = mtrig.parentElement.querySelector(".case-menu");
      const wasOpen = !menu.hidden;
      document.querySelectorAll(".case-menu").forEach(m => { m.hidden = true; });
      menu.hidden = wasOpen;
      return;
    }

    // Menu items
    const item = e.target.closest(".cm-item");
    if (item) {
      item.closest(".case-menu").hidden = true;
      const action = item.dataset.menu;
      if (action === "rerun") {
        const ta = $("#composer");
        ta.value = c.question;
        ta.dispatchEvent(new Event("input"));
        ta.focus();
        const pill = document.querySelector(`.intent-pill[data-intent="${c.kind}"]`);
        if (pill && !pill.dataset.soon) {
          document.querySelectorAll(".intent-pill").forEach(p => p.classList.remove("active"));
          pill.classList.add("active");
          State.activeIntent = c.kind;
          updateIntentExplain();
        }
        toast("Question reloaded — press Enter to re-run");
      } else if (action === "copylink") {
        const url = location.href.split("#")[0] + "#case=" + c.sessionId;
        navigator.clipboard?.writeText(url).then(
          () => toast("🔗 Link copied"),
          () => toast("Link: " + url)
        ) ?? toast("Link: " + url);
      } else if (action === "exportpdf") {
        toast("🖨 Opening print dialog…");
        setTimeout(() => window.print(), 300);
      } else if (action === "delete") {
        if (confirm(`Delete this case?\n\n"${c.question}"\n\nThis cannot be undone.`)) {
          // Remove from spine
          const idx = State.cases.findIndex(x => x.id === c.id);
          if (idx >= 0) State.cases.splice(idx, 1);
          // Remove from sessions rail
          const sidx = State.sessions.findIndex(s => s.session_id === c.sessionId);
          if (sidx >= 0) State.sessions.splice(sidx, 1);
          // Remove bookmark if any
          const bs = BookmarkStore.get();
          if (bs.has(c.sessionId)) { bs.delete(c.sessionId); BookmarkStore.save(bs); }
          if (c.sessionId === restoreSessionId()) clearSessionId();
          renderSpine();
          renderSessions();
          toast("Case deleted");
        }
      }
    }
  });
}

/* ============================================================
   NEXT POSSIBLE CAUSE
   ============================================================ */
async function handleNextCause(c, article) {
  if (!c.sessionId) return;
  const ncBtn = article.querySelector("[data-act='next-cause']");
  if (ncBtn) { ncBtn.disabled = true; ncBtn.innerHTML = '<i class="fa fa-spinner fa-spin"></i> Loading next cause…'; }
  try {
    const resp = await apiPost(`/instances/${State.instanceId}/next-issue`, {
      session_id: c.sessionId,
      mode: c.mode || "fast",
    });
    c.response = resp;
    c.outcome = null; // fresh cause → fresh feedback
    const bodyEl = article.querySelector(".case-body");
    if (bodyEl) bodyEl.innerHTML = buildDiagnoseBody(c);
    const footEl = article.querySelector(".case-foot");
    if (footEl) footEl.outerHTML = buildDiagnoseFooter(c);
    toast("Next possible cause loaded");
  } catch (e) {
    toast("Failed: " + e.message);
    if (ncBtn) { ncBtn.disabled = false; ncBtn.innerHTML = '<i class="fa fa-forward"></i> Try next likely cause'; }
  }
}

/* ============================================================
   COMPOSER
   ============================================================ */
const intentMeta = {
  diagnose: { explain: "A guided diagnosis card will open in your spine." },
  past:     { explain: "A past-events card with history and filters will open." },
};

function setupComposer() {
  const pills = $$(".intent-pill");
  pills.forEach(b => {
    b.addEventListener("click", () => {
      if (b.dataset.soon) {
        toast("🚧 Funzionalità in sviluppo — disponibile a breve");
        return;
      }
      pills.forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      State.activeIntent = b.dataset.intent;
      updateIntentExplain();
    });
  });

  const ta = $("#composer");
  ta.addEventListener("input", () => {
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 140) + "px";
  });
  ta.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSend(ta.dataset.continueSessionId || null); }
  });
  $("#send-btn").addEventListener("click", () => onSend(ta.dataset.continueSessionId || null));
  ta.addEventListener("input", () => {
    // Clear continueSessionId once the user starts editing (so the next plain send opens a new case)
    if (ta.dataset.continueWatermark !== ta.value && ta.dataset.continueSessionId) {
      // Keep it for one send if the value still matches the prefilled prompt; clear once user types beyond it
      // (No-op kept simple — clear it when the user wipes the textarea entirely.)
      if (!ta.value.trim()) delete ta.dataset.continueSessionId;
    }
  });

  // Read deeplink #case= on load
  const hash = location.hash.match(/#case=(.+)/);
  if (hash) setTimeout(() => loadSession(hash[1]), 500);
}

function updateIntentExplain() {
  const el = $("#intent-explain");
  if (el) el.textContent = (intentMeta[State.activeIntent] || intentMeta.diagnose).explain;
}

/* ---------- SEND ---------- */
async function onSend(continueSessionId = null) {
  const ta = $("#composer");
  const message = (ta.value || "").trim();
  if (!message) { toast("Type your question first"); return; }
  if (!State.instanceId) { toast("Pick an asset first"); return; }

  ta.value = "";
  ta.style.height = "auto";
  delete ta.dataset.continueSessionId; // consumed

  // Create a loading placeholder case
  const now = new Date();
  const ts = now.toTimeString().slice(0, 5);
  const tempId = "case-tmp-" + Date.now();
  const kind = State.activeIntent === "past" ? "past" : "diagnose";

  const caseObj = {
    id: tempId,
    kind,
    question: message,
    ts,
    sessionId: continueSessionId || null,
    response: null,
    mode: "fast",
    outcome: null,
  };

  State.cases.push(caseObj);
  State.activeCaseId = tempId;
  renderSpine();
  scrollToCase(tempId);

  const sendBtn = $("#send-btn");
  if (sendBtn) sendBtn.disabled = true;

  try {
    const body = {
      message,
      session_id: continueSessionId || undefined,
      mode: caseObj.mode,
      behavior_mode: kind === "past" ? "search_past_events" : "solve_current_problem",
    };
    const resp = await apiPost(`/instances/${State.instanceId}/chat`, body);
    const realSessionId = resp.session_id;
    const realKind = resp.behavior_mode === "search_past_events" ? "past" : "diagnose";

    // Update the case in State.cases
    const idx = State.cases.findIndex(x => x.id === tempId);
    if (idx >= 0) {
      State.cases[idx].sessionId = realSessionId;
      State.cases[idx].id = "case-" + realSessionId;
      State.cases[idx].kind = realKind;
      State.cases[idx].response = resp;
      State.activeCaseId = State.cases[idx].id;
    }

    persistSessionId(realSessionId);

    // Update sessions list
    const existingSession = State.sessions.find(s => s.session_id === realSessionId);
    if (!existingSession) {
      State.sessions.unshift({
        session_id: realSessionId,
        first_user_message: message,
        message_count: 2,
        last_message_at: new Date().toISOString(),
        behavior_mode: resp.behavior_mode,
      });
    } else {
      existingSession.last_message_at = new Date().toISOString();
      existingSession.message_count = (existingSession.message_count || 0) + 2;
    }

    renderSpine();
    renderSessions();
    scrollToCase(State.activeCaseId);
  } catch (e) {
    // Mark case as error
    const idx = State.cases.findIndex(x => x.id === tempId);
    if (idx >= 0) {
      State.cases[idx].response = { reply: "Error: " + e.message };
      State.cases[idx].kind = "diagnose";
      renderSpine();
    }
    toast("Error: " + e.message);
  } finally {
    if (sendBtn) sendBtn.disabled = false;
  }
}

/* ============================================================
   INSPECTOR
   ============================================================ */
// Tracks current inspector content for the "open in new window" button
const InspState = { kind: null, payload: null };

function setupInspector() {
  const insp = $("#inspector");

  $("#insp-close").addEventListener("click", e => {
    e.stopPropagation();
    closeInspector();
  });

  $("#insp-external").addEventListener("click", () => {
    if (InspState.kind === "pdf") {
      const { title, page } = InspState.payload;
      window.open(manualUrl(title, page), "_blank", "noopener");
    } else if (InspState.kind === "kg") {
      openKGWindow();
    }
  });

  insp.addEventListener("click", () => {
    if (window.innerWidth <= 1080 && !insp.classList.contains("overlay")) {
      insp.classList.add("overlay");
      return;
    }
    if (document.body.classList.contains("inspector-collapsed")) {
      document.body.classList.remove("inspector-collapsed");
    }
  });
}

function openInspector(kind, payload) {
  const insp = $("#inspector");
  document.body.classList.remove("inspector-collapsed");
  if (window.innerWidth <= 1080) insp.classList.add("overlay");

  InspState.kind = kind;
  InspState.payload = payload || {};

  $("#insp-empty").hidden = true;
  $("#insp-content").hidden = false;
  const crumb   = $("#insp-crumb");
  const body    = $("#insp-body");
  const extBtn  = $("#insp-external");

  if (kind === "kg") {
    extBtn.hidden = false;
    crumb.innerHTML = `
      <span class="insp-icon"><i class="fa fa-project-diagram"></i></span>
      <span class="insp-kind">KNOWLEDGE GRAPH</span>
      <span class="insp-name">Asset network</span>
    `;
    body.innerHTML = `
      <div class="insp-kg" id="kg-container"></div>
      <div class="kg-legend" id="kg-legend-inner"></div>
    `;
    loadKGGraph();

  } else if (kind === "pdf") {
    const { title, page } = payload || {};
    const url = manualUrl(title, page);
    extBtn.hidden = false;
    crumb.innerHTML = `
      <span class="insp-icon"><i class="fa fa-file-pdf"></i></span>
      <span class="insp-kind">MANUAL</span>
      <span class="insp-name">${escapeHtml(title || "Document")}${page ? " · §" + page : ""}</span>
    `;
    body.innerHTML = `<div class="insp-pdf"><iframe src="${encodeURI(url)}" title="${escapeHtml(title || "Manual")}"></iframe></div>`;

  } else if (kind === "evidence") {
    extBtn.hidden = true; // no external view for evidence (yet)
    const c = payload?.caseObj;
    const issue = c?.response?.current_issue || {};
    crumb.innerHTML = `
      <span class="insp-icon"><i class="fa fa-clock-rotate-left"></i></span>
      <span class="insp-kind">PAST OCCURRENCES</span>
      <span class="insp-name">${escapeHtml(issue.failure_mode_name || "Evidence")}${issue.component_name ? " · " + escapeHtml(issue.component_name) : ""}</span>
    `;
    body.innerHTML = renderEvidencePanel(c);

  } else if (kind === "past") {
    extBtn.hidden = true;
    const c = payload?.caseObj;
    crumb.innerHTML = `
      <span class="insp-icon"><i class="fa fa-table-list"></i></span>
      <span class="insp-kind">LOG NAVIGATOR</span>
      <span class="insp-name">${escapeHtml(truncate(c?.question || "Past events", 54))}</span>
    `;
    body.innerHTML = renderLogNavigatorShell();
    loadLogNavigator(c);
  }
}

/* ---------- Evidence panel renderer ---------- */
function renderEvidencePanel(c) {
  const resp = c?.response || {};
  const evidence = resp.log_evidence || [];
  if (!evidence.length) {
    return `<div class="ev-panel-empty">No past evidence available for this diagnosis.</div>`;
  }

  // Pick the global "top" — first item is highest score per backend ordering
  const head = evidence[0];
  const topLog = head.top_match_log || head.top_match || {};
  const recentLog = head.most_recent_log || head.most_recent || {};

  const sevPill = sev => sev != null ? `<span class="sev-pill s${sev}">${sev}</span>` : "—";
  const fmtOutcome = oc => {
    const s = (oc || "").toLowerCase();
    if (!s) return `<span class="ev-out unk">unknown</span>`;
    if (s.includes("resolved") || s === "ok" || s === "closed") return `<span class="ev-out ok"><i class="fa fa-check"></i> ${escapeHtml(oc)}</span>`;
    return `<span class="ev-out warn">${escapeHtml(oc)}</span>`;
  };

  // Build flat row list across all signatures
  const rows = [];
  evidence.forEach(m => {
    const t = m.top_match_log || m.top_match || {};
    const r = m.most_recent_log || m.most_recent || {};
    const seen = new Set();
    [t, r].forEach(rec => {
      if (rec && rec.log_id && !seen.has(rec.log_id)) {
        seen.add(rec.log_id);
        rows.push({ ...rec, _sig: m.event_signature_id, _isTop: rec.log_id === t.log_id });
      }
    });
  });
  rows.sort((a, b) => (b.occurred_at || "").localeCompare(a.occurred_at || ""));

  // Top-match featured card
  const topCard = `
    <section class="ev-top-card">
      <div class="ev-top-head">
        <span class="ev-badge"><i class="fa fa-bullseye"></i> CLOSEST MATCH</span>
        ${head.score != null ? `<span class="ev-score">${Math.round(head.score * 100)}% similarity</span>` : ""}
      </div>
      <div class="ev-top-title">${escapeHtml(topLog.title || topLog.event_name || head.event_signature_id || "Past event")}</div>
      <div class="ev-top-meta">
        ${sevPill(topLog.severity_number)}
        <span><i class="fa fa-calendar"></i> ${fmtDate(topLog.occurred_at)} <span class="muted">(${relativeTime(topLog.occurred_at)})</span></span>
        ${topLog.component_name_raw ? `<span><i class="fa fa-microchip"></i> ${escapeHtml(topLog.component_name_raw)}</span>` : ""}
        ${topLog.work_order_id ? `<span><i class="fa fa-clipboard-list"></i> WO ${escapeHtml(topLog.work_order_id)}</span>` : ""}
      </div>
      ${topLog.body ? `<div class="ev-top-body">${escapeHtml(topLog.body)}</div>` : ""}

      <div class="ev-resolved-block">
        <div class="ev-resolved-head"><i class="fa-solid fa-wrench"></i> HOW IT WAS RESOLVED</div>
        ${topLog.action_taken
          ? `<div class="ev-resolved-text">${escapeHtml(topLog.action_taken)}</div>`
          : `<div class="ev-resolved-text muted-italic">No action recorded for this event.</div>`}
        <div class="ev-resolved-foot">
          ${fmtOutcome(topLog.outcome)}
          ${topLog.downtime_min != null ? `<span class="ev-foot-stat">Downtime <b>${topLog.downtime_min} min</b></span>` : ""}
          ${topLog.actual_duration_min != null ? `<span class="ev-foot-stat">Repair time <b>${topLog.actual_duration_min} min</b></span>` : ""}
        </div>
      </div>

      <div class="ev-top-actions">
        <button class="primary-btn" data-act="diagnose-row" data-seed="${escapeHtml(topLog.title || head.event_signature_id || "")}">
          <i class="fa fa-stethoscope"></i> Diagnose this
        </button>
      </div>
    </section>`;

  // All occurrences table
  const tableRows = rows.slice(0, 30).map(r => `
    <tr>
      <td><span class="muted">${fmtDate(r.occurred_at)}</span></td>
      <td>${sevPill(r.severity_number)}</td>
      <td><div class="ev-cell-title">${escapeHtml(r.title || r.event_name || "")}</div></td>
      <td><span class="muted-2">${escapeHtml(r.component_name_raw || "—")}</span></td>
      <td><div class="ev-cell-action">${escapeHtml(truncate(r.action_taken || "—", 80))}</div></td>
      <td>${fmtOutcome(r.outcome)}</td>
      <td><button class="ev-row-act" data-act="diagnose-row" data-seed="${escapeHtml(r.title || r._sig || "")}" title="Diagnose this"><i class="fa fa-stethoscope"></i></button></td>
    </tr>`).join("");

  const tableSection = `
    <section class="ev-list-section">
      <h4 class="ev-section-title">All occurrences <span class="muted-2">(${rows.length})</span></h4>
      <div class="ev-table-wrap">
        <table class="ev-table">
          <thead>
            <tr>
              <th>When</th><th>Sev</th><th>Title</th><th>Component</th><th>Action taken</th><th>Outcome</th><th></th>
            </tr>
          </thead>
          <tbody>${tableRows}</tbody>
        </table>
      </div>
    </section>`;

  // Other matched signatures (above and beyond the top one)
  const sigSection = evidence.length > 1 ? `
    <section class="ev-sig-section">
      <h4 class="ev-section-title">Matched signatures <span class="muted-2">(${evidence.length})</span></h4>
      <div class="ev-sig-list">
        ${evidence.map((m, i) => `
          <div class="ev-sig ${i === 0 ? "primary" : ""}">
            <div class="ev-sig-name">${escapeHtml(m.event_signature_id || "(unknown)")}</div>
            <div class="ev-sig-meta">
              <span><b>${m.occurrence_count || 1}</b> occurrence${m.occurrence_count === 1 ? "" : "s"}</span>
              ${m.score != null ? `<span>${Math.round(m.score * 100)}% match</span>` : ""}
              ${m.linked_failure_mode_id ? `<span class="muted-2">${escapeHtml(m.linked_failure_mode_id)}</span>` : ""}
            </div>
            ${m.rerank_rationale ? `<div class="ev-sig-rationale">${escapeHtml(m.rerank_rationale)}</div>` : ""}
          </div>`).join("")}
      </div>
    </section>` : "";

  return `<div class="ev-panel">${topCard}${tableSection}${sigSection}</div>`;
}

/* ---------- Past-events log navigator ---------- */
function renderLogNavigatorShell() {
  return `
    <div class="log-nav" id="log-nav">
      <div class="log-nav-loading">
        <i class="fa fa-spinner fa-spin"></i>
        <span>Loading log result…</span>
      </div>
    </div>`;
}

function logFilterParams(filters = {}, extra = {}) {
  const allowed = [
    "date_from", "date_to", "component_id", "linked_failure_mode_id",
    "maintenance_type", "event_category", "event_signature_id", "status",
    "severity_min", "q", "limit", "offset",
  ];
  const params = new URLSearchParams();
  Object.entries({ ...filters, ...extra }).forEach(([k, v]) => {
    if (!allowed.includes(k) || v == null || v === "") return;
    params.set(k, String(v));
  });
  return params.toString() ? "?" + params.toString() : "";
}

function logFiltersFromCase(c) {
  const f = c?.response?.metrics?.log_filters;
  return (f && typeof f === "object") ? f : {};
}

function evidenceSignatures(c) {
  const ev = c?.response?.log_evidence || [];
  return [...new Set(ev.map(m => m.event_signature_id).filter(Boolean))];
}

function rowFromEvidenceRecord(rec, signatureId = "") {
  return {
    log_id: rec.log_id || `${signatureId}-${rec.occurred_at || Math.random()}`,
    occurred_at: rec.occurred_at || "",
    severity_number: rec.severity_number,
    severity_text: rec.severity_text || "",
    status: rec.status || "",
    event_signature_id: rec.event_signature_id || signatureId || "",
    title: rec.title || rec.event_name || "",
    body: rec.body || "",
    action_taken: rec.action_taken || "",
    outcome: rec.outcome || "",
    work_order_id: rec.work_order_id || "",
    component_name_raw: rec.component_name_raw || "",
    component_id: rec.component_id || "",
    linked_failure_mode_id: rec.linked_failure_mode_id || "",
    actual_duration_min: rec.actual_duration_min,
    downtime_min: rec.downtime_min,
    _fromEvidence: true,
  };
}

function fallbackRowsFromEvidence(c) {
  const rows = [];
  (c?.response?.log_evidence || []).forEach(m => {
    const sig = m.event_signature_id || "";
    const seen = new Set();
    [m.top_match_log || m.top_match, m.most_recent_log || m.most_recent].forEach(rec => {
      if (!rec) return;
      const row = rowFromEvidenceRecord(rec, sig);
      if (!row.log_id || seen.has(row.log_id)) return;
      seen.add(row.log_id);
      rows.push(row);
    });
  });
  return rows;
}

function uniqueLogRows(rows) {
  const byId = new Map();
  rows.forEach(r => {
    if (!r?.log_id || byId.has(r.log_id)) return;
    byId.set(r.log_id, r);
  });
  return [...byId.values()].sort((a, b) => (b.occurred_at || "").localeCompare(a.occurred_at || ""));
}

async function loadLogNavigator(c) {
  const root = $("#log-nav");
  if (!root || !c?.response) return;

  const filters = logFiltersFromCase(c);
  const signatures = evidenceSignatures(c);
  const summaryReq = apiGet(`/instances/${State.instanceId}/logs/summary${logFilterParams(filters)}`).catch(() => null);

  let rowsReq;
  if (signatures.length) {
    rowsReq = Promise.all(signatures.slice(0, 8).map(sig =>
      apiGet(`/instances/${State.instanceId}/logs${logFilterParams(filters, { event_signature_id: sig, limit: 200 })}`)
        .then(data => data.items || [])
        .catch(() => [])
    )).then(chunks => chunks.flat());
  } else {
    rowsReq = apiGet(`/instances/${State.instanceId}/logs${logFilterParams(filters, { limit: 200 })}`)
      .then(data => data.items || [])
      .catch(() => []);
  }

  const [summary, fetchedRows] = await Promise.all([summaryReq, rowsReq]);
  const rows = uniqueLogRows(fetchedRows.length ? fetchedRows : fallbackRowsFromEvidence(c));
  root.innerHTML = renderLogNavigator(c, summary, rows, filters);
  wireLogNavigator(root, c, rows);
}

function severityLabel(row) {
  return row.severity_text || row.severity_number || "—";
}

function severityClass(row) {
  const n = Number(row.severity_number);
  if (n >= 18 || /fatal|error/i.test(row.severity_text || "")) return "s4";
  if (n >= 14 || /warn/i.test(row.severity_text || "")) return "s3";
  return "";
}

function scopeLabel(filters) {
  const bits = [];
  if (filters.date_from || filters.date_to) bits.push(`${filters.date_from || "start"} → ${filters.date_to || "now"}`);
  if (filters.status) bits.push(`status: ${filters.status}`);
  if (filters.maintenance_type) bits.push(`maintenance: ${filters.maintenance_type}`);
  if (filters.event_category) bits.push(`category: ${filters.event_category}`);
  if (filters.severity_min) bits.push(`severity >= ${filters.severity_min}`);
  return bits.length ? bits.join(" · ") : "All available logs";
}

function renderLogNavigator(c, summary, rows, filters) {
  const evidence = c.response?.log_evidence || [];
  const totalEvidence = evidence.reduce((s, m) => s + (m.occurrence_count || 0), 0);
  const total = summary?.row_count ?? (totalEvidence || rows.length);
  const topSig = summary?.top_event_signatures?.[0]?.event_signature_id
    || evidence[0]?.event_signature_id
    || rows[0]?.event_signature_id
    || "—";
  const topSigCount = summary?.top_event_signatures?.[0]?.occurrence_count || totalEvidence || rows.length;
  const signatures = [...new Set(rows.map(r => r.event_signature_id).filter(Boolean))];
  const selected = rows[0] || null;

  return `
    <div class="log-nav-headspace"></div>
    <section class="log-nav-summary">
      <div class="log-nav-card">
        <div class="ln-label">Scope</div>
        <div class="ln-scope">${escapeHtml(scopeLabel(filters))}</div>
      </div>
      <div class="log-nav-card">
        <div class="ln-label">Rows in scope</div>
        <div class="ln-value">${total}</div>
      </div>
      <div class="log-nav-card">
        <div class="ln-label">Top recurring case</div>
        <div class="ln-sig">${escapeHtml(topSig)}</div>
        <div class="ln-sub">${topSigCount} occurrence${topSigCount === 1 ? "" : "s"}</div>
      </div>
    </section>

    <section class="log-nav-controls">
      <div class="ln-search-wrap">
        <i class="fa fa-magnifying-glass"></i>
        <input class="ln-search" type="search" placeholder="Filter rows, work orders, components…" />
      </div>
      <select class="ln-signature-filter">
        <option value="">All signatures</option>
        ${signatures.map(sig => `<option value="${escapeHtml(sig)}">${escapeHtml(sig)}</option>`).join("")}
      </select>
    </section>

    <section class="log-nav-detail" id="log-nav-detail">
      ${selected ? renderLogNavigatorDetail(selected) : `<div class="ln-empty-detail">No log rows available for this result.</div>`}
    </section>

    <section class="log-nav-table-section">
      <div class="ln-table-head">
        <h4 class="ev-section-title">Result rows <span class="muted-2" id="ln-visible-count">(${rows.length})</span></h4>
      </div>
      <div class="ln-table-wrap">
        <table class="ln-table">
          <thead>
            <tr>
              <th>When</th><th>Sev</th><th>Signature</th><th>Title</th><th>Component</th><th>Status</th><th>WO</th><th></th>
            </tr>
          </thead>
          <tbody>
            ${rows.map((r, i) => renderLogNavigatorRow(r, i === 0)).join("")}
          </tbody>
        </table>
      </div>
    </section>`;
}

function renderLogNavigatorRow(r, selected = false) {
  const searchText = [
    r.title, r.body, r.action_taken, r.work_order_id, r.component_name_raw,
    r.event_signature_id, r.status, r.outcome,
  ].filter(Boolean).join(" ").toLowerCase();
  return `
    <tr class="${selected ? "selected" : ""}" data-log-id="${escapeHtml(r.log_id)}"
        data-sig="${escapeHtml(r.event_signature_id || "")}" data-search="${escapeHtml(searchText)}">
      <td><span class="muted">${fmtDate(r.occurred_at)}</span></td>
      <td><span class="sev-pill ${severityClass(r)}">${escapeHtml(severityLabel(r))}</span></td>
      <td><code class="ln-code">${escapeHtml(r.event_signature_id || "—")}</code></td>
      <td><div class="ev-cell-title">${escapeHtml(r.title || r.event_name || "—")}</div></td>
      <td><span class="muted-2">${escapeHtml(r.component_name_raw || r.component_id || "—")}</span></td>
      <td>${escapeHtml(r.status || "—")}</td>
      <td><code class="ln-code">${escapeHtml(r.work_order_id || "—")}</code></td>
      <td><button class="ev-row-act" data-act="select-log-row" title="Inspect row"><i class="fa fa-arrow-right"></i></button></td>
    </tr>`;
}

function renderLogNavigatorDetail(r) {
  return `
    <div class="ln-detail-head">
      <div>
        <div class="ln-label">Selected log</div>
        <div class="ln-detail-title">${escapeHtml(r.title || r.event_name || "Untitled log")}</div>
      </div>
      <button class="primary-btn" data-act="diagnose-selected-log">
        <i class="fa fa-stethoscope"></i> Diagnose this
      </button>
    </div>
    <div class="ln-detail-meta">
      <span><i class="fa fa-calendar"></i> ${fmtDate(r.occurred_at)}</span>
      <span>${escapeHtml(r.work_order_id || r.source_record_id || "No WO")}</span>
      <span>${escapeHtml(r.component_name_raw || r.component_id || "No component")}</span>
      <span>${escapeHtml(r.status || "status unknown")}</span>
      <span>${escapeHtml(r.outcome || "outcome unknown")}</span>
    </div>
    ${r.body ? `<div class="ln-detail-block"><b>Observed evidence</b><p>${escapeHtml(r.body)}</p></div>` : ""}
    ${r.action_taken ? `<div class="ln-detail-block action"><b>Action taken</b><p>${escapeHtml(r.action_taken)}</p></div>` : ""}
    <div class="ln-detail-stats">
      ${r.actual_duration_min != null ? `<span>Repair <b>${r.actual_duration_min} min</b></span>` : ""}
      ${r.downtime_min != null ? `<span>Downtime <b>${r.downtime_min} min</b></span>` : ""}
      ${r.linked_failure_mode_id ? `<span>KG link <code>${escapeHtml(r.linked_failure_mode_id)}</code></span>` : ""}
    </div>`;
}

function wireLogNavigator(root, c, rows) {
  let selectedLogId = rows[0]?.log_id || null;
  const search = root.querySelector(".ln-search");
  const sig = root.querySelector(".ln-signature-filter");
  const visibleCount = root.querySelector("#ln-visible-count");
  const detail = root.querySelector("#log-nav-detail");

  function applyFilters() {
    const q = (search?.value || "").trim().toLowerCase();
    const s = sig?.value || "";
    let visible = 0;
    root.querySelectorAll(".ln-table tbody tr").forEach(tr => {
      const okText = !q || (tr.dataset.search || "").includes(q);
      const okSig = !s || tr.dataset.sig === s;
      const on = okText && okSig;
      tr.hidden = !on;
      if (on) visible += 1;
    });
    if (visibleCount) visibleCount.textContent = `(${visible})`;
  }

  function selectRow(logId) {
    const row = rows.find(r => r.log_id === logId);
    if (!row || !detail) return;
    selectedLogId = logId;
    detail.innerHTML = renderLogNavigatorDetail(row);
    root.querySelectorAll(".ln-table tbody tr").forEach(tr => {
      tr.classList.toggle("selected", tr.dataset.logId === logId);
    });
  }

  search?.addEventListener("input", applyFilters);
  sig?.addEventListener("change", applyFilters);
  root.addEventListener("click", e => {
    const rowBtn = e.target.closest("[data-act='select-log-row']");
    if (rowBtn) {
      const tr = rowBtn.closest("tr[data-log-id]");
      if (tr) selectRow(tr.dataset.logId);
      return;
    }
    const diag = e.target.closest("[data-act='diagnose-selected-log']");
    if (diag) {
      const row = rows.find(r => r.log_id === selectedLogId);
      if (!row) return;
      const ta = $("#composer");
      ta.value = row.title || row.event_name || c.question || "";
      ta.dispatchEvent(new Event("input"));
      delete ta.dataset.continueSessionId;
      document.querySelectorAll(".intent-pill").forEach(p => p.classList.remove("active"));
      const diagnosePill = document.querySelector(".intent-pill[data-intent='diagnose']");
      if (diagnosePill) diagnosePill.classList.add("active");
      State.activeIntent = "diagnose";
      updateIntentExplain();
      ta.focus();
      toast("Question prefilled — press Enter to start troubleshooting");
    }
  });
}

function closeInspector() {
  const insp = $("#inspector");
  insp.classList.remove("overlay");
  document.body.classList.add("inspector-collapsed");
  $("#insp-empty").hidden = false;
  $("#insp-content").hidden = true;
  $("#insp-external").hidden = true;
  InspState.kind = null;
  InspState.payload = null;
}

function openKGWindow() {
  if (!State._lastKGData) { toast("Graph data not loaded yet"); return; }
  const data = State._lastKGData;
  const html = `<!DOCTYPE html>
<html><head><meta charset="UTF-8"/>
<title>Knowledge Graph</title>
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"><\/script>
<style>
  body { margin:0; background:#0F172A; }
  #kg { width:100vw; height:100vh; }
</style>
</head><body>
<div id="kg"></div>
<script>
const colorMap = ${JSON.stringify(data.color_map || {})};
const nodes = new vis.DataSet(${JSON.stringify((data.nodes||[]).map(n=>({
  id:n.id, label:n.label, group:n.group, title:n.title,
  color: (data.color_map||{})[n.group]||"#334155",
  shape:"dot", size:12, font:{color:"#E2E8F0",size:11}
})))});
const edges = new vis.DataSet(${JSON.stringify((data.edges||[]).map(e=>({
  from:e.from, to:e.to, label:e.label, arrows:"to",
  color:{color:"#475569",highlight:"#60A5FA"},
  font:{color:"#64748B",size:9}
})))});
new vis.Network(document.getElementById("kg"),{nodes,edges},{
  nodes:{font:{color:"#E2E8F0",size:11}},
  physics:{stabilization:{iterations:80}},
  interaction:{hover:true}
});
<\/script>
</body></html>`;
  const blob = new Blob([html], { type: "text/html" });
  const url  = URL.createObjectURL(blob);
  const win  = window.open(url, "_blank", "noopener");
  if (win) setTimeout(() => URL.revokeObjectURL(url), 10000);
}

async function loadKGGraph() {
  if (!State.instanceId) return;
  const container = document.getElementById("kg-container");
  if (!container) return;

  if (State.kgInited && State.kgNetwork) return;

  try {
    const data = await apiGet(`/instances/${State.instanceId}/graph-data`);
    State._lastKGData = data;
    const nodes = new vis.DataSet((data.nodes || []).map(n => ({
      id: n.id,
      label: n.label,
      group: n.group,
      title: n.title,
      color: data.color_map?.[n.group] || "#334155",
      shape: "dot",
      size: 12,
      font: { color: "#E2E8F0", size: 11 },
    })));
    const edges = new vis.DataSet((data.edges || []).map(e => ({
      from: e.from, to: e.to, label: e.label,
      arrows: "to",
      color: { color: "#475569", highlight: "#60A5FA" },
      font: { color: "#64748B", size: 9 },
    })));
    State.kgNetwork = new vis.Network(container, { nodes, edges }, {
      nodes: { font: { color: "#E2E8F0", size: 11 } },
      physics: { stabilization: { iterations: 80 } },
      interaction: { hover: true },
      layout: {},
    });
    State.kgInited = true;

    const lg = document.getElementById("kg-legend-inner");
    if (lg) {
      lg.innerHTML = "";
      Object.entries(data.color_map || {}).forEach(([k, v]) => {
        lg.insertAdjacentHTML("beforeend",
          `<span><span class="lg-dot" style="background:${v}"></span>${escapeHtml(k)}</span>`);
      });
    }
  } catch (e) {
    toast("KG load failed: " + e.message);
  }
}

/* ============================================================
   COLUMN RESIZERS
   ============================================================ */
function setupResizers() {
  const MIN_LEFT = 200, MAX_LEFT = 480;
  const MIN_RIGHT = 280, MAX_RIGHT = 720;
  const root = document.documentElement;

  try {
    const l = localStorage.getItem("mc.col.left");
    const r = localStorage.getItem("mc.col.right");
    if (l) root.style.setProperty("--col-left-w", l);
    if (r) root.style.setProperty("--col-right-w", r);
  } catch {}

  $$(".col-resizer").forEach(r => {
    r.addEventListener("pointerdown", ev => {
      ev.preventDefault();
      const which = r.dataset.resize;
      r.setPointerCapture(ev.pointerId);
      r.classList.add("dragging");
      document.body.classList.add("col-resizing");
      const sidebarW = document.querySelector(".sidebar").getBoundingClientRect().width;

      function onMove(e) {
        if (which === "left") {
          const newW = Math.max(MIN_LEFT, Math.min(MAX_LEFT, e.clientX - sidebarW));
          root.style.setProperty("--col-left-w", newW + "px");
        } else if (which === "right") {
          if (document.body.classList.contains("inspector-collapsed")) return;
          const newW = Math.max(MIN_RIGHT, Math.min(MAX_RIGHT, window.innerWidth - e.clientX));
          root.style.setProperty("--col-right-w", newW + "px");
        }
      }

      function onUp() {
        r.releasePointerCapture(ev.pointerId);
        r.classList.remove("dragging");
        document.body.classList.remove("col-resizing");
        r.removeEventListener("pointermove", onMove);
        r.removeEventListener("pointerup", onUp);
        r.removeEventListener("pointercancel", onUp);
        try {
          const cs = getComputedStyle(root);
          localStorage.setItem("mc.col.left", cs.getPropertyValue("--col-left-w").trim() || "264px");
          localStorage.setItem("mc.col.right", cs.getPropertyValue("--col-right-w").trim() || "400px");
        } catch {}
      }

      r.addEventListener("pointermove", onMove);
      r.addEventListener("pointerup", onUp);
      r.addEventListener("pointercancel", onUp);
    });

    r.addEventListener("dblclick", () => {
      if (r.dataset.resize === "left") {
        root.style.setProperty("--col-left-w", "264px");
        try { localStorage.removeItem("mc.col.left"); } catch {}
      } else {
        root.style.setProperty("--col-right-w", "400px");
        try { localStorage.removeItem("mc.col.right"); } catch {}
      }
      toast("Column reset");
    });
  });
}
