from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class Node:
    id: str
    label: str
    ntype: str


@dataclass
class Edge:
    id: str
    source: str
    target: str
    etype: str


def _find_node_id_key(obj: Dict[str, Any]) -> Optional[str]:
    for k in obj:
        if k.endswith("_id"):
            return k
    return None


def _node_id(obj: Dict[str, Any]) -> Optional[str]:
    k = _find_node_id_key(obj)
    return str(obj[k]) if k else None


def _node_label(obj: Dict[str, Any], fallback: str = "") -> str:
    return str(obj.get("name", obj.get("code", obj.get("locator", obj.get("version", fallback)))))


def _build_id_to_info(ont: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """Returns {node_id: {"type": ntype, "label": label}}."""
    out: Dict[str, Dict[str, str]] = {}
    for ntype, items in ont.get("nodes", {}).items():
        for obj in items:
            nid = _node_id(obj)
            if nid:
                out[nid] = {"type": ntype, "label": _node_label(obj, nid)}
    return out


def build_graph(data: Dict[str, Any]) -> Dict[str, Any]:
    raw_nodes = data.get("nodes", {})
    raw_rels = data.get("relationships", [])

    nodes: List[Node] = []
    for ntype, items in raw_nodes.items():
        for obj in items:
            obj_id = _node_id(obj)
            if not obj_id:
                continue
            label = _node_label(obj, obj_id)
            nodes.append(Node(id=obj_id, label=label, ntype=ntype))

    edges: List[Edge] = []
    for idx, rel in enumerate(raw_rels):
        etype = str(rel.get("type", "REL"))
        from_id = str(rel.get("from_id", ""))
        to_id = str(rel.get("to_id", ""))
        if not from_id or not to_id:
            continue
        edges.append(Edge(id=f"e{idx}", source=from_id, target=to_id, etype=etype))

    node_types = sorted({n.ntype for n in nodes})
    edge_types = sorted({e.etype for e in edges})

    vis_nodes = [
        {
            "id": n.id,
            "label": n.label,
            "group": n.ntype,
            "title": f"{n.ntype}: {n.label}<br><code>{n.id}</code>",
        }
        for n in nodes
    ]
    vis_edges = [
        {
            "id": e.id,
            "from": e.source,
            "to": e.target,
            "label": e.etype,
            "title": e.etype,
            "arrows": "to",
        }
        for e in edges
    ]

    return {
        "nodes": vis_nodes,
        "edges": vis_edges,
        "node_types": node_types,
        "edge_types": edge_types,
    }
