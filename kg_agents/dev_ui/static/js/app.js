// ── Constants ──
const COLOR_PALETTE = [
  "#6b8cba", "#7aab82", "#c47e5a", "#9b79b8",
  "#5fa8a0", "#b87a7a", "#a0a052", "#6b9eb8",
  "#b8906b", "#7a8fb8", "#88a87a"
];
const HIGHLIGHT_COLOR = "#58a6ff";
const HIGHLIGHT_EDGE_COLOR = "#58a6ff";
const DIM_NODE_COLOR = "#21262d";
const DIM_EDGE_COLOR = "#161b22";
const DIM_FONT_COLOR = "#30363d";
const ACTIVE_FONT_COLOR = "#f0f6fc";
const API_ROOT = "/v1/kg-agents";
const DEFAULT_INSTANCE_ID = "irc5-default-instance";

// ── State ──
let network = null;
let nodesDataSet = null;
let edgesDataSet = null;
let colorByType = {};
let originalNodes = {};   // id → original vis props
let originalEdges = {};   // id → original vis props
let nodeMetadata = {};    // id → { group, description, label }
let sessionId = null;
let activeInstanceId = null;

// ── Product metadata (populated from /product-info) ──
let _productName = 'Product';
let _productShortName = 'TS';
let _suggestedSymptoms = [];
const APP_TITLE = 'Knowledge Agents Dev UI';

function greetingMessage(productName) {
  if (productName && productName !== 'Product') {
    return `Hello Fabio, describe the issue or symptom you are experiencing with your ${productName} and I will look it up in the knowledge base.`;
  }
  return 'Hello Fabio, describe the issue or symptom you are experiencing and I will look it up in the knowledge base.';
}

function instanceApi(path) {
  if (!activeInstanceId) {
    throw new Error('No active instance selected');
  }
  return `${API_ROOT}/instances/${encodeURIComponent(activeInstanceId)}${path}`;
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  let data = null;
  try {
    data = await res.json();
  } catch (err) {
    data = null;
  }
  if (!res.ok) {
    const detail = data && (data.detail || data.error);
    throw new Error(detail || `Request failed with status ${res.status}`);
  }
  return data;
}

async function loadProductInfo() {
  try {
    const info = await fetchJson(instanceApi('/product-info'));
    _productName = info.product_name || 'Product';
    _productShortName = info.product_short_name || _productName.substring(0, 2).toUpperCase();
    _suggestedSymptoms = info.suggested_symptoms || [];

    document.title = APP_TITLE;
    document.getElementById('header-title').textContent = APP_TITLE;
    document.getElementById('header-logo').textContent = _productShortName.substring(0, 2);
  } catch (err) {
    console.warn('Could not load product info:', err);
    _productName = 'Product';
    _productShortName = 'TS';
    _suggestedSymptoms = [];
  }
}

function renderChips() {
  // Remove existing chips container if present
  const existing = document.getElementById('symptom-chips');
  if (existing) existing.remove();

  if (_suggestedSymptoms.length === 0) return;

  const container = document.createElement('div');
  container.className = 'chips-container';
  container.id = 'symptom-chips';
  _suggestedSymptoms.forEach(s => {
    const btn = document.createElement('button');
    btn.className = 'chip';
    btn.dataset.query = s.query;
    btn.textContent = s.label;
    container.appendChild(btn);
  });
  messagesEl.appendChild(container);
  container.addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    inputEl.value = chip.dataset.query;
    sendMessage();
  });
}

// ── Session stats tracking ──
const sessionStats = { symptoms: new Set(), fms: new Set(), actions: new Set(), totalNodes: 0 };

function updateSessionStats(trace) {
  if (!trace) return;
  (trace.symptom_ids || []).forEach(id => sessionStats.symptoms.add(id));
  (trace.failure_mode_ids || []).forEach(id => sessionStats.fms.add(id));
  (trace.action_ids || []).forEach(id => sessionStats.actions.add(id));
  const explored = sessionStats.symptoms.size + sessionStats.fms.size + sessionStats.actions.size;
  const coverage = sessionStats.totalNodes > 0 ? Math.round((explored / sessionStats.totalNodes) * 100) : 0;
  document.getElementById('stat-symptoms').textContent = sessionStats.symptoms.size;
  document.getElementById('stat-fms').textContent = sessionStats.fms.size;
  document.getElementById('stat-actions').textContent = sessionStats.actions.size;
  document.getElementById('stat-coverage').textContent = coverage + '%';
}

function resetSessionStats() {
  sessionStats.symptoms.clear();
  sessionStats.fms.clear();
  sessionStats.actions.clear();
  document.getElementById('stat-symptoms').textContent = '0';
  document.getElementById('stat-fms').textContent = '0';
  document.getElementById('stat-actions').textContent = '0';
  document.getElementById('stat-coverage').textContent = '0%';
}

const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');
const resetBtn = document.getElementById('reset-btn');
const modelSelect = document.getElementById('model-select');
const instanceSelect = document.getElementById('instance-select');

function renderInitialAssistantState() {
  messagesEl.innerHTML = '';
  appendMessage('assistant', greetingMessage(_productName));
  renderChips();
}

function pickInitialInstanceId(instances) {
  const params = new URLSearchParams(window.location.search);
  const queryInstanceId = params.get('instance_id');
  if (queryInstanceId && instances.some(i => i.id === queryInstanceId)) {
    return queryInstanceId;
  }
  if (instances.some(i => i.id === DEFAULT_INSTANCE_ID)) {
    return DEFAULT_INSTANCE_ID;
  }
  return instances[0] ? instances[0].id : null;
}

async function loadInstances() {
  const payload = await fetchJson(`${API_ROOT}/instances`);
  const instances = payload.instances || [];
  instanceSelect.innerHTML = '';
  instances.forEach(instance => {
    const option = document.createElement('option');
    option.value = instance.id;
    option.textContent = instance.name || instance.id;
    instanceSelect.appendChild(option);
  });
  return instances;
}

async function switchInstance(instanceId) {
  activeInstanceId = instanceId;
  if (instanceSelect.value !== instanceId) {
    instanceSelect.value = instanceId;
  }
  sessionId = null;
  resetSessionStats();
  hideTelemetryPanel();
  removePopover();
  await loadProductInfo();
  renderInitialAssistantState();
  await initGraph();
}

