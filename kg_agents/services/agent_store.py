from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kg_agents.config import DATA_DIR, BASE_DIR


AGENTS_FILE = DATA_DIR / "agents.json"


def _load_agents() -> list[dict[str, Any]]:
    if not AGENTS_FILE.exists():
        return []
    with AGENTS_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_agents(agents: list[dict[str, Any]]) -> None:
    with AGENTS_FILE.open("w", encoding="utf-8") as f:
        json.dump(agents, f, indent=2, ensure_ascii=False)


def list_agents() -> list[dict[str, Any]]:
    agents = _load_agents()
    # Count instances per agent
    instances_dir = DATA_DIR / "instances"
    for agent in agents:
        count = 0
        if instances_dir.exists():
            for p in instances_dir.iterdir():
                if p.is_dir():
                    meta_file = p / "meta.json"
                    if meta_file.exists():
                        with meta_file.open("r", encoding="utf-8") as f:
                            meta = json.load(f)
                        if meta.get("agent_id") == agent["id"]:
                            count += 1
        agent["instance_count"] = count
    return agents


def get_agent(agent_id: str) -> dict[str, Any] | None:
    agents = _load_agents()
    for a in agents:
        if a["id"] == agent_id:
            return a
    return None


def create_agent(data: dict[str, Any]) -> dict[str, Any]:
    agents = _load_agents()
    agent = {
        "id": str(uuid.uuid4()),
        "name": data["name"],
        "description": data.get("description", ""),
        "ontology_schema": data.get("ontology_schema", {}),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    agents.append(agent)
    _save_agents(agents)
    return agent


def delete_agent(agent_id: str) -> bool:
    agents = _load_agents()
    new_agents = [a for a in agents if a["id"] != agent_id]
    if len(new_agents) == len(agents):
        return False
    _save_agents(new_agents)
    return True


def seed_default_agent() -> str:
    """Create the default Maintenance Troubleshooting Agent if not already present. Returns agent_id."""
    agents = _load_agents()
    for a in agents:
        if a.get("name") == "Maintenance Troubleshooting Agent":
            return a["id"]

    # Load default ontology schema
    schema_path = BASE_DIR / "ontology_schema.JSON"
    schema = {}
    if schema_path.exists():
        with schema_path.open("r", encoding="utf-8") as f:
            schema = json.load(f)

    agent = {
        "id": "maintenance-agent-default",
        "name": "Maintenance Troubleshooting Agent",
        "description": "Knowledge-grounded troubleshooting agent for maintenance diagnostics. Uses ontology-based reasoning to match symptoms to failure modes and corrective actions.",
        "ontology_schema": schema,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    agents.append(agent)
    _save_agents(agents)
    return agent["id"]
