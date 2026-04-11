from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kg_agents.config import DEFAULT_ONTOLOGY_PATH


def load_ontology(path: Path | None = None) -> dict[str, Any]:
    ontology_path = path or DEFAULT_ONTOLOGY_PATH
    with ontology_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_product_metadata(data: dict[str, Any]) -> dict[str, Any]:
    meta = data.get("metadata", {})
    return {
        "product_name": meta.get("product_name", "Unknown Product"),
        "product_short_name": meta.get("product_short_name", meta.get("product_name", "Product")),
        "product_type": meta.get("product_type", "product"),
        "domain_topics": meta.get("domain_topics", []),
    }


class OntologyIndex:
    """Pre-built lookup structures for fast graph traversal."""

    def __init__(self, data: dict[str, Any]) -> None:
        nodes = data.get("nodes", {})
        relationships = data.get("relationships", [])

        self.nodes_by_id: dict[str, dict[str, Any]] = {}
        for node_list in nodes.values():
            for node in node_list:
                node_id = self._node_id(node)
                if node_id:
                    self.nodes_by_id[node_id] = node

        self.symptoms: list[dict[str, Any]] = nodes.get("Symptom", [])
        self.error_codes: list[dict[str, Any]] = nodes.get("ErrorCode", [])

        self.may_indicate: dict[str, list[str]] = {}
        self.resolved_by: dict[str, list[str]] = {}
        self.affects: dict[str, list[str]] = {}
        self.indicates: dict[str, list[str]] = {}

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
        for key, value in node.items():
            if key.endswith("_id") and isinstance(value, str):
                return value
        return None


_default_index: OntologyIndex | None = None
_default_product_meta: dict[str, Any] | None = None


def get_index(path: Path | None = None) -> OntologyIndex:
    global _default_index
    if path is not None:
        return OntologyIndex(load_ontology(path))
    if _default_index is None:
        _default_index = OntologyIndex(load_ontology())
    return _default_index


def get_product_metadata(
    data: dict[str, Any] | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    global _default_product_meta
    if data is not None:
        return build_product_metadata(data)
    if path is not None:
        return build_product_metadata(load_ontology(path))
    if _default_product_meta is None:
        _default_product_meta = build_product_metadata(load_ontology())
    return _default_product_meta
