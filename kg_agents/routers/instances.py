from __future__ import annotations

from fastapi import APIRouter, HTTPException

from kg_agents.models import Instance, InstanceCreate, InstanceUpdate, InstanceResponse
from kg_agents.services import agent_store, instance_store

router = APIRouter(prefix="/v1/kg-agents", tags=["instances"])


@router.get("/agents/{agent_id}/instances", response_model=InstanceResponse)
async def list_instances(agent_id: str):
    agent = agent_store.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    instances = instance_store.list_instances(agent_id)
    return {"instances": instances}


@router.post("/agents/{agent_id}/instances", response_model=Instance, status_code=201)
async def create_instance(agent_id: str, data: InstanceCreate):
    agent = agent_store.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    ontology_data = data.model_dump().get("ontology_data")
    if ontology_data and agent.get("ontology_schema"):
        errors = instance_store.validate_ontology_data(ontology_data, agent["ontology_schema"])
        if errors:
            raise HTTPException(status_code=422, detail={"validation_errors": errors})
    instance = instance_store.create_instance(agent_id, data.model_dump())
    return instance


@router.get("/instances/{instance_id}", response_model=Instance)
async def get_instance(instance_id: str):
    instance = instance_store.get_instance(instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    return instance


@router.put("/instances/{instance_id}", response_model=Instance)
async def update_instance(instance_id: str, data: InstanceUpdate):
    update_data = data.model_dump(exclude_unset=True)
    ontology_data = update_data.get("ontology_data")
    if ontology_data:
        meta = instance_store.get_instance(instance_id)
        if not meta:
            raise HTTPException(status_code=404, detail="Instance not found")
        agent = agent_store.get_agent(meta["agent_id"])
        if agent and agent.get("ontology_schema"):
            errors = instance_store.validate_ontology_data(ontology_data, agent["ontology_schema"])
            if errors:
                raise HTTPException(status_code=422, detail={"validation_errors": errors})
    instance = instance_store.update_instance(instance_id, update_data)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    return instance


@router.delete("/instances/{instance_id}", status_code=204)
async def delete_instance(instance_id: str):
    if not instance_store.delete_instance(instance_id):
        raise HTTPException(status_code=404, detail="Instance not found")
