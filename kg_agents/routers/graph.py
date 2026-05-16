from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from kg_agents.config import DATA_DIR, DEFAULT_MANUALS_DIR
from kg_agents.engine.log_loader import load_log_store
from kg_agents.engine.log_search import search_logs
from kg_agents.services import instance_store

router = APIRouter(prefix="/v1/kg-agents", tags=["graph"])


def _manuals_dir() -> Path:
    root = DATA_DIR / "manuals"
    return root if root.exists() else DEFAULT_MANUALS_DIR


@router.get("/manuals")
def list_manuals():
    """List PDF manuals available under the static /manuals mount."""
    d = _manuals_dir()
    if not d.exists():
        return {"manuals": []}
    items = []
    for p in sorted(d.glob("*.pdf"), key=lambda x: x.name.lower()):
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        items.append({
            "title": p.stem,
            "filename": p.name,
            "size": size,
            "url": f"/manuals/{p.name}",
        })
    return {"manuals": items}

# Node type colors aligned with the platform color system (colors.js)
NODE_TYPE_COLORS: dict[str, str] = {
    "Symptom":          "#EF4444",  # red-500
    "FailureMode":      "#F97316",  # orange-500
    "CorrectiveAction": "#10B981",  # emerald-500
    "Action":           "#10B981",  # emerald-500
    "Component":        "#3B82F6",  # blue-500
    "ErrorCode":        "#F59E0B",  # yellow-500
    "Asset":            "#8B5CF6",  # violet-500
    "Process":          "#0891B2",  # cyan-600
    "Defect":           "#EF4444",  # red-500
    "RootCause":        "#F97316",  # orange-500
    "Inspection":       "#F59E0B",  # yellow-500
    "LogEvent":         "#14B8A6",  # teal-500 — virtual nodes from log overlay
}

# Edge labels for virtual log overlay edges
_LOG_EDGE_TO_ASSET = "LOG_FOR_ASSET"
_LOG_EDGE_TO_COMPONENT = "OBSERVED_ON"
_LOG_EDGE_TO_FAILURE_MODE = "SIMILAR_TO_FAILURE_MODE"

# Fallback palette for any other node types
_FALLBACK_PALETTE = [
    "#64748B",  # slate-500
    "#0891B2",  # cyan-600
    "#7C3AED",  # violet-600
    "#DB2777",  # pink-600
    "#0D9488",  # teal-600
]


def _node_id(node: dict) -> str | None:
    for key, value in node.items():
        if key.endswith("_id") and isinstance(value, str):
            return value
    return None


def _build_log_overlay(
    instance_id: str,
    log_query: str | None,
    limit_logs: int,
    ontology_node_ids: set[str],
) -> tuple[list[dict], list[dict]]:
    """Run a log search and project the matches into virtual graph nodes/edges.

    One LogEvent node per signature (the top-matching occurrence acts as the
    visible anchor). Virtual edges link only to ontology nodes that actually
    exist in the loaded graph — anything else (legacy ids, dropped components)
    is silently skipped to avoid dangling references.
    """
    query = (log_query or "").strip()

    if query:
        result = search_logs(
            query=query,
            instance_id=instance_id,
            limit=limit_logs,
            use_llm_rerank=True,
        )
        matches = result.get("matches", [])
    else:
        # No query: surface top signatures by occurrence count. Lets the UI
        # show "the most active log patterns on this machine" without having
        # to invent a search string.
        store = load_log_store(instance_id)
        if store is None or store.is_empty:
            return [], []
        ranked_sigs = sorted(
            store.signature_meta.items(),
            key=lambda kv: kv[1].get("occurrence_count", 0),
            reverse=True,
        )[:limit_logs]
        matches = []
        for sig_id, meta in ranked_sigs:
            occurrences = store.rows_by_signature.get(sig_id, [])
            if not occurrences:
                continue
            anchor = occurrences[0]  # most recent (rows_by_signature is sorted desc)
            matches.append({
                "event_signature_id": sig_id,
                "occurrence_count": meta.get("occurrence_count"),
                "linked_failure_mode_id": meta.get("linked_failure_mode_id"),
                "top_match_log": anchor,
            })

    asset_id = "asset_irc5"  # canonical IRC5 asset; safe-skip if missing
    has_asset = asset_id in ontology_node_ids

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_log_ids: set[str] = set()
    edge_counter = 0

    def _next_edge_id() -> str:
        nonlocal edge_counter
        edge_counter += 1
        return f"l{edge_counter}"

    for match in matches:
        top = match.get("top_match_log") or {}
        log_id = top.get("log_id")
        if not log_id or log_id in seen_log_ids:
            continue
        seen_log_ids.add(log_id)

        date = (top.get("occurred_at") or "")[:10]
        severity = top.get("severity_text") or ""
        wo = top.get("work_order_id") or ""
        signature = match.get("event_signature_id") or ""
        title_parts = [p for p in (severity, date, wo) if p]

        nodes.append({
            "id": log_id,
            "label": top.get("title") or signature or log_id,
            "group": "LogEvent",
            "title": " · ".join(title_parts) or signature,
            "description": top.get("body") or "",
            "severity": severity,
            "occurrence_count": match.get("occurrence_count"),
            "event_signature_id": signature,
        })

        if has_asset:
            edges.append({
                "id": _next_edge_id(),
                "from": log_id,
                "to": asset_id,
                "label": _LOG_EDGE_TO_ASSET,
            })

        comp_id = top.get("component_id") or ""
        if comp_id and comp_id in ontology_node_ids:
            edges.append({
                "id": _next_edge_id(),
                "from": log_id,
                "to": comp_id,
                "label": _LOG_EDGE_TO_COMPONENT,
            })

        fm_id = match.get("linked_failure_mode_id") or top.get("linked_failure_mode_id") or ""
        if fm_id and fm_id in ontology_node_ids:
            edges.append({
                "id": _next_edge_id(),
                "from": log_id,
                "to": fm_id,
                "label": _LOG_EDGE_TO_FAILURE_MODE,
            })

    return nodes, edges


