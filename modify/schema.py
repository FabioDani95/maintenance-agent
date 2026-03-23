from __future__ import annotations

import json
from typing import Any, Dict, List

from config import SCHEMA_PATH

_schema_cache: Dict[str, Any] | None = None


def load_schema(ont: Dict[str, Any]) -> Dict[str, Any]:
    """Load and cache the schema, merging types inferred from live ontology data."""
    global _schema_cache
    if _schema_cache is not None:
        return _schema_cache

    node_types: Dict[str, List[Dict]] = {}
    relation_constraints: Dict[str, Dict[str, List[str]]] = {}

    if SCHEMA_PATH.exists():
        with SCHEMA_PATH.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        for ndef in raw.get("nodes", []):
            node_types[ndef["name"]] = ndef.get("properties", [])
        for rdef in raw.get("relations", []):
            domain = rdef.get("domain", [])
            rng = rdef.get("range", [])
            if isinstance(domain, str):
                domain = [domain]
            if isinstance(rng, str):
                rng = [rng]
            relation_constraints[rdef["name"]] = {"domain": domain, "range": rng}

    # Merge extra types from live data
    for ntype, items in ont.get("nodes", {}).items():
        if ntype not in node_types:
            if items:
                props = [{"name": k, "type": "string", "required": True} for k in items[0]]
            else:
                props = []
            node_types[ntype] = props

    for rel in ont.get("relationships", []):
        rtype = rel.get("type", "")
        if rtype and rtype not in relation_constraints:
            relation_constraints[rtype] = {"domain": [], "range": []}

    _schema_cache = {
        "node_types": node_types,
        "relation_constraints": relation_constraints,
    }
    return _schema_cache


def invalidate_schema_cache() -> None:
    global _schema_cache
    _schema_cache = None
