from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Agent ──
class AgentCreate(BaseModel):
    name: str
    description: str = ""
    ontology_schema: dict[str, Any] = Field(default_factory=dict)


class Agent(BaseModel):
    id: str
    name: str
    description: str = ""
    ontology_schema: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    instance_count: int = 0


class AgentResponse(BaseModel):
    agents: list[Agent]


# ── Instance ──
class InstanceCreate(BaseModel):
    name: str
    description: str = ""
    ontology_data: dict[str, Any] | None = None


class InstanceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    ontology_data: dict[str, Any] | None = None


class Instance(BaseModel):
    id: str
    agent_id: str
    name: str
    description: str = ""
    created_at: str = ""
    updated_at: str = ""
    node_count: int = 0
    relationship_count: int = 0


class InstanceResponse(BaseModel):
    instances: list[Instance]


# ── Device Link ──
class DeviceLinkCreate(BaseModel):
    device_id: str
    device_name: str
    source: str = "device"  # "device" (platform device) or "equipment" (manual entry)
    platform_device_id: str | None = None
    category: str = "Device"  # "Device" or "Equipment"


class MeasurementMapping(BaseModel):
    failure_mode_id: str
    measurement_name: str
    subscription_id: str | None = None
    subscription_name: str | None = None


class DeviceLink(BaseModel):
    device_id: str
    device_name: str
    source: str = "device"
    platform_device_id: str | None = None
    category: str = "Device"
    measurement_mappings: list[MeasurementMapping] = Field(default_factory=list)


class MeasurementMappingBatch(BaseModel):
    mappings: list[MeasurementMapping]


# ── Chat ──
class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    model: str | None = None


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    highlight: dict[str, Any] = Field(default_factory=dict)
    has_more_issues: bool = False
    issue_number: int | None = None
    total_issues: int | None = None
    telemetry: dict[str, Any] | None = None


class NextIssueRequest(BaseModel):
    session_id: str
    model: str | None = None


class ResetRequest(BaseModel):
    session_id: str | None = None


# ── Graph ──
class GraphNode(BaseModel):
    id: str
    label: str
    group: str
    title: str
    description: str = ""
    severity: str = ""


class GraphEdge(BaseModel):
    id: str
    from_id: str = Field(alias="from")
    to_id: str = Field(alias="to")
    label: str

    model_config = {"populate_by_name": True}


class GraphDataResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    node_types: list[str]
    edge_types: list[str]
    color_map: dict[str, str]


# ── Product Info ──
class ProductInfoResponse(BaseModel):
    product_name: str
    product_short_name: str
    product_type: str
    domain_topics: list[str] = Field(default_factory=list)
    suggested_symptoms: list[dict[str, str]] = Field(default_factory=list)


# ── Status ──
class StatusResponse(BaseModel):
    ok: bool
    ontology_version: str | None = None
    total_nodes: int | None = None
    total_relationships: int | None = None