@router.get("/instances/{instance_id}/graph-data")
async def graph_data(
    instance_id: str,
    include_logs: bool = Query(False, description="Overlay top log events as virtual LogEvent nodes."),
    log_query: str | None = Query(None, description="Free-text query driving the log overlay (defaults to all logs when empty)."),
    limit_logs: int = Query(20, ge=1, le=100, description="Max log signatures to surface as virtual nodes."),
):
    inst = instance_store.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")

    ont_path = instance_store.get_ontology_path(instance_id)
    if not ont_path.exists():
        raise HTTPException(status_code=404, detail="Ontology not found")

    with ont_path.open("r", encoding="utf-8") as f:
        ont = json.load(f)

    nodes_by_type = ont.get("nodes", {})
    relationships = ont.get("relationships", [])

    type_list = sorted(nodes_by_type.keys())
    fallback_idx = 0
    color_map = {}
    for t in type_list:
        if t in NODE_TYPE_COLORS:
            color_map[t] = NODE_TYPE_COLORS[t]
        else:
            color_map[t] = _FALLBACK_PALETTE[fallback_idx % len(_FALLBACK_PALETTE)]
            fallback_idx += 1

    vis_nodes = []
    ontology_node_ids: set[str] = set()
    for ntype, node_list in nodes_by_type.items():
        for node in node_list:
            nid = _node_id(node)
            if not nid:
                continue
            ontology_node_ids.add(nid)
            vis_nodes.append({
                "id": nid,
                "label": node.get("name", nid),
                "group": ntype,
                "title": f"{ntype}: {node.get('name', nid)}",
                "description": node.get("description", ""),
                "severity": node.get("severity", ""),
            })

    vis_edges = []
    for i, rel in enumerate(relationships):
        vis_edges.append({
            "id": f"e{i}",
            "from": rel["from_id"],
            "to": rel["to_id"],
            "label": rel["type"],
        })

    edge_types_set = {r["type"] for r in relationships}

    if include_logs:
        log_nodes, log_edges = _build_log_overlay(
            instance_id=instance_id,
            log_query=log_query,
            limit_logs=limit_logs,
            ontology_node_ids=ontology_node_ids,
        )
        if log_nodes:
            vis_nodes.extend(log_nodes)
            if "LogEvent" not in type_list:
                type_list = sorted(type_list + ["LogEvent"])
                color_map["LogEvent"] = NODE_TYPE_COLORS["LogEvent"]
        vis_edges.extend(log_edges)
        edge_types_set.update(e["label"] for e in log_edges)

    edge_types = sorted(edge_types_set)
    return {
        "nodes": vis_nodes,
        "edges": vis_edges,
        "node_types": type_list,
        "edge_types": edge_types,
        "color_map": color_map,
    }
