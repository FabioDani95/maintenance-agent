from __future__ import annotations

import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory

from ontology_loader import load_ontology, get_index, get_product_metadata
from orchestrator import handle_message, handle_next_issue, reset_session, get_last_trace

api_bp = Blueprint("api", __name__)

_MANUALS_DIR = Path(__file__).resolve().parent / "manuals"

# Colour palette aligned with the ontology editor UI.
_COLOR_PALETTE = [
    "#6b8cba", "#7aab82", "#c47e5a", "#9b79b8",
    "#5fa8a0", "#b87a7a", "#a0a052", "#6b9eb8",
    "#b8906b", "#7a8fb8", "#88a87a",
]


def _node_id(node: dict) -> str | None:
    for key in ("symptom_id", "failure_mode_id", "action_id", "component_id", "asset_id", "error_code_id"):
        if key in node:
            return node[key]
    return None


def _build_graph_payload() -> dict:
    """Convert the ontology into vis-network compatible nodes + edges."""
    ont = load_ontology()
    nodes_by_type = ont.get("nodes", {})
    relationships = ont.get("relationships", [])

    type_list = sorted(nodes_by_type.keys())
    color_map = {t: _COLOR_PALETTE[i % len(_COLOR_PALETTE)] for i, t in enumerate(type_list)}

    vis_nodes = []
    for ntype, node_list in nodes_by_type.items():
        for node in node_list:
            nid = _node_id(node)
            if not nid:
                continue
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

    edge_types = sorted({r["type"] for r in relationships})
    return {
        "nodes": vis_nodes,
        "edges": vis_edges,
        "node_types": type_list,
        "edge_types": edge_types,
        "color_map": color_map,
    }


@api_bp.route("/graph_data")
def graph_data():
    try:
        return jsonify(_build_graph_payload())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@api_bp.route("/chat", methods=["POST"])
def chat():
    body = request.get_json(force=True, silent=True) or {}
    user_message = (body.get("message") or "").strip()
    session_id = (body.get("session_id") or "").strip() or str(uuid.uuid4())
    model = (body.get("model") or "").strip() or None

    if not user_message:
        return jsonify({"error": "empty message", "session_id": session_id}), 400

    try:
        result = handle_message(user_message, session_id, model=model)
    except Exception as exc:
        return jsonify({"error": str(exc), "session_id": session_id}), 500

    trace = get_last_trace(session_id)
    return jsonify({
        "reply": result["reply"],
        "session_id": session_id,
        "highlight": trace,
        "has_more_issues": result.get("has_more_issues", False),
        "issue_number": result.get("issue_number"),
        "total_issues": result.get("total_issues"),
        "telemetry": result.get("telemetry"),
    })


@api_bp.route("/next_issue", methods=["POST"])
def next_issue():
    body = request.get_json(force=True, silent=True) or {}
    session_id = (body.get("session_id") or "").strip()
    model = (body.get("model") or "").strip() or None

    if not session_id:
        return jsonify({"error": "missing session_id"}), 400

    try:
        result = handle_next_issue(session_id, model=model)
    except Exception as exc:
        return jsonify({"error": str(exc), "session_id": session_id}), 500

    trace = result.get("highlight") or get_last_trace(session_id)
    return jsonify({
        "reply": result["reply"],
        "session_id": session_id,
        "highlight": trace,
        "has_more_issues": result.get("has_more_issues", False),
        "issue_number": result.get("issue_number"),
        "total_issues": result.get("total_issues"),
        "telemetry": result.get("telemetry"),
    })


@api_bp.route("/telemetry/<signal_name>")
def telemetry_signal(signal_name: str):
    """Return raw time series data for a specific signal."""
    from telemetry_loader import get_signals, get_stats
    hours = request.args.get("hours", 48, type=int)
    signals = get_signals([signal_name], hours=hours)
    stats = get_stats([signal_name])
    if signal_name not in signals:
        return jsonify({"error": f"Signal '{signal_name}' not found"}), 404
    return jsonify({
        "signal": signal_name,
        "data": signals[signal_name],
        "stats": stats.get(signal_name, {}),
    })


@api_bp.route("/reset", methods=["POST"])
def reset():
    body = request.get_json(force=True, silent=True) or {}
    session_id = (body.get("session_id") or "").strip()
    if session_id:
        reset_session(session_id)
    return jsonify({"ok": True})


@api_bp.route("/manuals/<path:filename>")
def serve_manual(filename: str):
    return send_from_directory(str(_MANUALS_DIR), filename)


@api_bp.route("/product_info")
def product_info():
    """Return product metadata and representative symptoms for the UI."""
    meta = get_product_metadata()
    index = get_index()
    chips = []
    for symptom in index.symptoms[:4]:
        chips.append({
            "label": symptom.get("name", ""),
            "query": symptom.get("name", "") + (
                ". " + symptom.get("description", "") if symptom.get("description") else ""
            ),
        })
    for ec in index.error_codes[:4]:
        code = ec.get("code", "")
        chips.append({
            "label": code,
            "query": code,
        })
    return jsonify({**meta, "suggested_symptoms": chips})


@api_bp.route("/status")
def status():
    try:
        ont = load_ontology()
        meta = ont.get("metadata", {})
        return jsonify({
            "ok": True,
            "ontology_version": meta.get("version"),
            "total_nodes": meta.get("total_nodes"),
            "total_relationships": meta.get("total_relationships"),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