// ── Chat functions ──
function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formatInline(text) {
  let html = escapeHtml(text);
  html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(
    /(https?:\/\/[^\s<>"')\]]+)/g,
    '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>'
  );
  html = html.replace(
    /\[MANUAL:([^:\]]+):(\d+)\]/g,
    (_, title, page) =>
      `<button class="manual-btn" data-title="${escapeHtml(title)}" data-page="${page}">&#128196; Open manual p.${page}</button>`
  );
  return html;
}

function normalizeAssistantText(text) {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const cleaned = [];
  for (let i = 0; i < lines.length; i += 1) {
    const current = lines[i].trim();
    const next = (lines[i + 1] || '').trim();
    const isOrphanCounter = /^\d+[.)]?$/.test(current);
    const nextStartsSection = /^(?:\*\*)?(Possible issue|Affected component|Recommended corrective actions|Source):/i.test(next);
    if (isOrphanCounter && nextStartsSection) continue;
    cleaned.push(lines[i].replace(/\s+$/, ''));
  }
  return cleaned.join('\n').trim();
}

function buildAssistantHtml(text) {
  const normalized = normalizeAssistantText(text);
  if (!normalized) return '';

  const lines = normalized.split('\n');
  const blocks = [];
  let listItems = [];
  let currentItem = null;

  function pushCurrentItem() {
    if (!currentItem) return;
    listItems.push(currentItem);
    currentItem = null;
  }

  function flushList() {
    pushCurrentItem();
    if (!listItems.length) return;
    let html = '<ol class="assistant-list">';
    listItems.forEach(item => {
      html += '<li>';
      html += `<div class="assistant-action-title">${formatInline(item.title)}</div>`;
      if (item.lines.length) {
        item.lines.forEach(line => {
          html += `<div class="assistant-line">${formatInline(line)}</div>`;
        });
      }
      if (item.substeps.length) {
        html += '<div class="assistant-substeps">';
        item.substeps.forEach(step => {
          html += `<div class="assistant-substep"><span class="assistant-substep-num">${escapeHtml(step.num)}</span><span>${formatInline(step.text)}</span></div>`;
        });
        html += '</div>';
      }
      if (item.source) {
        html += `<div class="assistant-field assistant-source"><span class="assistant-label">Source:</span> ${formatInline(item.source)}</div>`;
      }
      html += '</li>';
    });
    html += '</ol>';
    blocks.push(html);
    listItems = [];
  }

  function pushBlock(html) {
    flushList();
    blocks.push(`<div class="assistant-block">${html}</div>`);
  }

  lines.forEach(rawLine => {
    const trimmed = rawLine.trim();
    if (!trimmed) {
      pushCurrentItem();
      return;
    }

    const substep = rawLine.match(/^\s+(\d+)\.\s+(.*)$/);
    if (substep && currentItem) {
      currentItem.substeps.push({ num: `${substep[1]}.`, text: substep[2] });
      return;
    }

    const topLevelItem = rawLine.match(/^(\d+)\.\s+(.*)$/);
    if (topLevelItem) {
      pushCurrentItem();
      currentItem = { title: topLevelItem[2], lines: [], substeps: [], source: '' };
      return;
    }

    const fieldMatch = trimmed.match(/^(Possible issue|Affected component|Source):\s*(.*)$/i);
    if (fieldMatch) {
      const label = fieldMatch[1];
      const value = fieldMatch[2];
      if (/^source$/i.test(label) && currentItem) {
        currentItem.source = value;
      } else {
        pushBlock(`<span class="assistant-label">${escapeHtml(label)}:</span> ${formatInline(value)}`);
      }
      return;
    }

    if (/^Recommended corrective actions:?$/i.test(trimmed)) {
      pushBlock(`<div class="assistant-section-title">${escapeHtml(trimmed)}</div>`);
      return;
    }

    if (currentItem) {
      currentItem.lines.push(trimmed);
      return;
    }

    pushBlock(formatInline(trimmed));
  });

  flushList();
  return `<div class="assistant-content">${blocks.join('')}</div>`;
}

function pluralize(count, singular, pluralForm) {
  const plural = pluralForm || `${singular}s`;
  return count === 1 ? singular : plural;
}

function formatOutcomeDate(value) {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  return parsed.toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' });
}

function describeActionHistory(stats) {
  if (!stats || !stats.total_uses) {
    return { label: 'New path', className: 'is-new' };
  }
  if (stats.resolved_count > 0) {
    return {
      label: `Resolved ${stats.resolved_count} ${pluralize(stats.resolved_count, 'time')} before`,
      className: 'is-success',
    };
  }
  return {
    label: `Seen ${stats.total_uses} ${pluralize(stats.total_uses, 'time')}`,
    className: 'is-known',
  };
}

function buildPathStatsHtml(stats) {
  if (!stats || !stats.total_uses) {
    return '<div class="issue-history-empty">No recorded outcome yet for this exact symptom → cause → action path.</div>';
  }

  const metricBits = [
    `<span class="issue-metric">Seen ${stats.total_uses}x</span>`,
    `<span class="issue-metric issue-metric-success">${stats.resolved_count} resolved</span>`,
    `<span class="issue-metric">${Math.round(stats.success_rate_pct)}% success</span>`,
  ];

  if (stats.not_resolved_count > 0) {
    metricBits.push(`<span class="issue-metric issue-metric-risk">${stats.not_resolved_count} not solved</span>`);
  }
  if (stats.partially_resolved_count > 0) {
    metricBits.push(`<span class="issue-metric">${stats.partially_resolved_count} partial</span>`);
  }
  if (stats.escalated_count > 0) {
    metricBits.push(`<span class="issue-metric">${stats.escalated_count} escalated</span>`);
  }
  if (stats.avg_duration_min > 0) {
    metricBits.push(`<span class="issue-metric">Avg ${stats.avg_duration_min.toFixed(1)} min</span>`);
  }
  const lastOutcome = formatOutcomeDate(stats.last_outcome_at);
  if (lastOutcome) {
    metricBits.push(`<span class="issue-metric">Last ${escapeHtml(lastOutcome)}</span>`);
  }

  return `<div class="issue-history-metrics">${metricBits.join('')}</div>`;
}

