from __future__ import annotations

from fastapi import APIRouter, HTTPException

from kg_agents.models import Agent, AgentCreate, AgentResponse
from kg_agents.services import agent_store

router = APIRouter(prefix="/v1/kg-agents", tags=["agents"])


@router.get("/agents", response_model=AgentResponse)
async def list_agents():
    agents = agent_store.list_agents()
    return {"agents": agents}


@router.post("/agents", response_model=Agent, status_code=201)
async def create_agent(data: AgentCreate):
    agent = agent_store.create_agent(data.model_dump())
    return agent


@router.get("/agents/{agent_id}", response_model=Agent)
async def get_agent(agent_id: str):
    agent = agent_store.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.get("/agents/{agent_id}/schema")
async def get_agent_schema(agent_id: str):
    agent = agent_store.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent.get("ontology_schema", {})


@router.delete("/agents/{agent_id}", status_code=204)
async def delete_agent(agent_id: str):
    if not agent_store.delete_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
