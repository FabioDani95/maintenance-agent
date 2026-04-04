from __future__ import annotations

import json
from fastapi import APIRouter, HTTPException

from kg_agents.services import instance_store

router = APIRouter(prefix="/v1/kg-agents", tags=["graph"])

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
}

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


@router.get("/instances/{instance_id}/graph-data")
async def graph_data(instance_id: str):
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