function buildCurrentIssueHtml(currentIssue, extra) {
  if (!currentIssue) return '';

  const actions = Array.isArray(currentIssue.action_options) ? currentIssue.action_options : [];
  const issueCounter = (extra && extra.issue_number && extra.total_issues)
    ? `<div class="issue-counter">Possible cause ${extra.issue_number} of ${extra.total_issues}</div>`
    : '';
  const subtitle = actions.length > 1
    ? 'Mark the action that solved it. History below is for the exact path already seen in past sessions.'
    : 'Confirm whether this corrective path solved it. History below refers to this exact path.';

  let html = '<div class="issue-resolution-panel" data-issue-state="active">';
  html += issueCounter;
  html += '<div class="issue-resolution-title">Did this fix the problem?</div>';
  html += `<div class="issue-resolution-subtitle">${escapeHtml(subtitle)}</div>`;

  if (actions.length > 0) {
    html += '<div class="issue-action-list">';
    actions.forEach((action, index) => {
      const badge = describeActionHistory(action.stats);
      const actionTitle = escapeHtml(action.action_name || `Action ${index + 1}`);
      const badgeClass = escapeHtml(badge.className);
      const badgeLabel = escapeHtml(badge.label);
      const actionId = escapeHtml(action.action_id || '');
      const actionName = escapeHtml(action.action_name || '');
      const buttonLabel = actions.length > 1 ? 'Resolved with this action' : 'Resolved';
      const knownClass = action.stats && action.stats.total_uses > 0 ? 'is-known' : 'is-new';

      html += `<div class="issue-action-card ${knownClass}" data-action-id="${actionId}">`;
      html += '<div class="issue-action-header">';
      html += `<div class="issue-action-title">${actionTitle}</div>`;
      html += `<span class="issue-action-state ${badgeClass}">${badgeLabel}</span>`;
      html += '</div>';
      html += `<div class="issue-action-history">${buildPathStatsHtml(action.stats)}</div>`;
      html += '<div class="issue-action-controls">';
      html += `<button class="resolved-btn" data-action-id="${actionId}" data-action-name="${actionName}">${buttonLabel}</button>`;
      html += '</div>';
      html += '</div>';
    });
    html += '</div>';
  }

  if (extra && extra.has_more_issues) {
    const autoLogActionId = actions.length === 1 ? escapeHtml(actions[0].action_id || '') : '';
    const autoLogAttr = autoLogActionId ? ` data-auto-log-action-id="${autoLogActionId}"` : '';
    html += '<div class="issue-actions-footer">';
    html += `<button class="not-solved-btn"${autoLogAttr}>Next cause</button>`;
    html += '</div>';
  }

  html += '<div class="issue-panel-status" aria-live="polite"></div>';
  html += '</div>';
  return html;
}

function buildClarificationHtml(extra) {
  if (!extra || !extra.awaiting_clarification) return '';
  const options = Array.isArray(extra.clarification_options) ? extra.clarification_options : [];
  if (!options.length) return '';

  let html = '<div class="clarification-panel">';
  html += '<div class="clarification-title">Quick clarification</div>';
  html += '<div class="clarification-subtitle">Choose the closest description, or type your own short answer.</div>';
  html += '<div class="clarification-options">';
  options.forEach(option => {
    const optionId = escapeHtml(option.id || '');
    const label = escapeHtml(option.label || '');
    const description = escapeHtml(option.description || '');
    html += `<button class="clarification-option-btn" data-option-id="${optionId}" data-option-label="${label}">`;
    html += `<span class="clarification-option-index">${optionId}</span>`;
    html += '<span class="clarification-option-text">';
    html += `<span class="clarification-option-label">${label}</span>`;
    if (description) {
      html += `<span class="clarification-option-description">${description}</span>`;
    }
    html += '</span>';
    html += '</button>';
  });
  html += '</div>';
  html += '</div>';
  return html;
}

function updateIssuePanelStatus(panel, text) {
  if (!panel) return;
  const statusEl = panel.querySelector('.issue-panel-status');
  if (!statusEl) return;
  statusEl.textContent = text || '';
}

function setIssuePanelButtonsDisabled(panel, disabled, exceptButton = null) {
  if (!panel) return;
  panel.querySelectorAll('.resolved-btn, .not-solved-btn').forEach(btn => {
    if (exceptButton && btn === exceptButton) return;
    btn.disabled = disabled;
  });
}

function setIssuePanelState(panel, state, statusText) {
  if (!panel) return;
  panel.dataset.issueState = state;
  const disableButtons = state !== 'active';
  setIssuePanelButtonsDisabled(panel, disableButtons);
  panel.classList.toggle('is-inactive', state === 'inactive');
  panel.classList.toggle('is-completed', state === 'completed');
  updateIssuePanelStatus(panel, statusText);
}

function deactivateActiveIssuePanels(statusText) {
  messagesEl.querySelectorAll('.issue-resolution-panel[data-issue-state="active"]').forEach(panel => {
    setIssuePanelState(panel, 'inactive', statusText);
  });
}

function setBusyButton(button, busyText) {
  if (!button) return;
  if (!button.dataset.originalText) {
    button.dataset.originalText = button.textContent;
  }
  button.disabled = true;
  button.textContent = busyText;
}

function restoreBusyButton(button) {
  if (!button) return;
  if (button.dataset.originalText) {
    button.textContent = button.dataset.originalText;
  }
  button.disabled = false;
}

async function logOutcome(selectedActionId, outcome) {
  return fetchJson(instanceApi('/log-outcome'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId,
      selected_action_id: selectedActionId,
      outcome,
    }),
  });
}

function appendMessage(role, text, extra) {
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  let html = '';
  if (role === 'assistant') {
    html = buildAssistantHtml(text);
  } else {
    div.textContent = text;
    html = div.textContent.replace(/\n/g, '<br>');
  }
  const hasCurrentIssue = role === 'assistant' && extra && extra.current_issue;
  const hasClarification = role === 'assistant' && extra && extra.awaiting_clarification;
  if (hasCurrentIssue) {
    html += buildCurrentIssueHtml(extra.current_issue, extra);
  } else if (hasClarification) {
    html += buildClarificationHtml(extra);
  } else if (extra && extra.has_more_issues) {
    // Fallback for assistant responses without structured issue payload.
    html += `<div class="issue-counter">Possible cause ${extra.issue_number} of ${extra.total_issues}</div>`;
    html += `<button class="not-solved-btn">Next cause</button>`;
  } else if (extra && extra.issue_number && extra.total_issues) {
    html += `<div class="issue-counter">Possible cause ${extra.issue_number} of ${extra.total_issues}</div>`;
  }
  // Reasoning trace
  if (extra && extra.reasoning && extra.reasoning.length > 0) {
    let traceHtml = '<details class="reasoning-trace"><summary>Show reasoning path</summary><div class="trace-content">';
    extra.reasoning.forEach(r => {
      const scorePart = r.score ? ` <span class="trace-score">(${Math.round(r.score * 100)}%)</span>` : '';
      const compPart = r.component ? ` <span class="trace-arrow">[</span>${r.component}<span class="trace-arrow">]</span>` : '';
      traceHtml += `<div class="trace-path">`
        + `${r.symptom}${scorePart} <span class="trace-arrow">\u2192</span> ${r.failure_mode}${compPart} <span class="trace-arrow">\u2192</span> ${r.action}`
        + (r.source ? `<br><span style="color:#6e7681">Source: ${r.source}</span>` : '')
        + `</div>`;
    });
    traceHtml += `<div style="margin-top:6px;color:#6e7681">All content retrieved from knowledge graph \u2014 0 LLM-generated facts</div>`;
    traceHtml += '</div></details>';
    html += traceHtml;
  }
  // Confidence bar
  if (extra && extra.scores && Object.keys(extra.scores).length > 0) {
    const best = Math.max(...Object.values(extra.scores));
    const pct = Math.round(best * 100);
    const color = pct >= 75 ? '#3fb950' : pct >= 55 ? '#d29922' : '#f85149';
    html += `<div class="confidence-bar-container"><div class="label">Match confidence: ${pct}%</div>`
          + `<div class="confidence-bar"><div class="confidence-bar-fill" data-width="${pct}" style="background:${color};"></div></div></div>`;
  }
  div.innerHTML = html;
  // Animate confidence bar fill after render
  const fill = div.querySelector('.confidence-bar-fill');
  if (fill) requestAnimationFrame(() => { fill.style.width = fill.dataset.width + '%'; });
  messagesEl.appendChild(div);
  scrollToBottom();
  return div;
}

