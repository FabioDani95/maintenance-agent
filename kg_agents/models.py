from __future__ import annotations

from datetime import datetime
from typing import Literal
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


class PathStatsSummary(BaseModel):
    instance_id: str
    path_key: str
    symptom_ids: list[str] = Field(default_factory=list)
    final_failure_mode_id: str
    final_path: list[str] = Field(default_factory=list)
    selected_action_id: str
    total_uses: int = 0
    resolved_count: int = 0
    partially_resolved_count: int = 0
    not_resolved_count: int = 0
    escalated_count: int = 0
    total_duration_sec: int = 0
    avg_duration_min: float = 0.0
    success_rate_pct: float = 0.0
    last_outcome_at: str = ""


class ActionOption(BaseModel):
    action_id: str
    action_name: str
    instruction_text: str = ""
    source_title: str = ""
    source_reference: str = ""
    path_key: str
    final_path: list[str] = Field(default_factory=list)
    stats: PathStatsSummary | None = None


class CurrentIssue(BaseModel):
    failure_mode_id: str
    failure_mode_name: str
    component_id: str = ""
    component_name: str = ""
    symptom_ids: list[str] = Field(default_factory=list)
    action_options: list[ActionOption] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    highlight: dict[str, Any] = Field(default_factory=dict)
    has_more_issues: bool = False
    issue_number: int | None = None
    total_issues: int | None = None
    telemetry: dict[str, Any] | None = None
    current_issue: CurrentIssue | None = None


class NextIssueRequest(BaseModel):
    session_id: str
    model: str | None = None


class ResetRequest(BaseModel):
    session_id: str | None = None


class OutcomeLogRequest(BaseModel):
    session_id: str
    selected_action_id: str | None = None
    outcome: Literal["resolved", "partially_resolved", "not_resolved", "escalated"]
    user_feedback: str = ""


class InterventionRecord(BaseModel):
    id: int
    instance_id: str
    session_id: str
    created_at: str
    updated_at: str
    started_at: str | None = None
    duration_sec: int = 0
    ontology_version: str | None = None
    ontology_hash: str | None = None
    symptom_ids: list[str] = Field(default_factory=list)
    final_failure_mode_id: str
    final_path: list[str] = Field(default_factory=list)
    path_key: str
    selected_action_id: str
    outcome: Literal["resolved", "partially_resolved", "not_resolved", "escalated"]
    user_queries: list[str] = Field(default_factory=list)
    user_feedback: str = ""


class OutcomeLogResponse(BaseModel):
    ok: bool = True
    intervention: InterventionRecord
    stats: PathStatsSummary


class PathStatsResponse(BaseModel):
    stats: list[PathStatsSummary] = Field(default_factory=list)


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


# ── Extraction Summary ──
class ExtractionContext(BaseModel):
    kg_id: str
    version: str = ""
    ontology_ref: str = ""
    extraction_timestamp: str = ""


class ExtractionPerformance(BaseModel):
    status: str = ""
    triplets_validated: int = 0
    total_automation_time: str = ""
    estimated_cost_usd: float = 0.0
    llm_tokens: int = 0


class ExtractionModelUsage(BaseModel):
    primary_model: str = ""
    secondary_model: str = ""


class ExtractionFileLinks(BaseModel):
    meta: str = ""
    ontology: str = ""


class ExtractionSummary(BaseModel):
    context: ExtractionContext
    extraction_performance: ExtractionPerformance = Field(default_factory=ExtractionPerformance)
    model_usage: ExtractionModelUsage = Field(default_factory=ExtractionModelUsage)
    file_links: ExtractionFileLinks = Field(default_factory=ExtractionFileLinks)


# ── All Instances (cross-agent) ──
class InstanceWithAgent(BaseModel):
    id: str
    agent_id: str
    agent_name: str = ""
    name: str
    description: str = ""
    created_at: str = ""
    updated_at: str = ""
    node_count: int = 0
    relationship_count: int = 0


class AllInstancesResponse(BaseModel):
    instances: list[InstanceWithAgent]
