from __future__ import annotations

import copy
import json
from typing import Any, Dict, Optional

from config import ONTOLOGY_PATH, BASE_DIR

# ---------------------------------------------------------------------------
# In-memory working state
# ---------------------------------------------------------------------------
_working_ontology: Optional[Dict[str, Any]] = None
_has_unsaved_changes: bool = False


def load_ontology() -> Dict[str, Any]:
    if not ONTOLOGY_PATH.exists():
        raise FileNotFoundError(f"ontology.json non trovato in {BASE_DIR}")
    with ONTOLOGY_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_working_ontology() -> Dict[str, Any]:
    global _working_ontology
    if _working_ontology is None:
        _working_ontology = copy.deepcopy(load_ontology())
    return _working_ontology


def _mark_dirty() -> None:
    global _has_unsaved_changes
    _has_unsaved_changes = True


def is_dirty() -> bool:
    return _has_unsaved_changes


def clear_dirty() -> None:
    global _has_unsaved_changes
    _has_unsaved_changes = False


def save_to_disk() -> Dict[str, Any]:
    """Bumps version, writes versioned archive and updates main ontology.json."""
    ont = get_working_ontology()
    meta = ont.setdefault("metadata", {})

    # Bump version
    old_ver = str(meta.get("version", "1.0"))
    parts = old_ver.split(".")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
    except ValueError:
        parts.append("1")
    new_ver = ".".join(parts)
    meta["version"] = new_ver

    # Recount
    total_nodes = sum(len(items) for items in ont.get("nodes", {}).values())
    total_rels = len(ont.get("relationships", []))
    meta["total_nodes"] = total_nodes
    meta["total_relationships"] = total_rels

    # Write versioned file
    old_versions_dir = BASE_DIR / "oldVersion"
    old_versions_dir.mkdir(exist_ok=True)
    versioned_name = f"ontology_v{new_ver}.json"
    versioned_path = old_versions_dir / versioned_name
    with versioned_path.open("w", encoding="utf-8") as f:
        json.dump(ont, f, indent=4, ensure_ascii=False)

    # Update main file
    with ONTOLOGY_PATH.open("w", encoding="utf-8") as f:
        json.dump(ont, f, indent=4, ensure_ascii=False)

    clear_dirty()

    return {
        "ok": True,
        "version": new_ver,
        "saved_as": versioned_name,
        "total_nodes": total_nodes,
        "total_relationships": total_rels,
    }