const FIXER_SVG = `
<svg class="fixer-thinking-svg" viewBox="0 0 36 40" xmlns="http://www.w3.org/2000/svg" width="36" height="40">
  <!-- Hard hat -->
  <ellipse cx="18" cy="12" rx="10" ry="3.5" fill="#f0883e"/>
  <path d="M8 12 Q8 7 18 7 Q28 7 28 12Z" fill="#f0883e"/>
  <rect x="7" y="11.5" width="22" height="2.5" rx="1.2" fill="#d4721c"/>
  <!-- Face -->
  <circle cx="18" cy="18" r="6" fill="#ffd9b3"/>
  <!-- Eyes (thinking — looking up-left) -->
  <circle cx="15.5" cy="16.5" r="1" fill="#333"/>
  <circle cx="20.5" cy="16.5" r="1" fill="#333"/>
  <circle cx="15" cy="16" r="0.4" fill="#fff"/>
  <circle cx="20" cy="16" r="0.4" fill="#fff"/>
  <!-- Slight frown / concentration -->
  <path d="M15.5 20.5 Q18 19.5 20.5 20.5" stroke="#c07a5a" stroke-width="0.8" fill="none" stroke-linecap="round"/>
  <!-- Raised eyebrow left -->
  <path d="M14 14.8 Q15.5 14 17 14.8" stroke="#8b5e3c" stroke-width="0.7" fill="none" stroke-linecap="round"/>
  <!-- Body / coverall -->
  <rect x="11" y="24" width="14" height="12" rx="4" fill="#1f6feb"/>
  <!-- Collar -->
  <rect x="15.5" y="23.5" width="5" height="2" rx="1" fill="#f0883e"/>
  <!-- Left arm -->
  <rect x="5" y="24" width="7" height="2.5" rx="1.2" fill="#1f6feb" transform="rotate(15 8.5 25.25)"/>
  <!-- Right arm holding wrench -->
  <rect x="24" y="24" width="7" height="2.5" rx="1.2" fill="#1f6feb" transform="rotate(-20 27.5 25.25)"/>
  <!-- Wrench head -->
  <circle cx="31" cy="22" r="2" fill="#8b949e" stroke="#484f58" stroke-width="0.5"/>
  <rect cx="31" cy="22" x="30" y="21" width="1" height="4" fill="#8b949e"/>
</svg>`;

let _typingTimerId = null;

function showTyping() {
  const div = document.createElement('div');
  div.className = 'msg typing';
  div.id = 'typing-indicator';

  const wrap = document.createElement('div');
  wrap.className = 'fixer-thinking-wrap';
  wrap.innerHTML = FIXER_SVG
    + `<div class="fixer-thought-bubble">
         <div class="fixer-dot"></div>
         <div class="fixer-dot"></div>
         <div class="fixer-dot"></div>
       </div>`;

  const thinkingLabels = {
    'gpt-5-nano': 'The Fixer is on it…',
    'gpt-5-mini': 'The Fixer is thinking…',
    'gpt-5.4':    'The Fixer is digging deep…',
    'gpt-5':      'The Fixer is doing a thorough analysis…',
  };
  const label = document.createElement('span');
  label.className = 'fixer-thinking-label';
  label.textContent = thinkingLabels[modelSelect.value] || 'The Fixer is thinking…';

  const timer = document.createElement('span');
  timer.className = 'fixer-thinking-timer';
  timer.textContent = '0s';

  div.appendChild(wrap);
  div.appendChild(label);
  div.appendChild(timer);
  messagesEl.appendChild(div);
  scrollToBottom();

  const start = Date.now();
  _typingTimerId = setInterval(() => {
    const elapsed = Math.floor((Date.now() - start) / 1000);
    timer.textContent = elapsed + 's';
  }, 1000);
}

function removeTyping() {
  if (_typingTimerId) { clearInterval(_typingTimerId); _typingTimerId = null; }
  const el = document.getElementById('typing-indicator');
  if (el) el.remove();
}

async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text) return;

  hideChips();
  inputEl.value = '';
  inputEl.style.height = 'auto';
  sendBtn.disabled = true;
  appendMessage('user', text);
  showTyping();

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 45000);
    let data;
    try {
      data = await fetchJson(instanceApi('/chat'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, session_id: sessionId, model: modelSelect.value }),
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeoutId);
    }
    sessionId = data.session_id;
    removeTyping();
    deactivateActiveIssuePanels('Superseded by a newer diagnosis.');
    if (data.reply) {
      appendMessage('assistant', data.reply, {
        has_more_issues: data.has_more_issues,
        issue_number: data.issue_number,
        total_issues: data.total_issues,
        scores: data.highlight ? data.highlight.scores : null,
        reasoning: data.highlight ? data.highlight.reasoning : null,
        current_issue: data.current_issue || null,
        awaiting_clarification: !!data.awaiting_clarification,
        clarification_options: data.clarification_options || [],
      });
    } else {
      appendMessage('assistant', 'Error: ' + (data.error || 'unknown'));
    }
    // Highlight the graph
    if (data.highlight && Object.keys(data.highlight).length > 0) {
      highlightGraph(data.highlight);
      updateSessionStats(data.highlight);
    }
    // Show telemetry panel
    if (data.telemetry) {
      showTelemetryPanel(data.telemetry);
    } else {
      hideTelemetryPanel();
    }
  } catch (err) {
    removeTyping();
    if (err.name === 'AbortError') {
      appendMessage('assistant', 'Request timed out — the AI service may be slow. Please try again.');
    } else {
      appendMessage('assistant', 'Network error. Please check your connection and try again.');
    }
  } finally {
    sendBtn.disabled = false;
    inputEl.focus();
  }
}

