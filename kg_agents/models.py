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
    mode: Literal["fast", "non-fast"] | None = None
    behavior_mode: Literal["solve_current_problem", "search_past_events"] | None = None
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


class ClarificationOption(BaseModel):
    id: str
    label: str
    description: str = ""


class PastCaseResolution(BaseModel):
    log_id: str
    occurred_at: str | None = None
    work_order_id: str | None = None
    action_taken: str = ""
    outcome: str = ""


class RankedLogRef(BaseModel):
    log_id: str
    similarity: float = 0.0


class PastCasesSummary(BaseModel):
    top_event_signature_id: str
    matched_signatures: int = 0
    occurrence_count: int = 0
    resolved_count: int = 0
    partially_resolved_count: int = 0
    not_resolved_count: int = 0
    escalated_count: int = 0
    unknown_outcome_count: int = 0
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    most_used_resolution: PastCaseResolution | None = None
    sample_resolutions: list[PastCaseResolution] = Field(default_factory=list)
    top_log_id: str | None = None
    # Narrative produced by an LLM from the top-K basis logs (~2-4 sentences).
    # The card renders this in place of the old "Most-used fix" one-liner.
    narrative_summary: str = ""
    # log_ids the LLM was given as the basis for `narrative_summary` (top K by
    # similarity to the user query). The frontend highlights these rows in the
    # log navigator.
    basis_log_ids: list[str] = Field(default_factory=list)
    # All matching rows ordered by similarity to the query (descending). Used
    # by the log navigator to sort rows by relevance instead of by date.
    ranked_log_refs: list[RankedLogRef] = Field(default_factory=list)


class PastCasesAnalysisRequest(BaseModel):
    session_id: str | None = None
    query: str
    log_ids: list[str] = Field(default_factory=list)
    date_from: str | None = None
    date_to: str | None = None
    maintenance_type: str | None = None
    event_category: str | None = None
    status: str | None = None
    severity_min: int | None = None
    limit: int = 3


class PastCasesAnalysisResponse(BaseModel):
    analysis_markdown: str
    log_ids_used: list[str] = Field(default_factory=list)
    past_cases_summary: PastCasesSummary | None = None
    log_evidence: list[dict[str, Any]] = Field(default_factory=list)
    timings: dict[str, float] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    highlight: dict[str, Any] = Field(default_factory=dict)
    has_more_issues: bool = False
    issue_number: int | None = None
    total_issues: int | None = None
    telemetry: dict[str, Any] | None = None
    current_issue: CurrentIssue | None = None
    awaiting_clarification: bool = False
    clarification_question: str | None = None
    clarification_options: list[ClarificationOption] = Field(default_factory=list)
    intent: str | None = None
    behavior_mode: Literal["solve_current_problem", "search_past_events"] | None = None
    log_evidence: list[dict[str, Any]] = Field(default_factory=list)
    past_cases_summary: PastCasesSummary | None = None
    timings: dict[str, float] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)


class NextIssueRequest(BaseModel):
    session_id: str
    mode: Literal["fast", "non-fast"] | None = None
    model: str | None = None


class RecommendRequest(BaseModel):
    session_id: str


class RecommendResponse(BaseModel):
    instance_id: str
    session_id: str
    recommendation_markdown: str
    model: str
    timing_s: float


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
    secondary_model: str | None = None


class ExtractionFileLinks(BaseModel):
    meta: str = ""
    ontology: str = ""


class ExtractionSummary(BaseModel):
    context: ExtractionContext
    extraction_performance: ExtractionPerformance = Field(default_factory=ExtractionPerformance)
    model_usage: ExtractionModelUsage = Field(default_factory=ExtractionModelUsage)
    node_counts: dict[str, int] = Field(default_factory=dict)
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


# ── Chat Logs ──
class ChatLogEntry(BaseModel):
    id: int
    instance_id: str
    session_id: str
    role: str
    content: str
    created_at: str
    payload: dict[str, Any] | None = None


class ChatSessionSummary(BaseModel):
    session_id: str
    started_at: str
    last_message_at: str
    message_count: int
    first_user_message: str = ""
    behavior_mode: Literal["solve_current_problem", "search_past_events"] | None = None


class ChatSessionsResponse(BaseModel):
    sessions: list[ChatSessionSummary]


class ChatSessionMessagesResponse(BaseModel):
    messages: list[ChatLogEntry]


class LogRecord(BaseModel):
    log_id: str
    source_system: str | None = None
    source_record_id: str | None = None
    occurred_at: str | None = None
    observed_at: str | None = None
    instance_id: str
    asset_id: str | None = None
    device_id: str | None = None
    equipment_tag: str | None = None
    location: str | None = None
    event_name: str | None = None
    event_category: str | None = None
    maintenance_type: str | None = None
    status: str | None = None
    severity_number: int | None = None
    severity_text: str | None = None
    component_id: str | None = None
    component_name_raw: str | None = None
    error_code: str | None = None
    alarm_code: str | None = None
    signal_name: str | None = None
    observed_value: float | None = None
    observed_unit: str | None = None
    threshold_value: float | None = None
    threshold_unit: str | None = None
    work_order_id: str | None = None
    title: str | None = None
    body: str | None = None
    action_taken: str | None = None
    outcome: str | None = None
    planned_duration_min: int | None = None
    actual_duration_min: int | None = None
    downtime_min: int | None = None
    semantic_text: str | None = None
    event_signature_id: str | None = None
    linked_failure_mode_id: str | None = None
    linked_symptom_id: str | None = None
    quality_flags: list[str] = Field(default_factory=list)
    attributes_json: dict[str, Any] = Field(default_factory=dict)


class LogListResponse(BaseModel):
    instance_id: str
    total: int
    limit: int
    offset: int
    items: list[LogRecord]


class LogSummaryResponse(BaseModel):
    instance_id: str
    row_count: int
    top_event_signatures: list[dict[str, Any]] = Field(default_factory=list)
    top_components: list[dict[str, Any]] = Field(default_factory=list)
    severity_distribution: dict[str, int] = Field(default_factory=dict)
    events_by_month: dict[str, int] = Field(default_factory=dict)
    open_events: int = 0
    downtime_by_component_min: dict[str, int] = Field(default_factory=dict)


class LogSearchRequest(BaseModel):
    query: str
    date_from: str | None = None
    date_to: str | None = None
    component_id: str | None = None
    linked_failure_mode_id: str | None = None
    maintenance_type: str | None = None
    event_category: str | None = None
    event_signature_id: str | None = None
    status: str | None = None
    severity_min: int | None = None
    limit: int = 5
    use_llm_rerank: bool = True


class LogSearchMatch(BaseModel):
    event_signature_id: str
    score: float
    occurrence_count: int
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    linked_failure_mode_id: str = ""
    linked_symptom_id: str = ""
    top_match_log: LogRecord
    most_recent_log: LogRecord
    all_log_ids: list[str] = Field(default_factory=list)
    rerank_rationale: str | None = None


class LogSearchResponse(BaseModel):
    query: str
    instance_id: str
    match_count: int
    matches: list[LogSearchMatch]
    diagnostics: dict[str, Any] = Field(default_factory=dict)
