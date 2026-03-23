HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>{{ ontology_name }} - Graph Editor</title>
    <script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
    <style>
      *, *::before, *::after { box-sizing: border-box; }
      html, body {
        margin: 0;
        padding: 0;
        height: 100%;
        font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #111827;
        color: #e5e7eb;
      }
      .layout {
        display: grid;
        grid-template-columns: 260px 1fr;
        grid-template-rows: 48px 1fr;
        grid-template-areas:
          "header header"
          "sidebar main";
        height: 100vh;
        transition: grid-template-columns 0.25s ease;
      }
      .layout.edit-mode {
        grid-template-columns: 260px 1fr 360px;
        grid-template-areas:
          "header header header"
          "sidebar main editpanel";
      }
      header {
        grid-area: header;
        padding: 0 20px;
        background: #1f2937;
        border-bottom: 1px solid #374151;
        display: flex;
        align-items: center;
        justify-content: space-between;
      }
      .header-left { display: flex; align-items: center; gap: 14px; }
      header h1 {
        margin: 0;
        font-size: 15px;
        font-weight: 600;
        letter-spacing: 0.03em;
        color: #f9fafb;
      }
      header span.sub {
        font-size: 11px;
        color: #9ca3af;
      }
      .header-right { display: flex; align-items: center; gap: 12px; }
      /* Toggle switch */
      .mode-switch {
        display: flex; align-items: center; gap: 6px;
        font-size: 11px; color: #9ca3af; cursor: pointer; user-select: none;
      }
      .mode-switch .sw {
        width: 34px; height: 18px; border-radius: 9px;
        background: #374151; position: relative; transition: background 0.2s;
      }
      .mode-switch .sw::after {
        content: ""; position: absolute; top: 2px; left: 2px;
        width: 14px; height: 14px; border-radius: 50%;
        background: #9ca3af; transition: transform 0.2s, background 0.2s;
      }
      .mode-switch.active .sw { background: #f59e0b; }
      .mode-switch.active .sw::after { transform: translateX(16px); background: #fff; }
      .sidebar {
        grid-area: sidebar;
        padding: 12px 14px;
        border-right: 1px solid #374151;
        background: #1f2937;
        overflow-y: auto;
      }
      .sidebar h2 {
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin: 12px 0 6px;
        color: #6b7280;
      }
      .sidebar h2:first-child { margin-top: 0; }
      .chips {
        display: flex;
        flex-wrap: wrap;
        gap: 5px;
      }
      .chip {
        border-radius: 4px;
        border: 1px solid #374151;
        padding: 3px 8px;
        font-size: 11px;
        cursor: pointer;
        user-select: none;
        background: #111827;
        color: #9ca3af;
        transition: background 0.1s, color 0.1s, border-color 0.1s;
        display: flex;
        align-items: center;
        gap: 5px;
      }
      .chip .dot {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        flex-shrink: 0;
      }
      .chip.active {
        background: #374151;
        border-color: #4b5563;
        color: #e5e7eb;
      }
      .chip:hover { border-color: #6b7280; }
      .toggles {
        display: flex;
        flex-direction: column;
        gap: 6px;
        margin-top: 4px;
      }
      .toggle {
        font-size: 11px;
        display: flex;
        align-items: center;
        gap: 6px;
        cursor: pointer;
        color: #9ca3af;
      }
      .toggle input { accent-color: #6b7280; }
      #network-container {
        grid-area: main;
        position: relative;
        background: #0d1117;
        overflow: hidden;
      }
      #network {
        position: absolute;
        top: 0; left: 0; right: 0; bottom: 0;
        width: 100%;
        height: 100%;
      }
      .hint {
        position: absolute;
        bottom: 10px;
        left: 12px;
        font-size: 10px;
        color: #6b7280;
      }
      .status-pill {
        position: absolute;
        top: 10px;
        right: 12px;
        font-size: 11px;
        padding: 4px 10px;
        border-radius: 4px;
        border: 1px solid #374151;
        background: #1f2937;
        color: #9ca3af;
      }
      #error-box {
        display: none;
        position: absolute;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        background: #1f2937;
        border: 1px solid #374151;
        border-radius: 6px;
        padding: 20px 28px;
        color: #f87171;
        font-size: 13px;
        text-align: center;
      }

      /* ---- Edit Panel ---- */
      .edit-panel {
        grid-area: editpanel;
        background: #1f2937;
        border-left: 1px solid #374151;
        display: none;
        flex-direction: column;
        overflow: hidden;
      }
      .layout.edit-mode .edit-panel { display: flex; }
      .ep-header {
        padding: 10px 14px;
        border-bottom: 1px solid #374151;
        display: flex; align-items: center; justify-content: space-between;
        flex-shrink: 0;
      }
      .ep-header h2 { margin: 0; font-size: 13px; font-weight: 600; color: #f9fafb; }
      .ep-body {
        flex: 1; overflow-y: auto; padding: 12px 14px;
      }
      .ep-placeholder {
        color: #6b7280; font-size: 12px; text-align: center; margin-top: 40px;
      }
      .ep-section { margin-bottom: 14px; }
      .ep-section-title {
        font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em;
        color: #6b7280; margin-bottom: 6px;
      }
      .ep-badge {
        display: inline-flex; align-items: center; gap: 5px;
        font-size: 11px; padding: 2px 8px; border-radius: 4px;
        background: #374151; color: #e5e7eb; margin-bottom: 4px;
      }
      .ep-badge .dot { width: 7px; height: 7px; border-radius: 50%; }
      .ep-id { font-family: monospace; font-size: 11px; color: #9ca3af; word-break: break-all; }
      .ep-field { margin-bottom: 8px; }
      .ep-field label {
        display: block; font-size: 10px; color: #6b7280;
        margin-bottom: 2px; text-transform: uppercase; letter-spacing: 0.05em;
      }
      .ep-field input, .ep-field textarea, .ep-field select {
        width: 100%; background: #111827; border: 1px solid #374151;
        color: #e5e7eb; border-radius: 4px; padding: 5px 8px; font-size: 12px;
        font-family: inherit; outline: none;
      }
      .ep-field input:focus, .ep-field textarea:focus, .ep-field select:focus {
        border-color: #f59e0b;
      }
      .ep-field textarea { resize: vertical; min-height: 50px; }
      .ep-btn {
        padding: 5px 12px; font-size: 11px; border-radius: 4px; cursor: pointer;
        border: 1px solid #4b5563; background: #374151; color: #e5e7eb;
        transition: background 0.15s;
      }
      .ep-btn:hover { background: #4b5563; }
      .ep-btn-primary { background: #f59e0b; border-color: #f59e0b; color: #111827; font-weight: 600; }
      .ep-btn-primary:hover { background: #d97706; }
      .ep-btn-danger { background: #991b1b; border-color: #991b1b; color: #fca5a5; }
      .ep-btn-danger:hover { background: #7f1d1d; }
      .ep-rel-row {
        display: flex; align-items: center; gap: 6px;
        font-size: 11px; padding: 4px 0; border-bottom: 1px solid #1f2937;
        color: #d1d5db;
      }
      .ep-rel-row .rel-type {
        background: #111827; padding: 1px 6px; border-radius: 3px;
        font-size: 10px; color: #9ca3af; white-space: nowrap;
      }
      .ep-rel-row .rel-node {
        flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        cursor: pointer;
      }
      .ep-rel-row .rel-node:hover { color: #f59e0b; }
      .ep-rel-row .rel-del {
        background: none; border: none; color: #6b7280; cursor: pointer;
        font-size: 13px; padding: 0 3px; line-height: 1;
      }
      .ep-rel-row .rel-del:hover { color: #f87171; }
      .ep-add-rel {
        margin-top: 8px; padding: 8px; background: #111827;
        border: 1px solid #374151; border-radius: 4px;
      }
      .ep-add-rel summary {
        font-size: 11px; color: #9ca3af; cursor: pointer; user-select: none;
      }
      .ep-add-rel summary:hover { color: #e5e7eb; }
      .ep-add-rel-form { margin-top: 8px; display: flex; flex-direction: column; gap: 6px; }
      /* Save bar */
      .ep-save-bar {
        flex-shrink: 0; padding: 10px 14px;
        border-top: 1px solid #374151;
        display: flex; align-items: center; gap: 8px;
      }
      .ep-save-bar .unsaved-dot {
        width: 7px; height: 7px; border-radius: 50%; background: #f59e0b;
        display: none;
      }
      .ep-save-bar .unsaved-dot.visible { display: inline-block; }
      .ep-save-bar .version-label { font-size: 11px; color: #6b7280; flex: 1; }
      .ep-toast {
        position: fixed; bottom: 20px; right: 20px;
        background: #065f46; color: #d1fae5; padding: 8px 16px;
        border-radius: 6px; font-size: 12px; z-index: 9999;
        opacity: 0; transition: opacity 0.3s;
        pointer-events: none;
      }
      .ep-toast.show { opacity: 1; }
    </style>
  </head>
  <body>
    <div class="layout" id="layout">
      <header>
        <div class="header-left">
          <h1>{{ ontology_name }}</h1>
          <span class="sub">Drag · Scroll to zoom · Click chips to filter</span>
        </div>
        <div class="header-right">
          <div class="mode-switch" id="edit-mode-toggle">
            <span>Edit Mode</span>
            <div class="sw"></div>
          </div>
        </div>
      </header>
      <aside class="sidebar">
        <h2>Node Types</h2>
        <div id="node-types" class="chips"></div>
        <h2>Relationship Types</h2>
        <div id="edge-types" class="chips"></div>
        <h2>Options</h2>
        <div class="toggles">
          <label class="toggle">
            <input type="checkbox" id="physics-toggle" checked />
            <span>Physics</span>
          </label>
          <label class="toggle">
            <input type="checkbox" id="smooth-toggle" checked />
            <span>Smooth edges</span>
          </label>
        </div>
        <button id="fit-btn" style="margin-top:10px;width:100%;padding:5px 0;font-size:11px;background:#374151;border:1px solid #4b5563;border-radius:4px;color:#e5e7eb;cursor:pointer;">Fit to screen</button>
      </aside>
      <main id="network-container">
        <div id="network"></div>
        <div id="error-box"></div>
        <div class="hint">Drag nodes · scroll to zoom · click node in edit mode to modify</div>
        <div class="status-pill" id="status-pill">Loading…</div>
      </main>
      <aside class="edit-panel" id="edit-panel">
        <div class="ep-header">
          <h2 id="ep-title">Edit Node</h2>
        </div>
        <div class="ep-body" id="ep-body">
          <div class="ep-placeholder">Click a node in the graph to edit it.</div>
        </div>
        <div class="ep-save-bar">
          <span class="unsaved-dot" id="unsaved-dot"></span>
          <span class="version-label" id="version-label">v?</span>
          <button class="ep-btn ep-btn-primary" id="save-btn">Save Version</button>
        </div>
      </aside>
    </div>
    <div class="ep-toast" id="toast"></div>

    <script>
      const COLOR_PALETTE = [
        "#6b8cba", "#7aab82", "#c47e5a", "#9b79b8",
        "#5fa8a0", "#b87a7a", "#a0a052", "#6b9eb8",
        "#b8906b", "#7a8fb8", "#88a87a"
      ];

      let network = null;
      let nodesDataSet = null;
      let edgesDataSet = null;
      let nodesView = null;
      let edgesView = null;
      let allNodes = [];
      let allEdges = [];
      let activeNodeTypes = new Set();
      let activeEdgeTypes = new Set();
      let colorByType = {};
      let editMode = false;
      let hasUnsavedChanges = false;
      let selectedNodeId = null;
      let schemaData = null;
      let allNodesList = [];

      function pickColor(idx) {
        return COLOR_PALETTE[idx % COLOR_PALETTE.length];
      }

      function toast(msg) {
        const el = document.getElementById("toast");
        el.textContent = msg;
        el.classList.add("show");
        setTimeout(() => el.classList.remove("show"), 2500);
      }

      function updateStatusPill() {
        const nCount = nodesView ? nodesView.get().length : 0;
        const eCount = edgesView ? edgesView.get().length : 0;
        document.getElementById("status-pill").textContent = `${nCount} nodes · ${eCount} edges`;
      }

      function markUnsaved() {
        hasUnsavedChanges = true;
        document.getElementById("unsaved-dot").classList.add("visible");
      }

      function buildNodeChips(nodeTypes) {
        const container = document.getElementById("node-types");
        container.innerHTML = "";
        nodeTypes.forEach((val, idx) => {
          const color = pickColor(idx);
          const chip = document.createElement("div");
          chip.className = "chip active";
          chip.dataset.value = val;
          chip.innerHTML = `<span class="dot" style="background:${color}"></span>${val}`;
          chip.addEventListener("click", () => {
            const isActive = chip.classList.toggle("active");
            if (isActive) activeNodeTypes.add(val); else activeNodeTypes.delete(val);
            applyFilters();
          });
          container.appendChild(chip);
        });
      }

      function buildEdgeChips(edgeTypes) {
        const container = document.getElementById("edge-types");
        container.innerHTML = "";
        edgeTypes.forEach((val) => {
          const chip = document.createElement("div");
          chip.className = "chip active";
          chip.dataset.value = val;
          chip.textContent = val;
          chip.addEventListener("click", () => {
            const isActive = chip.classList.toggle("active");
            if (isActive) activeEdgeTypes.add(val); else activeEdgeTypes.delete(val);
            applyFilters();
          });
          container.appendChild(chip);
        });
      }

      function applyFilters() {
        if (!nodesView || !edgesView) return;
        nodesView.refresh();
        edgesView.refresh();
        updateStatusPill();
      }

      function showError(msg) {
        const box = document.getElementById("error-box");
        box.textContent = msg;
        box.style.display = "block";
        document.getElementById("status-pill").textContent = "Error";
      }

      function enrichNode(n) {
        const c = colorByType[n.group] || "#4b5563";
        return {
          ...n,
          color: {
            border: c, background: c,
            highlight: { border: "#e5e7eb", background: c },
            hover: { border: "#e5e7eb", background: c },
          },
          font: { color: "#f9fafb", size: 12, face: "system-ui" },
          shape: "dot",
          size: 13,
        };
      }

      // ---- Edit panel ----
      async function loadNodeEdit(nodeId) {
        selectedNodeId = nodeId;
        document.getElementById("ep-title").textContent = "Edit Node";
        const body = document.getElementById("ep-body");
        body.innerHTML = '<div class="ep-placeholder">Loading…</div>';

        let data;
        try {
          const res = await fetch(`/node/${encodeURIComponent(nodeId)}`);
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          data = await res.json();
        } catch (err) {
          body.innerHTML = `<div class="ep-placeholder" style="color:#f87171">${err.message}</div>`;
          return;
        }

        // Highlight selected node
        if (nodesDataSet) {
          // Reset all
          nodesDataSet.forEach(n => {
            nodesDataSet.update({ id: n.id, borderWidth: 1 });
          });
          nodesDataSet.update({ id: nodeId, borderWidth: 3 });
        }

        renderEditPanel(data);
      }

      function renderEditPanel(data) {
        const body = document.getElementById("ep-body");
        const color = colorByType[data.type] || "#4b5563";
        const idKey = Object.keys(data.attributes).find(k => k.endsWith("_id")) || "";

        let html = `<div class="ep-section">
          <div class="ep-badge"><span class="dot" style="background:${color}"></span>${data.type}</div>
          <div class="ep-id">${data.id}</div>
        </div>`;

        // Attributes
        html += `<div class="ep-section"><div class="ep-section-title">Attributes</div>`;
        const editableKeys = Object.keys(data.attributes).filter(k => k !== idKey);
        for (const key of editableKeys) {
          const val = data.attributes[key] || "";
          html += `<div class="ep-field">
            <label>${key}</label>
            <input type="text" data-attr-key="${key}" value="${escHtml(String(val))}" />
          </div>`;
        }
        html += `<button class="ep-btn ep-btn-primary" id="apply-attrs-btn" style="margin-top:4px">Apply</button></div>`;

        // Outgoing relationships
        html += `<div class="ep-section"><div class="ep-section-title">Outgoing Relationships (${data.relationships_out.length})</div>`;
        if (data.relationships_out.length === 0) {
          html += `<div style="font-size:11px;color:#6b7280">None</div>`;
        }
        for (const r of data.relationships_out) {
          html += `<div class="ep-rel-row">
            <span class="rel-type">${escHtml(r.type)}</span>
            <span class="rel-node" data-nav-node="${escHtml(r.to_id)}" title="${escHtml(r.to_id)}">${escHtml(r.to_label)}</span>
            <button class="rel-del" data-rel-idx="${r.index}" title="Delete">&times;</button>
          </div>`;
        }
        html += `</div>`;

        // Incoming relationships
        html += `<div class="ep-section"><div class="ep-section-title">Incoming Relationships (${data.relationships_in.length})</div>`;
        if (data.relationships_in.length === 0) {
          html += `<div style="font-size:11px;color:#6b7280">None</div>`;
        }
        for (const r of data.relationships_in) {
          html += `<div class="ep-rel-row">
            <span class="rel-node" data-nav-node="${escHtml(r.from_id)}" title="${escHtml(r.from_id)}">${escHtml(r.from_label)}</span>
            <span class="rel-type">${escHtml(r.type)}</span>
            <button class="rel-del" data-rel-idx="${r.index}" title="Delete">&times;</button>
          </div>`;
        }
        html += `</div>`;

        // Add relationship
        html += `<details class="ep-add-rel"><summary>+ Add Relationship</summary>
          <div class="ep-add-rel-form">
            <div class="ep-field">
              <label>Direction</label>
              <select id="add-rel-dir">
                <option value="out">Outgoing from this node</option>
                <option value="in">Incoming to this node</option>
              </select>
            </div>
            <div class="ep-field">
              <label>Relationship Type</label>
              <select id="add-rel-type">
                ${buildRelTypeOptions()}
              </select>
            </div>
            <div class="ep-field">
              <label>Target Node</label>
              <input type="text" id="add-rel-node-search" placeholder="Search nodes…" autocomplete="off" />
              <select id="add-rel-node" size="4" style="margin-top:4px;max-height:120px"></select>
            </div>
            <button class="ep-btn" id="add-rel-btn">Add</button>
          </div>
        </details>`;

        // Delete node
        html += `<div class="ep-section" style="margin-top:20px;padding-top:14px;border-top:1px solid #374151">
          <div class="ep-section-title" style="color:#f87171">Danger Zone</div>
          <button class="ep-btn ep-btn-danger" id="delete-node-btn" style="width:100%">Delete this node</button>
        </div>`;

        body.innerHTML = html;

        // Wire up events
        document.getElementById("apply-attrs-btn").addEventListener("click", () => applyAttrs(data.id));

        body.querySelectorAll(".rel-del").forEach(btn => {
          btn.addEventListener("click", () => deleteRelationship(parseInt(btn.dataset.relIdx)));
        });

        body.querySelectorAll(".rel-node[data-nav-node]").forEach(el => {
          el.addEventListener("click", () => {
            const targetId = el.dataset.navNode;
            if (targetId && nodesDataSet.get(targetId)) {
              network.selectNodes([targetId]);
              network.focus(targetId, { scale: 1.2, animation: { duration: 400 } });
              loadNodeEdit(targetId);
            }
          });
        });

        // Node search for add-rel
        const searchInput = document.getElementById("add-rel-node-search");
        const nodeSelect = document.getElementById("add-rel-node");
        populateNodeSelect(nodeSelect, "");
        searchInput.addEventListener("input", () => {
          populateNodeSelect(nodeSelect, searchInput.value.trim().toLowerCase());
        });

        document.getElementById("add-rel-btn").addEventListener("click", () => addRelationship(data.id));

        document.getElementById("delete-node-btn").addEventListener("click", () => {
          if (!confirm(`Delete node "${data.attributes.name || data.id}"?\nAll its relationships will also be removed.`)) return;
          deleteNode(data.id);
        });
      }

      function buildRelTypeOptions() {
        const types = new Set();
        if (schemaData && schemaData.relation_constraints) {
          Object.keys(schemaData.relation_constraints).forEach(t => types.add(t));
        }
        // Also from current edges
        if (edgesDataSet) {
          edgesDataSet.forEach(e => types.add(e.label));
        }
        return Array.from(types).sort().map(t => `<option value="${escHtml(t)}">${escHtml(t)}</option>`).join("");
      }

      function populateNodeSelect(sel, filter) {
        sel.innerHTML = "";
        const items = allNodesList.filter(n =>
          !filter || n.label.toLowerCase().includes(filter) || n.id.toLowerCase().includes(filter)
        ).slice(0, 50);
        for (const n of items) {
          const opt = document.createElement("option");
          opt.value = n.id;
          opt.textContent = `[${n.type}] ${n.label}`;
          sel.appendChild(opt);
        }
      }

      function escHtml(s) {
        const d = document.createElement("div");
        d.textContent = s;
        return d.innerHTML;
      }

      async function applyAttrs(nodeId) {
        const inputs = document.querySelectorAll("#ep-body input[data-attr-key]");
        const attrs = {};
        inputs.forEach(inp => { attrs[inp.dataset.attrKey] = inp.value; });

        try {
          const res = await fetch(`/node/${encodeURIComponent(nodeId)}/update`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ attributes: attrs }),
          });
          const result = await res.json();
          if (result.ok && result.vis_node) {
            const enriched = enrichNode(result.vis_node);
            enriched.borderWidth = 3; // keep selected highlight
            nodesDataSet.update(enriched);
            // Update allNodes reference
            const idx = allNodes.findIndex(n => n.id === nodeId);
            if (idx >= 0) Object.assign(allNodes[idx], enriched);
            // Update allNodesList
            const nIdx = allNodesList.findIndex(n => n.id === nodeId);
            if (nIdx >= 0) allNodesList[nIdx].label = result.vis_node.label;
            markUnsaved();
            toast("Attributes updated");
          }
        } catch (err) {
          toast("Error: " + err.message);
        }
      }

      async function deleteRelationship(relIndex) {
        try {
          const res = await fetch(`/relationship/${relIndex}/delete`, {
            method: "POST",
          });
          const result = await res.json();
          if (result.ok) {
            // Rebuild edges from server
            await reloadEdges();
            markUnsaved();
            toast("Relationship deleted");
            // Re-fetch current node
            if (selectedNodeId) loadNodeEdit(selectedNodeId);
          }
        } catch (err) {
          toast("Error: " + err.message);
        }
      }

      async function addRelationship(currentNodeId) {
        const dir = document.getElementById("add-rel-dir").value;
        const rtype = document.getElementById("add-rel-type").value;
        const targetSel = document.getElementById("add-rel-node");
        const targetId = targetSel.value;
        if (!rtype || !targetId) {
          toast("Select relationship type and target node");
          return;
        }

        const from_id = dir === "out" ? currentNodeId : targetId;
        const to_id = dir === "out" ? targetId : currentNodeId;

        try {
          const res = await fetch("/relationship/add", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ type: rtype, from_id, to_id }),
          });
          const result = await res.json();
          if (result.ok && result.edge) {
            edgesDataSet.add(result.edge);
            allEdges.push(result.edge);
            // Rebuild edge type chips if new type
            if (!activeEdgeTypes.has(rtype)) {
              activeEdgeTypes.add(rtype);
              const allET = new Set();
              edgesDataSet.forEach(e => allET.add(e.label));
              buildEdgeChips(Array.from(allET).sort());
            }
            markUnsaved();
            toast("Relationship added");
            if (selectedNodeId) loadNodeEdit(selectedNodeId);
            updateStatusPill();
          }
        } catch (err) {
          toast("Error: " + err.message);
        }
      }

      async function deleteNode(nodeId) {
        try {
          const res = await fetch(`/node/${encodeURIComponent(nodeId)}/delete`, { method: "POST" });
          const result = await res.json();
          if (result.ok) {
            // Remove from DataSet and local arrays
            nodesDataSet.remove(nodeId);
            allNodes = allNodes.filter(n => n.id !== nodeId);
            allNodesList = allNodesList.filter(n => n.id !== nodeId);
            // Reload edges (some were removed server-side)
            await reloadEdges();
            selectedNodeId = null;
            markUnsaved();
            // Reset edit panel
            document.getElementById("ep-body").innerHTML =
              '<div class="ep-placeholder">Node deleted. Click another node to edit.</div>';
            toast(`Node deleted (${result.removed_relationships} relationships removed)`);
            updateStatusPill();
          }
        } catch (err) {
          toast("Error: " + err.message);
        }
      }

      async function reloadEdges() {
        try {
          const res = await fetch("/data");
          const payload = await res.json();
          allEdges = payload.edges || [];
          edgesDataSet.clear();
          edgesDataSet.add(allEdges);
          // Refresh edge type chips
          const edgeTypes = payload.edge_types || [];
          activeEdgeTypes.clear();
          edgeTypes.forEach(t => activeEdgeTypes.add(t));
          buildEdgeChips(edgeTypes);
          applyFilters();
        } catch (err) {
          console.error("reloadEdges error:", err);
        }
      }

      // ---- Save ----
      async function saveVersion() {
        try {
          const res = await fetch("/save", { method: "POST" });
          const result = await res.json();
          if (result.ok) {
            hasUnsavedChanges = false;
            document.getElementById("unsaved-dot").classList.remove("visible");
            document.getElementById("version-label").textContent = `v${result.version}`;
            toast(`Saved as ${result.saved_as}`);
          }
        } catch (err) {
          toast("Save error: " + err.message);
        }
      }

      // ---- Init ----
      async function init() {
        // Load schema & all nodes in parallel
        const [payloadRes, schemaRes, nodesListRes] = await Promise.all([
          fetch("/data"), fetch("/schema"), fetch("/all_nodes")
        ]);

        let payload;
        try {
          if (!payloadRes.ok) throw new Error(`HTTP ${payloadRes.status}`);
          payload = await payloadRes.json();
          schemaData = await schemaRes.json();
          allNodesList = await nodesListRes.json();
        } catch (err) {
          showError(`Failed to load data: ${err.message}`);
          return;
        }

        allNodes = payload.nodes || [];
        allEdges = payload.edges || [];
        const nodeTypes = payload.node_types || [];
        const edgeTypes = payload.edge_types || [];

        if (allNodes.length === 0) {
          showError("No nodes found in ontology.json");
          return;
        }

        nodeTypes.forEach((t, idx) => { colorByType[t] = pickColor(idx); });

        allNodes = allNodes.map(n => enrichNode(n));

        nodeTypes.forEach(t => activeNodeTypes.add(t));
        edgeTypes.forEach(t => activeEdgeTypes.add(t));

        // Persistent DataSet + DataView
        nodesDataSet = new vis.DataSet(allNodes);
        edgesDataSet = new vis.DataSet(allEdges);

        nodesView = new vis.DataView(nodesDataSet, {
          filter: n => activeNodeTypes.has(n.group)
        });
        edgesView = new vis.DataView(edgesDataSet, {
          filter: e => {
            if (!activeEdgeTypes.has(e.label)) return false;
            const fromNode = nodesDataSet.get(e.from);
            const toNode = nodesDataSet.get(e.to);
            if (!fromNode || !toNode) return false;
            return activeNodeTypes.has(fromNode.group) && activeNodeTypes.has(toNode.group);
          }
        });

        const container = document.getElementById("network");
        const options = {
          autoResize: true,
          physics: {
            enabled: true,
            stabilization: { iterations: 300, fit: true },
            barnesHut: {
              gravitationalConstant: -3000,
              springLength: 130,
              springConstant: 0.04,
              damping: 0.15,
            },
          },
          interaction: {
            hover: true,
            tooltipDelay: 80,
            hideEdgesOnDrag: true,
            navigationButtons: false,
            keyboard: true,
          },
          edges: {
            smooth: { enabled: true, type: "continuous", roundness: 0.2 },
            font: { size: 9, color: "#6b7280", background: "transparent", strokeWidth: 0 },
            color: { color: "#374151", highlight: "#9ca3af", hover: "#9ca3af" },
            arrows: { to: { enabled: true, scaleFactor: 0.6 } },
            width: 1,
          },
        };

        try {
          network = new vis.Network(
            container,
            { nodes: nodesView, edges: edgesView },
            options
          );
        } catch (err) {
          showError(`Failed to render graph: ${err.message}`);
          return;
        }

        buildNodeChips(nodeTypes);
        buildEdgeChips(edgeTypes);

        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            network.redraw();
            network.fit({ animation: false });
          });
        });

        network.once("stabilizationIterationsDone", () => {
          network.fit({ animation: { duration: 600, easingFunction: "easeInOutQuad" } });
          updateStatusPill();
        });

        setTimeout(() => {
          const pill = document.getElementById("status-pill");
          if (pill.textContent === "Loading…") {
            network.redraw();
            network.fit();
            updateStatusPill();
          }
        }, 8000);

        document.getElementById("physics-toggle").addEventListener("change", e => {
          network.setOptions({ physics: { enabled: e.target.checked } });
        });
        document.getElementById("smooth-toggle").addEventListener("change", e => {
          network.setOptions({ edges: { smooth: { enabled: e.target.checked } } });
        });
        document.getElementById("fit-btn").addEventListener("click", () => {
          network.fit({ animation: { duration: 400, easingFunction: "easeInOutQuad" } });
        });

        // Edit mode toggle
        document.getElementById("edit-mode-toggle").addEventListener("click", () => {
          editMode = !editMode;
          document.getElementById("layout").classList.toggle("edit-mode", editMode);
          document.getElementById("edit-mode-toggle").classList.toggle("active", editMode);
          // Resize network after layout transition
          setTimeout(() => { network.redraw(); network.fit(); }, 300);
        });

        // Node click in edit mode
        network.on("click", params => {
          if (!editMode || params.nodes.length === 0) return;
          loadNodeEdit(params.nodes[0]);
        });

        // Save button
        document.getElementById("save-btn").addEventListener("click", saveVersion);

        // Load current version
        try {
          const statusRes = await fetch("/status");
          const status = await statusRes.json();
          document.getElementById("version-label").textContent = `v${status.version}`;
        } catch {}

        // Unsaved changes warning
        window.addEventListener("beforeunload", e => {
          if (hasUnsavedChanges) {
            e.preventDefault();
            e.returnValue = "";
          }
        });
      }

      window.addEventListener("load", init);
    </script>
  </body>
</html>
"""