function hideChips() {
  const chips = document.getElementById('symptom-chips');
  if (chips) chips.remove();
}

sendBtn.addEventListener('click', sendMessage);
inputEl.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});
inputEl.addEventListener('input', () => {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 100) + 'px';
});

resetBtn.addEventListener('click', async () => {
  if (sessionId) {
    await fetchJson(instanceApi('/reset'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId }),
    });
    sessionId = null;
  }
  renderInitialAssistantState();
  resetGraphHighlight();
  resetSessionStats();
  hideTelemetryPanel();
});

instanceSelect.addEventListener('change', async (e) => {
  if (!e.target.value || e.target.value === activeInstanceId) return;
  await switchInstance(e.target.value);
});

// ── Graph functions ──
const SEVERITY_COLORS = { high: '#f85149', medium: '#d29922', low: '#3fb950' };

function enrichNode(n, cMap) {
  const c = cMap[n.group] || "#4b5563";
  const sevColor = (n.group === 'Symptom' && n.severity) ? SEVERITY_COLORS[n.severity] || c : c;
  const bw = (n.group === 'Symptom' && n.severity) ? 2.5 : 1;
  return {
    ...n,
    color: { border: sevColor, background: c, highlight: { border: "#e5e7eb", background: c }, hover: { border: "#e5e7eb", background: c } },
    font: { color: ACTIVE_FONT_COLOR, size: 11, face: "system-ui" },
    shape: "dot",
    size: 10,
    borderWidth: bw,
  };
}

function buildLegend(nodeTypes, cMap) {
  const el = document.getElementById('graph-legend');
  el.innerHTML = '';
  nodeTypes.forEach(t => {
    const item = document.createElement('div');
    item.className = 'legend-item';
    item.innerHTML = `<span class="legend-dot" style="background:${cMap[t]}"></span>${t}`;
    el.appendChild(item);
  });
}

async function initGraph() {
  let payload;
  document.getElementById('graph-status').textContent = 'Loading graph…';
  nodeMetadata = {};
  originalNodes = {};
  originalEdges = {};
  if (network) {
    network.destroy();
    network = null;
  }
  try {
    payload = await fetchJson(instanceApi('/graph-data'));
  } catch (err) {
    document.getElementById('graph-status').textContent = 'Failed to load graph';
    return;
  }

  const nodeTypes = payload.node_types || [];
  const edgeTypes = payload.edge_types || [];
  colorByType = payload.color_map || {};

  const visNodes = (payload.nodes || []).map(n => enrichNode(n, colorByType));
  const visEdges = (payload.edges || []).map(e => ({
    ...e,
    color: { color: "#21262d", highlight: "#58a6ff", hover: "#30363d" },
    font: { size: 0, color: "transparent" },
    arrows: { to: { enabled: true, scaleFactor: 0.5 } },
    width: 1,
    smooth: { enabled: true, type: "continuous", roundness: 0.15 },
  }));

  nodesDataSet = new vis.DataSet(visNodes);
  edgesDataSet = new vis.DataSet(visEdges);

  // Save originals for reset
  visNodes.forEach(n => { originalNodes[n.id] = { label: n.label, color: { ...n.color }, font: { ...n.font }, size: n.size, borderWidth: n.borderWidth }; });
  visEdges.forEach(e => { originalEdges[e.id] = { color: e.color, width: e.width, font: e.font }; });
  // Save metadata for click interactions
  (payload.nodes || []).forEach(n => { nodeMetadata[n.id] = { group: n.group, description: n.description || '', label: n.label, severity: n.severity || '' }; });
  sessionStats.totalNodes = visNodes.length;

  const container = document.getElementById('network');
  network = new vis.Network(container, { nodes: nodesDataSet, edges: edgesDataSet }, {
    autoResize: true,
    physics: {
      enabled: true,
      stabilization: { iterations: 250, fit: true },
      barnesHut: { gravitationalConstant: -2500, springLength: 120, springConstant: 0.04, damping: 0.15 },
    },
    interaction: { hover: true, tooltipDelay: 100, hideEdgesOnDrag: true, keyboard: false, zoomView: true, dragView: true },
    edges: { smooth: { enabled: true, type: "continuous", roundness: 0.15 } },
  });

  buildLegend(nodeTypes, colorByType);
  attachNodeClickHandler();

  network.once('stabilizationIterationsDone', () => {
    network.fit({ animation: { duration: 500, easingFunction: "easeInOutQuad" } });
    document.getElementById('graph-status').textContent = '';
  });
}

// ── Node click interaction ──
let activePopover = null;
function removePopover() {
  if (activePopover) { activePopover.remove(); activePopover = null; }
}

function showNodePopover(nodeId, domPosition) {
  removePopover();
  const meta = nodeMetadata[nodeId];
  if (!meta) return;

  const pop = document.createElement('div');
  pop.className = 'node-popover';
  pop.innerHTML = `<div class="pop-type">${meta.group}</div>`
    + `<div class="pop-name">${meta.label}</div>`
    + (meta.description ? `<div class="pop-desc">${meta.description}</div>` : '')
    + (meta.group === 'Symptom' ? '<div class="pop-hint">Click to search this symptom</div>' : '');

  const graphPanel = document.querySelector('.graph-panel');
  graphPanel.appendChild(pop);
  // Position relative to graph panel
  const rect = graphPanel.getBoundingClientRect();
  pop.style.left = Math.min(domPosition.x - rect.left, rect.width - 310) + 'px';
  pop.style.top = (domPosition.y - rect.top + 12) + 'px';
  activePopover = pop;
}

// Attach click handler once graph is ready
function attachNodeClickHandler() {
  if (!network) return;
  network.on('click', (params) => {
    if (params.nodes.length === 0) { removePopover(); return; }
    const nodeId = params.nodes[0];
    const meta = nodeMetadata[nodeId];
    if (!meta) return;

    const domPos = params.pointer.DOM;

    if (meta.group === 'Symptom') {
      // Populate chat input and send
      removePopover();
      inputEl.value = meta.label;
      sendMessage();
    } else {
      // Show popover for other node types
      showNodePopover(nodeId, { x: params.event.center.x, y: params.event.center.y });
    }
  });
  // Close popover on canvas click (no node)
  network.on('deselectNode', removePopover);
}

