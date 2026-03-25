from __future__ import annotations

import json
from fastapi import APIRouter, HTTPException

from kg_agents.services import instance_store

router = APIRouter(prefix="/v1/kg-agents", tags=["graph"])

COLOR_PALETTE = [
    "#6b8cba", "#7aab82", "#c47e5a", "#9b79b8",
    "#5fa8a0", "#b87a7a", "#a0a052", "#6b9eb8",
    "#b8906b", "#7a8fb8", "#88a87a",
]


def _node_id(node: dict) -> str | None:
    for key in ("symptom_id", "failure_mode_id", "action_id",
                "component_id", "printer_id", "asset_id", "error_code_id"):
        if key in node:
            return node[key]
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
    color_map = {t: COLOR_PALETTE[i % len(COLOR_PALETTE)] for i, t in enumerate(type_list)}

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
