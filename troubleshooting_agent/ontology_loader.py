from __future__ import annotations

import json
from typing import Any

from config import ONTOLOGY_PATH


def load_ontology() -> dict[str, Any]:
    with ONTOLOGY_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


class OntologyIndex:
    """Pre-built lookup structures for fast graph traversal."""

    def __init__(self, data: dict[str, Any]) -> None:
        nodes = data.get("nodes", {})
        relationships = data.get("relationships", [])

        # Flat id → node dict for all node types
        self.nodes_by_id: dict[str, dict[str, Any]] = {}
        for node_list in nodes.values():
            for node in node_list:
                node_id = self._node_id(node)
                if node_id:
                    self.nodes_by_id[node_id] = node

        self.symptoms: list[dict[str, Any]] = nodes.get("Symptom", [])
        self.error_codes: list[dict[str, Any]] = nodes.get("ErrorCode", [])

        # Adjacency maps derived from relationships
        self.may_indicate: dict[str, list[str]] = {}   # symptom_id → [failure_mode_id]
        self.resolved_by: dict[str, list[str]] = {}    # failure_mode_id → [action_id]
        self.affects: dict[str, list[str]] = {}        # failure_mode_id → [component_id]
        self.indicates: dict[str, list[str]] = {}      # error_code_id → [failure_mode_id]

        for rel in relationships:
            rtype = rel.get("type")
            src = rel.get("from_id")
            dst = rel.get("to_id")
            if rtype == "MAY_INDICATE":
                self.may_indicate.setdefault(src, []).append(dst)
            elif rtype == "RESOLVED_BY":
                self.resolved_by.setdefault(src, []).append(dst)
            elif rtype == "AFFECTS":
                self.affects.setdefault(src, []).append(dst)
            elif rtype == "INDICATES":
                self.indicates.setdefault(src, []).append(dst)

    @staticmethod
    def _node_id(node: dict[str, Any]) -> str | None:
        for key in ("symptom_id", "failure_mode_id", "action_id",
                    "component_id", "printer_id", "error_code_id"):
            if key in node:
                return node[key]
        return None


_index: OntologyIndex | None = None
_product_meta: dict[str, Any] | None = None


def get_index() -> OntologyIndex:
    global _index
    if _index is None:
        _index = OntologyIndex(load_ontology())
    return _index


def get_product_metadata() -> dict[str, Any]:
    """Return product metadata from the ontology (cached)."""
    global _product_meta
    if _product_meta is None:
        data = load_ontology()
        meta = data.get("metadata", {})
        _product_meta = {
            "product_name": meta.get("product_name", "Unknown Product"),
            "product_short_name": meta.get("product_short_name", meta.get("product_name", "Product")),
            "product_type": meta.get("product_type", "product"),
            "domain_topics": meta.get("domain_topics", []),
        }
    return _product_meta