// ── Highlight logic ──
function resetGraphHighlight() {
  if (!nodesDataSet || !edgesDataSet) return;

  const nodeUpdates = [];
  nodesDataSet.forEach(n => {
    const orig = originalNodes[n.id];
    if (orig) nodeUpdates.push({ id: n.id, ...orig });
  });
  nodesDataSet.update(nodeUpdates);

  const edgeUpdates = [];
  edgesDataSet.forEach(e => {
    const orig = originalEdges[e.id];
    if (orig) edgeUpdates.push({ id: e.id, ...orig });
  });
  edgesDataSet.update(edgeUpdates);
}

function highlightGraph(trace) {
  if (!nodesDataSet || !edgesDataSet || !network) return;

  // Reset to original state first
  resetGraphHighlight();

  const symptomIds = new Set(trace.symptom_ids || []);
  const errorCodeIds = new Set(trace.error_code_ids || []);
  const fmIds = new Set(trace.failure_mode_ids || []);
  const actionIds = new Set(trace.action_ids || []);
  const componentIds = new Set(trace.component_ids || []);
  const allHighlightIds = new Set([...symptomIds, ...errorCodeIds, ...fmIds, ...actionIds, ...componentIds]);

  const scores = trace.scores || {};

  // Classify edges by stage
  const edgesByStage = { sym2fm: new Set(), fm2act: new Set() };
  (trace.edges || []).forEach(e => {
    const key = e.from + "\u2192" + e.to;
    if ((symptomIds.has(e.from) || errorCodeIds.has(e.from)) && fmIds.has(e.to)) edgesByStage.sym2fm.add(key);
    else edgesByStage.fm2act.add(key);
  });
  const allEdgePairs = new Set([...edgesByStage.sym2fm, ...edgesByStage.fm2act]);

  // Helper: light up a set of nodes
  function lightNodes(ids) {
    const updates = [];
    ids.forEach(id => {
      const n = nodesDataSet.get(id);
      if (!n) return;
      const typeColor = colorByType[n.group] || "#58a6ff";
      let label = originalNodes[id] ? originalNodes[id].label : n.label;
      if (scores[id] !== undefined) label += `\n(${Math.round(scores[id] * 100)}%)`;
      updates.push({
        id,
        label,
        color: { border: HIGHLIGHT_COLOR, background: typeColor, highlight: { border: "#fff", background: typeColor }, hover: { border: "#fff", background: typeColor } },
        font: { color: ACTIVE_FONT_COLOR, size: 13, face: "system-ui", bold: true, multi: 'md' },
        size: 16,
        borderWidth: 3,
      });
    });
    if (updates.length) nodesDataSet.update(updates);
  }

  // Helper: light up edges matching a set of from→to keys
  function lightEdges(pairSet) {
    const updates = [];
    edgesDataSet.forEach(e => {
      if (pairSet.has(e.from + "\u2192" + e.to)) {
        updates.push({
          id: e.id,
          color: { color: HIGHLIGHT_EDGE_COLOR, highlight: HIGHLIGHT_EDGE_COLOR },
          width: 2.5,
          font: { size: 9, color: "#8b949e", strokeWidth: 0 },
        });
      }
    });
    if (updates.length) edgesDataSet.update(updates);
  }

  // Stage 0: Dim everything
  const dimNodes = [];
  nodesDataSet.forEach(n => {
    dimNodes.push({
      id: n.id,
      color: { border: DIM_NODE_COLOR, background: DIM_NODE_COLOR, highlight: { border: DIM_NODE_COLOR, background: DIM_NODE_COLOR }, hover: { border: "#30363d", background: "#21262d" } },
      font: { color: DIM_FONT_COLOR, size: 9, face: "system-ui" },
      size: 7, borderWidth: 1,
    });
  });
  nodesDataSet.update(dimNodes);

  const dimEdges = [];
  edgesDataSet.forEach(e => {
    dimEdges.push({
      id: e.id,
      color: { color: DIM_EDGE_COLOR, highlight: DIM_EDGE_COLOR },
      width: 0.5,
      font: { size: 0, color: "transparent" },
    });
  });
  edgesDataSet.update(dimEdges);

  // Focus camera on all highlighted nodes immediately
  const fitIds = Array.from(allHighlightIds).filter(id => nodesDataSet.get(id));
  if (fitIds.length > 0) {
    network.fit({
      nodes: fitIds,
      animation: { duration: 800, easingFunction: "easeInOutQuad" },
      maxZoomLevel: 1.5, minZoomLevel: 0.3,
    });
  }

  // Stage 1 (0ms): Light symptoms
  lightNodes(symptomIds);

  // Stage 2 (500ms): Light edges Symptom → FailureMode
  setTimeout(() => lightEdges(edgesByStage.sym2fm), 500);

  // Stage 3 (1000ms): Light FailureMode nodes
  setTimeout(() => lightNodes(fmIds), 1000);

  // Stage 4 (1500ms): Light edges FM → Action/Component
  setTimeout(() => lightEdges(edgesByStage.fm2act), 1500);

  // Stage 5 (2000ms): Light Action + Component nodes
  setTimeout(() => {
    lightNodes(actionIds);
    lightNodes(componentIds);
  }, 2000);
}

// ── Telemetry panel ──
const TELEMETRY_COLORS = [
  { line: '#58a6ff', fill: 'rgba(88,166,255,0.07)' },   // blue
  { line: '#3fb950', fill: 'rgba(63,185,80,0.07)' },    // green
  { line: '#f0883e', fill: 'rgba(240,136,62,0.07)' },   // orange
  { line: '#d2a8ff', fill: 'rgba(210,168,255,0.07)' },  // lavender
  { line: '#ffa657', fill: 'rgba(255,166,87,0.07)' },   // amber
  { line: '#79c0ff', fill: 'rgba(121,192,255,0.07)' },  // sky
  { line: '#56d364', fill: 'rgba(86,211,100,0.07)' },   // lime
  { line: '#ff7b72', fill: 'rgba(255,123,114,0.07)' },  // coral
];
let telemetryCharts = [];

