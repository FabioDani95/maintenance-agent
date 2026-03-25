from __future__ import annotations

import json
from fastapi import APIRouter, HTTPException

from kg_agents.models import DeviceLinkCreate, DeviceLink, MeasurementMappingBatch
from kg_agents.services import instance_store

router = APIRouter(prefix="/v1/kg-agents", tags=["devices"])


@router.get("/instances/{instance_id}/devices")
async def get_devices(instance_id: str):
    inst = instance_store.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    devices = instance_store.get_devices(instance_id)
    return {"devices": devices}


@router.post("/instances/{instance_id}/devices", status_code=201)
async def link_device(instance_id: str, data: DeviceLinkCreate):
    inst = instance_store.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    device = instance_store.link_device(instance_id, data.model_dump())
    return device


@router.delete("/instances/{instance_id}/devices/{device_id}", status_code=204)
async def unlink_device(instance_id: str, device_id: str):
    inst = instance_store.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    if not instance_store.unlink_device(instance_id, device_id):
        raise HTTPException(status_code=404, detail="Device not linked")


@router.post("/instances/{instance_id}/devices/{device_id}/measurement-mappings")
async def save_measurement_mappings(instance_id: str, device_id: str, data: MeasurementMappingBatch):
    inst = instance_store.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    mappings = [m.model_dump() for m in data.mappings]
    if not instance_store.save_measurement_mappings(instance_id, device_id, mappings):
        raise HTTPException(status_code=404, detail="Device not linked")
    return {"ok": True}


@router.get("/instances/{instance_id}/failure-modes-measurements")
async def get_failure_modes_measurements(instance_id: str):
    """Return failure modes that have related_measurements, for mapping UI."""
    inst = instance_store.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")

    ont_path = instance_store.get_ontology_path(instance_id)
    if not ont_path.exists():
        return {"failure_modes": []}

    with ont_path.open("r", encoding="utf-8") as f:
        ont = json.load(f)

    result = []
    for fm in ont.get("nodes", {}).get("FailureMode", []):
        measurements = fm.get("related_measurements", [])
        if measurements:
            result.append({
                "failure_mode_id": fm["failure_mode_id"],
                "name": fm.get("name", fm["failure_mode_id"]),
                "description": fm.get("description", ""),
                "related_measurements": measurements,
            })
    return {"failure_modes": result}
