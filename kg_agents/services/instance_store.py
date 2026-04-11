from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kg_agents.config import DATA_DIR, BASE_DIR, SEED_DIR


INSTANCES_DIR = DATA_DIR / "instances"
SUMMARIES_DIR = DATA_DIR / "extraction_summaries"


def validate_ontology_data(ontology_data: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Validate ontology_data against the agent's ontology_schema.

    Returns a list of error messages. Empty list means valid.
    """
    errors: list[str] = []
    if not schema or not schema.get("nodes"):
        return errors  # no schema to validate against

    # Build allowed node types and their id field names from schema
    schema_node_types: dict[str, str] = {}  # name -> id_field
    for node_def in schema.get("nodes", []):
        name = node_def["name"]
        props = node_def.get("properties", [])
        id_field = next((p["name"] for p in props if p.get("unique")), None)
        schema_node_types[name] = id_field or ""

    # Build allowed relationship types with domain/range
    schema_relations: dict[str, tuple[str, str]] = {}  # name -> (domain, range)
    for rel_def in schema.get("relations", []):
        schema_relations[rel_def["name"]] = (rel_def["domain"], rel_def["range"])

    nodes = ontology_data.get("nodes", {})
    relationships = ontology_data.get("relationships", [])

    # Validate node types
    for node_type in nodes:
        if node_type not in schema_node_types:
            errors.append(f"Unknown node type '{node_type}'. Allowed: {sorted(schema_node_types.keys())}")

    # Collect all node IDs and their types for relationship validation
    node_id_to_type: dict[str, str] = {}
    for node_type, node_list in nodes.items():
        if node_type not in schema_node_types:
            continue
        id_field = schema_node_types[node_type]
        for node in node_list:
            nid = node.get(id_field)
            if not nid:
                errors.append(f"Node in '{node_type}' missing required id field '{id_field}'")
            else:
                node_id_to_type[nid] = node_type

    # Validate relationships
    for i, rel in enumerate(relationships):
        rel_type = rel.get("type", "")
        from_id = rel.get("from_id", "")
        to_id = rel.get("to_id", "")

        if rel_type not in schema_relations:
            errors.append(f"Relationship[{i}]: unknown type '{rel_type}'. Allowed: {sorted(schema_relations.keys())}")
            continue

        expected_domain, expected_range = schema_relations[rel_type]
        from_type = node_id_to_type.get(from_id)
        to_type = node_id_to_type.get(to_id)

        if from_id and from_type and from_type != expected_domain:
            errors.append(
                f"Relationship[{i}] '{rel_type}': from_id '{from_id}' is type '{from_type}', expected '{expected_domain}'"
            )
        if to_id and to_type and to_type != expected_range:
            errors.append(
                f"Relationship[{i}] '{rel_type}': to_id '{to_id}' is type '{to_type}', expected '{expected_range}'"
            )

    return errors


def _instance_dir(instance_id: str) -> Path:
    return INSTANCES_DIR / instance_id


def _load_meta(instance_id: str) -> dict[str, Any] | None:
    meta_file = _instance_dir(instance_id) / "meta.json"
    if not meta_file.exists():
        return None
    with meta_file.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_meta(instance_id: str, meta: dict[str, Any]) -> None:
    d = _instance_dir(instance_id)
    d.mkdir(parents=True, exist_ok=True)
    with (d / "meta.json").open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)


def _count_ontology(instance_id: str) -> tuple[int, int]:
    ont_file = _instance_dir(instance_id) / "ontology.json"
    if not ont_file.exists():
        return 0, 0
    with ont_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    nodes = data.get("nodes", {})
    node_count = sum(len(v) for v in nodes.values())
    rel_count = len(data.get("relationships", []))
    return node_count, rel_count


def list_instances(agent_id: str) -> list[dict[str, Any]]:
    result = []
    if not INSTANCES_DIR.exists():
        return result
    for p in sorted(INSTANCES_DIR.iterdir()):
        if not p.is_dir():
            continue
        meta = _load_meta(p.name)
        if meta and meta.get("agent_id") == agent_id:
            nc, rc = _count_ontology(p.name)
            meta["node_count"] = nc
            meta["relationship_count"] = rc
            result.append(meta)
    return result


def get_instance(instance_id: str) -> dict[str, Any] | None:
    meta = _load_meta(instance_id)
    if meta:
        nc, rc = _count_ontology(instance_id)
        meta["node_count"] = nc
        meta["relationship_count"] = rc
    return meta


def create_instance(agent_id: str, data: dict[str, Any]) -> dict[str, Any]:
    instance_id = str(uuid.uuid4())
    d = _instance_dir(instance_id)
    d.mkdir(parents=True, exist_ok=True)

    meta = {
        "id": instance_id,
        "agent_id": agent_id,
        "name": data["name"],
        "description": data.get("description", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_meta(instance_id, meta)

    # Save ontology data if provided
    ontology_data = data.get("ontology_data")
    if ontology_data:
        with (d / "ontology.json").open("w", encoding="utf-8") as f:
            json.dump(ontology_data, f, indent=2, ensure_ascii=False)

    return meta


def update_instance(instance_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    meta = _load_meta(instance_id)
    if not meta:
        return None
    if "name" in data and data["name"] is not None:
        meta["name"] = data["name"]
    if "description" in data and data["description"] is not None:
        meta["description"] = data["description"]
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_meta(instance_id, meta)

    if "ontology_data" in data and data["ontology_data"] is not None:
        d = _instance_dir(instance_id)
        with (d / "ontology.json").open("w", encoding="utf-8") as f:
            json.dump(data["ontology_data"], f, indent=2, ensure_ascii=False)

    nc, rc = _count_ontology(instance_id)
    meta["node_count"] = nc
    meta["relationship_count"] = rc
    return meta


def delete_instance(instance_id: str) -> bool:
    d = _instance_dir(instance_id)
    if not d.exists():
        return False
    shutil.rmtree(d)
    return True


def get_ontology_path(instance_id: str) -> Path:
    return _instance_dir(instance_id) / "ontology.json"


def get_embeddings_path(instance_id: str) -> Path:
    return _instance_dir(instance_id) / "symptom_embeddings.json"


def get_telemetry_dir(instance_id: str) -> Path:
    return _instance_dir(instance_id) / "telemetry"


# ── Cross-agent queries ──

def list_all_instances() -> list[dict[str, Any]]:
    """Return every instance across all agents, with agent_name attached."""
    from kg_agents.services.agent_store import list_agents

    agent_map: dict[str, str] = {}
    for agent in list_agents():
        agent_map[agent["id"]] = agent["name"]

    result = []
    if not INSTANCES_DIR.exists():
        return result
    for p in sorted(INSTANCES_DIR.iterdir()):
        if not p.is_dir():
            continue
        meta = _load_meta(p.name)
        if not meta:
            continue
        nc, rc = _count_ontology(p.name)
        meta["node_count"] = nc
        meta["relationship_count"] = rc
        meta["agent_name"] = agent_map.get(meta.get("agent_id", ""), "")
        result.append(meta)
    return result


def get_extraction_summary(instance_id: str) -> dict[str, Any] | None:
    """Read the extraction summary for an instance, or None if it doesn't exist."""
    summary_file = SUMMARIES_DIR / f"{instance_id}.json"
    if not summary_file.exists():
        return None
    with summary_file.open("r", encoding="utf-8") as f:
        return json.load(f)


# ── Device Links ──
def _devices_file(instance_id: str) -> Path:
    return _instance_dir(instance_id) / "devices.json"


def _load_devices(instance_id: str) -> list[dict[str, Any]]:
    f = _devices_file(instance_id)
    if not f.exists():
        return []
    with f.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _save_devices(instance_id: str, devices: list[dict[str, Any]]) -> None:
    d = _instance_dir(instance_id)
    d.mkdir(parents=True, exist_ok=True)
    with _devices_file(instance_id).open("w", encoding="utf-8") as fh:
        json.dump(devices, fh, indent=2, ensure_ascii=False)


def get_devices(instance_id: str) -> list[dict[str, Any]]:
    return _load_devices(instance_id)


def link_device(instance_id: str, device_data: dict[str, Any]) -> dict[str, Any]:
    devices = _load_devices(instance_id)
    # Check if already linked
    for d in devices:
        if d["device_id"] == device_data["device_id"]:
            d.update(device_data)
            _save_devices(instance_id, devices)
            return d
    device = {
        "device_id": device_data["device_id"],
        "device_name": device_data["device_name"],
        "source": device_data.get("source", "device"),
        "platform_device_id": device_data.get("platform_device_id"),
        "category": device_data.get("category", "Device"),
        "measurement_mappings": device_data.get("measurement_mappings", []),
    }
    devices.append(device)
    _save_devices(instance_id, devices)
    return device


def unlink_device(instance_id: str, device_id: str) -> bool:
    devices = _load_devices(instance_id)
    new_devices = [d for d in devices if d["device_id"] != device_id]
    if len(new_devices) == len(devices):
        return False
    _save_devices(instance_id, new_devices)
    return True


def save_measurement_mappings(instance_id: str, device_id: str, mappings: list[dict[str, Any]]) -> bool:
    devices = _load_devices(instance_id)
    for d in devices:
        if d["device_id"] == device_id:
            d["measurement_mappings"] = mappings
            _save_devices(instance_id, devices)
            return True
    return False


# ── Seed ──
def seed_default_instance(agent_id: str) -> str:
    """Create the default IRC5 instance if not already present. Returns instance_id."""
    # Check if already exists
    for p in INSTANCES_DIR.iterdir() if INSTANCES_DIR.exists() else []:
        if not p.is_dir():
            continue
        meta = _load_meta(p.name)
        if meta and meta.get("id") == "irc5-default-instance":
            return meta["id"]

    instance_id = "irc5-default-instance"
    d = _instance_dir(instance_id)
    d.mkdir(parents=True, exist_ok=True)

    meta = {
        "id": instance_id,
        "agent_id": agent_id,
        "name": "IRC5",
        "description": "Troubleshooting knowledge graph for the ABB IRC5 robot controller. Covers controller power, FlexPendant, control modules, drive modules, motors, gearboxes, and related maintenance diagnostics.",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_meta(instance_id, meta)

    # Copy ontology seed, preferring the dedicated IRC5 source when available.
    src_ont = BASE_DIR / "irc5_abb_robotics_V0.json"
    if not src_ont.exists():
        src_ont = BASE_DIR / "ontology.json"
    if src_ont.exists():
        shutil.copy2(src_ont, d / "ontology.json")

    # Copy symptom_embeddings.json from troubleshooting_agent
    src_emb = SEED_DIR / "symptom_embeddings.json"
    if src_emb.exists():
        shutil.copy2(src_emb, d / "symptom_embeddings.json")

    # Copy telemetry
    src_tel_dir = SEED_DIR / "telemetry"
    if src_tel_dir.exists():
        tel_dir = d / "telemetry"
        if not tel_dir.exists():
            shutil.copytree(src_tel_dir, tel_dir)

    return instance_id