function showTelemetryPanel(telemetryData) {
  if (!telemetryData || !telemetryData.columns || telemetryData.columns.length === 0) {
    hideTelemetryPanel();
    return;
  }

  const panel = document.getElementById('telemetry-panel');
  const content = document.getElementById('telemetry-content');

  // Destroy old charts
  telemetryCharts.forEach(c => c.destroy());
  telemetryCharts = [];
  content.innerHTML = '';

  telemetryData.columns.forEach((col, colIdx) => {
    const signalData = telemetryData.signals[col];
    const stats = telemetryData.stats[col];
    if (!signalData || signalData.length === 0 || !stats) return;

    // Card
    const card = document.createElement('div');
    card.className = 'telemetry-card';

    // Title
    const color = TELEMETRY_COLORS[colIdx % TELEMETRY_COLORS.length];
    const title = document.createElement('div');
    title.className = 'telemetry-card-title';
    title.style.color = color.line;
    title.textContent = col.replace(/_/g, ' ');
    card.appendChild(title);

    // Chart canvas
    const chartWrap = document.createElement('div');
    chartWrap.className = 'telemetry-chart-wrap';
    const canvas = document.createElement('canvas');
    chartWrap.appendChild(canvas);
    card.appendChild(chartWrap);

    // Downsample labels for readability (show ~8 tick labels)
    const labels = signalData.map(d => {
      const dt = new Date(d.t);
      return dt.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })
        + ' ' + dt.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    });
    const values = signalData.map(d => d.v);

    const chart = new Chart(canvas, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [{
          data: values,
          borderColor: color.line,
          backgroundColor: color.fill,
          fill: true,
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHoverBackgroundColor: color.line,
          pointHoverBorderColor: '#f0f6fc',
          pointHoverBorderWidth: 2,
          borderWidth: 1.5,
          tension: 0.3,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: 'index',
          intersect: false,
        },
        hover: {
          mode: 'index',
          intersect: false,
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            enabled: true,
            backgroundColor: '#1c2128',
            borderColor: '#30363d',
            borderWidth: 1,
            titleColor: '#8b949e',
            bodyColor: '#e5e7eb',
            bodyFont: { size: 12, weight: '600' },
            titleFont: { size: 10 },
            padding: 8,
            displayColors: false,
            callbacks: {
              label: function(ctx) { return ctx.parsed.y.toFixed(4); }
            }
          },
        },
        scales: {
          x: {
            ticks: {
              display: true,
              color: '#484f58',
              font: { size: 9 },
              maxTicksLimit: 8,
              maxRotation: 0,
            },
            grid: { color: '#21262d' },
          },
          y: {
            ticks: { color: '#484f58', font: { size: 10 } },
            grid: { color: '#21262d' },
          }
        }
      },
      plugins: [{
        id: 'crosshair',
        afterDraw(chart) {
          if (chart.tooltip && chart.tooltip.opacity > 0) {
            const ctx = chart.ctx;
            const x = chart.tooltip.caretX;
            const top = chart.scales.y.top;
            const bottom = chart.scales.y.bottom;
            ctx.save();
            ctx.beginPath();
            ctx.setLineDash([3, 3]);
            ctx.lineWidth = 1;
            ctx.strokeStyle = '#484f58';
            ctx.moveTo(x, top);
            ctx.lineTo(x, bottom);
            ctx.stroke();
            ctx.restore();
          }
        }
      }]
    });
    telemetryCharts.push(chart);

    // Stats row
    const trendArrows = { rising: '\u25B2', falling: '\u25BC', stable: '\u25B6' };
    const trendClass = 'telemetry-trend-' + (stats.trend || 'stable');
    const statsRow = document.createElement('div');
    statsRow.className = 'telemetry-stats-row';
    statsRow.innerHTML =
        `<div class="telemetry-stat"><span class="tstat-label">Mean:</span><span class="tstat-value">${stats.mean}</span></div>`
      + `<div class="telemetry-stat"><span class="tstat-label">Std:</span><span class="tstat-value">${stats.std}</span></div>`
      + `<div class="telemetry-stat"><span class="tstat-label">Min:</span><span class="tstat-value">${stats.min}</span></div>`
      + `<div class="telemetry-stat"><span class="tstat-label">Max:</span><span class="tstat-value">${stats.max}</span></div>`
      + `<div class="telemetry-stat"><span class="tstat-label">Last:</span><span class="tstat-value">${stats.last}</span></div>`
      + `<div class="telemetry-stat"><span class="tstat-label">Trend:</span><span class="tstat-value ${trendClass}">${trendArrows[stats.trend] || '?'} ${stats.trend || 'N/A'}</span></div>`;
    card.appendChild(statsRow);

    content.appendChild(card);
  });

  panel.classList.add('visible', 'collapsed');
}

function hideTelemetryPanel() {
  const panel = document.getElementById('telemetry-panel');
  panel.classList.remove('visible');
  telemetryCharts.forEach(c => c.destroy());
  telemetryCharts = [];
}

// Toggle collapse
document.getElementById('telemetry-header').addEventListener('click', () => {
  document.getElementById('telemetry-panel').classList.toggle('collapsed');
});

// ── PDF overlay ──
const pdfOverlay = document.getElementById('pdf-overlay');
const pdfIframe = document.getElementById('pdf-iframe');
const pdfTitle = document.getElementById('pdf-title');
const pdfPageLabel = document.getElementById('pdf-page-label');
const pdfCloseBtn = document.getElementById('pdf-close-btn');

function openManual(title, page) {
  const encodedTitle = encodeURIComponent(title + '.pdf');
  pdfIframe.src = `/manuals/${encodedTitle}#page=${page}`;
  pdfTitle.textContent = title;
  pdfPageLabel.textContent = `Page ${page}`;
  pdfOverlay.classList.add('visible');
}

function closeManual() {
  pdfOverlay.classList.remove('visible');
  pdfIframe.src = '';
}

pdfCloseBtn.addEventListener('click', closeManual);

// Event delegation for manual buttons and "not solved" buttons in chat messages
messagesEl.addEventListener('click', async (e) => {
  const manualBtn = e.target.closest('.manual-btn');
  if (manualBtn) {
    const title = manualBtn.dataset.title;
    const page = manualBtn.dataset.page;
    if (title && page) openManual(title, parseInt(page));
    return;
  }

  const clarificationBtn = e.target.closest('.clarification-option-btn');
  if (clarificationBtn && !clarificationBtn.disabled) {
    inputEl.value = clarificationBtn.dataset.optionId || clarificationBtn.dataset.optionLabel || '';
    inputEl.dispatchEvent(new Event('input'));
    await sendMessage();
    return;
  }

  const notSolvedBtn = e.target.closest('.not-solved-btn');
  if (notSolvedBtn && !notSolvedBtn.disabled) {
    const panel = notSolvedBtn.closest('.issue-resolution-panel');
    const autoLogActionId = notSolvedBtn.dataset.autoLogActionId || '';
    setBusyButton(notSolvedBtn, 'Loading next cause…');
    setIssuePanelButtonsDisabled(panel, true, notSolvedBtn);
    updateIssuePanelStatus(panel, autoLogActionId ? 'Saving "not solved" and loading the next cause…' : 'Loading the next cause…');
    showTyping();

    try {
      let autoLogged = false;
      if (autoLogActionId) {
        try {
          await logOutcome(autoLogActionId, 'not_resolved');
          autoLogged = true;
        } catch (logErr) {
          console.warn('Could not auto-log not_resolved outcome before loading next cause:', logErr);
        }
      }

      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 45000);
      let data;
      try {
        data = await fetchJson(instanceApi('/next-issue'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: sessionId, model: modelSelect.value }),
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timeoutId);
      }
      removeTyping();
      if (panel) {
        const statusText = autoLogged
          ? 'Marked as not solved. Showing the next possible cause.'
          : 'Showing the next possible cause.';
        notSolvedBtn.textContent = 'Next cause shown';
        setIssuePanelState(panel, 'inactive', statusText);
      } else if (notSolvedBtn.dataset.originalText) {
        notSolvedBtn.textContent = notSolvedBtn.dataset.originalText;
      }
      if (data.reply) {
        appendMessage('assistant', data.reply, {
          has_more_issues: data.has_more_issues,
          issue_number: data.issue_number,
          total_issues: data.total_issues,
          scores: data.highlight ? data.highlight.scores : null,
          reasoning: data.highlight ? data.highlight.reasoning : null,
          current_issue: data.current_issue || null,
          awaiting_clarification: !!data.awaiting_clarification,
          clarification_options: data.clarification_options || [],
        });
      }
      if (data.highlight && Object.keys(data.highlight).length > 0) {
        highlightGraph(data.highlight);
        updateSessionStats(data.highlight);
      }
      // Show telemetry panel
      if (data.telemetry) {
        showTelemetryPanel(data.telemetry);
      } else {
        hideTelemetryPanel();
      }
    } catch (err) {
      removeTyping();
      restoreBusyButton(notSolvedBtn);
      setIssuePanelButtonsDisabled(panel, false, notSolvedBtn);
      updateIssuePanelStatus(panel, 'Could not load the next cause.');
      if (err.name === 'AbortError') {
        appendMessage('assistant', 'Request timed out — the AI service may be slow. Please try again.');
      } else {
        appendMessage('assistant', 'Error loading next cause. Please try again.');
      }
    }
    return;
  }

  const resolvedBtn = e.target.closest('.resolved-btn');
  if (resolvedBtn && !resolvedBtn.disabled) {
    const panel = resolvedBtn.closest('.issue-resolution-panel');
    if (!panel || panel.dataset.issueState !== 'active') {
      return;
    }

    const actionId = resolvedBtn.dataset.actionId;
    const actionName = resolvedBtn.dataset.actionName || 'this action';
    const actionCard = resolvedBtn.closest('.issue-action-card');
    if (!actionId || !sessionId) {
      updateIssuePanelStatus(panel, 'No active session available for outcome logging.');
      return;
    }

    setBusyButton(resolvedBtn, 'Saving…');
    setIssuePanelButtonsDisabled(panel, true, resolvedBtn);
    updateIssuePanelStatus(panel, `Saving confirmed resolution for ${actionName}…`);

    try {
      const data = await logOutcome(actionId, 'resolved');
      const badge = describeActionHistory(data.stats);

      if (actionCard) {
        actionCard.classList.add('is-selected');
        const stateEl = actionCard.querySelector('.issue-action-state');
        if (stateEl) {
          stateEl.className = `issue-action-state ${badge.className}`;
          stateEl.textContent = badge.label;
        }
        const historyEl = actionCard.querySelector('.issue-action-history');
        if (historyEl) {
          historyEl.innerHTML = buildPathStatsHtml(data.stats);
        }
      }

      setIssuePanelState(panel, 'completed', `Resolution saved for ${actionName}.`);
      resolvedBtn.textContent = 'Resolved saved';
    } catch (err) {
      restoreBusyButton(resolvedBtn);
      setIssuePanelButtonsDisabled(panel, false, resolvedBtn);
      updateIssuePanelStatus(panel, err.message || 'Could not save the resolution outcome.');
      appendMessage('assistant', 'Could not save the resolution outcome. Please try again.');
    }
  }
});

// ── Resizable panels ──
(function initResizers() {
  const layout = document.querySelector('.layout');
  const resizerV = document.getElementById('resizer-v');
  const resizerH = document.getElementById('resizer-h');

  // Vertical resizer (graph | chat)
  resizerV.addEventListener('mousedown', (e) => {
    e.preventDefault();
    resizerV.classList.add('active');
    const startX = e.clientX;
    const layoutRect = layout.getBoundingClientRect();
    const startChatW = document.querySelector('.chat-panel').getBoundingClientRect().width;

    function onMove(ev) {
      const dx = ev.clientX - startX;
      const newChatW = Math.max(280, Math.min(startChatW - dx, layoutRect.width - 200));
      layout.style.gridTemplateColumns = `1fr 4px ${newChatW}px`;
    }
    function onUp() {
      resizerV.classList.remove('active');
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      // Resize charts after drag
      telemetryCharts.forEach(c => c.resize());
    }
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });

  // Horizontal resizer (main row | telemetry)
  resizerH.addEventListener('mousedown', (e) => {
    e.preventDefault();
    resizerH.classList.add('active');
    const startY = e.clientY;
    const layoutRect = layout.getBoundingClientRect();
    const telPanel = document.getElementById('telemetry-panel');
    const startTelH = telPanel.getBoundingClientRect().height;

    function onMove(ev) {
      const dy = ev.clientY - startY;
      const newTelH = Math.max(36, Math.min(startTelH - dy, layoutRect.height - 150));
      telPanel.style.maxHeight = newTelH + 'px';
      layout.style.gridTemplateRows = `48px 1fr 4px ${newTelH}px`;
    }
    function onUp() {
      resizerH.classList.remove('active');
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      telemetryCharts.forEach(c => c.resize());
    }
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });
})();

// ── Init ──
window.addEventListener('load', async () => {
  try {
    const instances = await loadInstances();
    const initialInstanceId = pickInitialInstanceId(instances);
    if (!initialInstanceId) {
      document.getElementById('graph-status').textContent = 'No instances available';
      messagesEl.innerHTML = '';
      appendMessage('assistant', 'No knowledge graph instances are available yet.');
      sendBtn.disabled = true;
      return;
    }
    await switchInstance(initialInstanceId);
  } catch (err) {
    console.error('Failed to bootstrap dev UI:', err);
    document.getElementById('graph-status').textContent = 'Failed to load instances';
    messagesEl.innerHTML = '';
    appendMessage('assistant', 'Could not load instances from the Knowledge Agents API.');
    sendBtn.disabled = true;
    return;
  }
  inputEl.focus();
});
