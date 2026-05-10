# Log Integration Design

## Context

`kg_agents` currently exposes a FastAPI backend for knowledge-grounded troubleshooting. Each machine or product is represented as an ontology instance under:

```text
kg_agents/data/instances/<instance_id>/
```

The current chat flow is centered on the knowledge graph:

1. user describes a problem;
2. the system retrieves matching symptoms or failure modes;
3. graph traversal returns possible causes and corrective actions;
4. telemetry is optionally attached when the selected failure mode exposes `related_measurements`.

This works for live troubleshooting, but it does not cover another important class of questions:

```text
Has this ever happened on this machine?
When did this last occur?
What was done last time?
How often does this issue recur?
Which components have the most repeated problems?
```

Those questions are not primarily graph-traversal questions. They are historical log and maintenance-record retrieval questions. Logs therefore need to be modeled as an instance-level memory that can be searched directly, and only optionally linked back to graph entities.

## Source Example

The example file `Mock_Log.csv` is a work-order style maintenance log, not a telemetry time-series. It contains fields such as:

- equipment id;
- work order id;
- order type;
- maintenance activity type;
- work order description;
- operation description;
- scheduled and actual dates;
- actual and planned work;
- order status.

It is useful as a mock source, but not as a final schema. It is multi-machine, contains dirty values, and has several issues that a future ingest pipeline must normalize:

- invalid or missing dates;
- non-normalized status values;
- equipment id gaps or typos;
- inconsistent work center names;
- outlier work durations;
- implicit component/failure information embedded only in free text.

For the MVP we should create a clean canonical CSV for IRC5, but design it as a simulation of a real future ingestion pipeline.

## Design Principles

1. Logs are not ontology nodes by default.

   `ontology.json` remains the stable troubleshooting knowledge graph. Logs are operational history and can grow to millions of rows. They should not be appended directly into the ontology.

2. A log can be searched without a failure mode.

   Historical questions must work even when no `failure_mode_id` is known. Free-text log search and semantic similarity are first-class capabilities.

3. Graph links are optional enrichments.

   Fields like `component_id`, `failure_mode_id`, `symptom_id`, and `error_code` are useful when present, but the log record remains valid without them.

4. CSV is an ingestion format, not the final query engine.

   For MVP the data can live as CSV. For scale, normalized event tables plus vector/sparse indexes should back the APIs.

5. Embeddings should be generated offline.

   Historical log search should use embeddings. They should be computed during ingest, preferably batch/offline, not during every query.

6. The system must scale to large log volumes.

   If a machine has 1M log rows, the graph UI should never render 1M log nodes. Search should retrieve a small relevant subset, and the graph should overlay only those results.

## Canonical CSV

For the IRC5 MVP, place logs at:

```text
kg_agents/data/instances/irc5-default-instance/logs/machine_logs.csv
```

Do not place this file under `telemetry/`. The existing telemetry loader expects wide numeric time-series CSV files with a `timestamp` column and measurement columns.

Recommended canonical columns:

```csv
log_id,
source_system,
source_record_id,
occurred_at,
observed_at,
instance_id,
asset_id,
device_id,
equipment_tag,
location,
event_name,
event_category,
maintenance_type,
status,
severity_number,
severity_text,
component_id,
component_name_raw,
error_code,
alarm_code,
signal_name,
observed_value,
observed_unit,
threshold_value,
threshold_unit,
work_order_id,
title,
body,
action_taken,
outcome,
planned_duration_min,
actual_duration_min,
downtime_min,
semantic_text,
event_signature_id,
linked_failure_mode_id,
linked_symptom_id,
quality_flags,
attributes_json
```

The CSV should be stored as a single header row with one row per event occurrence. The line breaks above are only for readability.

### Field Semantics

`log_id`
: Stable unique id for the normalized event occurrence.

`source_system`
: Origin of the record, for example `mock_cmms`, `irc5_event_log`, `erp`, `mes`, or `manual_import`.

`source_record_id`
: Original source id before normalization.

`occurred_at`
: Timestamp when the event happened. Use ISO 8601 UTC.

`observed_at`
: Timestamp when the system ingested or observed the event. This follows the same distinction used by structured logging models such as OpenTelemetry.

`instance_id`
: Knowledge graph instance id, for example `irc5-default-instance`.

`asset_id`
: Asset node id from the ontology, for example `asset_irc5`.

`device_id`
: Linked platform device id from `devices.json`, if available.

`equipment_tag`
: Human or plant tag, such as `IRC5-CTRL-01`.

`location`
: Cell, station, plant area, or building reference.

`event_name`
: Normalized event name, for example `ethernet_packet_loss`, `flexpendant_disconnected`, `drive_motor_overtemperature`.

`event_category`
: Broad category such as `alarm`, `fault`, `inspection`, `maintenance`, `operator_note`, `work_order`, or `condition_monitoring`.

`maintenance_type`
: `CM`, `PM`, `PDM`, or blank if not applicable.

`status`
: Normalized state such as `open`, `closed`, `released`, `completed`, `cancelled`, or `unknown`.

`severity_number`
: Numeric severity. Suggested mapping follows OpenTelemetry style ranges: `9-12` info, `13-16` warning, `17-20` error, `21-24` fatal.

`severity_text`
: Human severity label, for example `INFO`, `WARN`, `ERROR`, `FATAL`.

`component_id`
: Optional ontology component id, for example `comp_communications`.

`component_name_raw`
: Raw component text from the source system.

`error_code` / `alarm_code`
: Raw machine code if available.

`signal_name`
: Measurement or telemetry signal associated with the log event, for example `ethernet_packet_loss_pct`.

`observed_value`, `observed_unit`, `threshold_value`, `threshold_unit`
: Structured measurement context when the event is triggered by a numeric or boolean observation.

`work_order_id`
: Maintenance work order reference, if available.

`title`
: Short human-readable title.

`body`
: Main natural-language description.

`action_taken`
: What technicians or operators did.

`outcome`
: Result such as `resolved`, `partially_resolved`, `not_resolved`, `monitoring`, or blank.

`planned_duration_min`, `actual_duration_min`, `downtime_min`
: Duration fields for operational analytics.

`semantic_text`
: Explicit text prepared for embeddings and search. It should concatenate normalized context and important raw text. This field is intentionally redundant.

`event_signature_id`
: Stable normalized pattern id, such as `irc5_communications_ethernet_packet_loss`. Multiple occurrences can share the same signature.

`linked_failure_mode_id`
: Optional best-effort link to a failure mode in the ontology.

`linked_symptom_id`
: Optional best-effort link to a symptom in the ontology.

`quality_flags`
: Comma-separated data quality flags such as `missing_component`, `date_imputed`, `duration_outlier`, `unmapped_failure_mode`.

`attributes_json`
: Extra source-specific attributes as JSON.

## Example IRC5 Rows

```csv
log_id,source_system,source_record_id,occurred_at,observed_at,instance_id,asset_id,device_id,equipment_tag,location,event_name,event_category,maintenance_type,status,severity_number,severity_text,component_id,component_name_raw,error_code,alarm_code,signal_name,observed_value,observed_unit,threshold_value,threshold_unit,work_order_id,title,body,action_taken,outcome,planned_duration_min,actual_duration_min,downtime_min,semantic_text,event_signature_id,linked_failure_mode_id,linked_symptom_id,quality_flags,attributes_json
log_irc5_0001,mock_cmms,WO-IRC5-1001,2026-04-12T09:20:00Z,2026-04-12T09:21:00Z,irc5-default-instance,asset_irc5,691e2a2a29b1e258a42771f9,IRC5-CTRL-01,cell-a,ethernet_packet_loss,alarm,CM,open,17,ERROR,comp_communications,Communications,,,ethernet_packet_loss_pct,8.4,%,2.0,%,WO-IRC5-1001,Intermittent Ethernet communication loss,Ethernet packet loss above threshold during robot operation.,Inspect controller Ethernet cabling switch port AXC LED and link state.,monitoring,120,,35,IRC5 communications ethernet packet loss intermittent network problem high packet loss controller link AXC LED,irc5_communications_ethernet_packet_loss,fm_ethernet_network_has_problems,,,"{""raw_equipment_id"":""IRC5-CTRL-01""}"
log_irc5_0002,mock_cmms,WO-IRC5-1002,2026-04-13T14:05:00Z,2026-04-13T14:06:00Z,irc5-default-instance,asset_irc5,691e2a2a29b1e258a42771f9,IRC5-CTRL-01,cell-a,flexpendant_disconnected,alarm,CM,closed,13,WARN,comp_flexpendant,FlexPendant,,,flexpendant_connected,0,boolean,1,boolean,WO-IRC5-1002,FlexPendant intermittently disconnected,FlexPendant disconnected during operation and pendant power LED was not stable.,Reseat pendant connector and inspect cable strain relief.,resolved,60,48,15,IRC5 FlexPendant disconnected intermittent pendant power LED connector cable controller,irc5_flexpendant_disconnected,fm_flexpendant_not_connected_to_controller,,,"{}"
log_irc5_0003,mock_cmms,WO-IRC5-1003,2026-04-14T07:40:00Z,2026-04-14T07:41:00Z,irc5-default-instance,asset_irc5,691e2a2a29b1e258a42771f9,IRC5-CTRL-01,cell-a,drive_motor_overtemperature,alarm,CM,open,17,ERROR,comp_drive_module,Drive Module,,,drive_motor_temp_c,86.2,C,75,C,WO-IRC5-1003,Drive motor temperature high,Drive motor temperature above expected operating range during production cycle.,Check cabinet cooling fan airflow path and duty cycle.,monitoring,150,,45,IRC5 drive motor temperature high overheating cabinet cooling fan airflow duty cycle,irc5_drive_motor_overtemperature,fm_components_overheated,,,"{}"
```

## Runtime Data Model

The MVP can read from CSV, but the scalable conceptual model should be:

```text
event_occurrences
  log_id
  instance_id
  asset_id
  device_id
  occurred_at
  normalized structured fields
  raw/source fields

event_signatures
  event_signature_id
  instance_id
  canonical title/body/semantic_text
  occurrence_count
  first_seen_at
  last_seen_at
  embedding
  sparse/full-text index

event_links
  log_id or event_signature_id
  target_type: Asset | Component | Symptom | FailureMode | ErrorCode
  target_id
  confidence
  link_source: explicit | rule | embedding | llm
```

For a small MVP, these can be materialized in JSON or SQLite. For production-scale data, use a real store:

- relational database for structured filtering and aggregates;
- vector database or pgvector for embeddings;
- full-text/sparse index for exact terms, codes, and equipment tags.

## Embeddings Strategy

Embeddings should be used, but not naively.

### What to Embed

Embed `semantic_text`, not the entire raw CSV row. The text should include:

- asset/model context;
- event name;
- component name;
- error/alarm code;
- measurement name and abnormal value;
- title/body/action text;
- normalized technical terms.

Example:

```text
IRC5 communications ethernet packet loss intermittent network problem high packet loss controller link AXC LED. Action: inspect Ethernet cabling and switch port.
```

### Occurrences vs Signatures

For scale, prefer embedding event signatures.

If there are 1M log rows but many repeated patterns, build signatures such as:

```text
irc5_communications_ethernet_packet_loss
irc5_flexpendant_disconnected
irc5_drive_motor_overtemperature
irc5_brake_release_fault
```

Then:

- embed the signature once;
- keep all individual occurrences in structured storage;
- use occurrences for counts, dates, downtime, status, and work orders.

For rare unique events, a signature may map one-to-one with a log row.

### Model Choice

Use `text-embedding-3-small` for MVP and initial production unless evaluation shows it is insufficient. It is designed for search and supports dimension reduction through the `dimensions` parameter.

For large backfills, use the OpenAI Batch API so embeddings are generated asynchronously with lower cost and higher throughput.

## Retrieval Strategy

Use hybrid retrieval, not vector search alone.

Log questions often contain exact tokens that matter:

- error codes;
- work order ids;
- equipment tags;
- signal names;
- component names.

Dense embeddings are useful for semantic similarity:

```text
Has the pendant ever lost connection?
```

should match:

```text
FlexPendant intermittently disconnected during operation.
```

Sparse/full-text search is useful for exact matching:

```text
WO-IRC5-1001
ethernet_packet_loss_pct
DSQC 662
```

Recommended query flow:

```text
user query
  -> intent router
  -> extract filters and entities
  -> run structured filters
  -> run sparse keyword search
  -> run dense embedding search
  -> fuse/rerank candidates
  -> aggregate occurrences
  -> generate grounded answer
```

Reciprocal rank fusion is a reasonable first approach to combine sparse and dense candidate sets.

## Agent Behavior

The existing chat endpoint should gain an intent-routing step before the current troubleshooting pipeline.

### Intent Types

`troubleshooting_current`
: User describes a current issue and asks for diagnosis or corrective action. Use existing KG flow.

`log_history_search`
: User asks whether something happened before, when it happened, or what was done. Use log retrieval first.

`log_analytics`
: User asks about frequency, recurrence, most common issues, worst components, downtime, or trends. Use structured aggregates.

`hybrid_diagnosis_with_history`
: User describes a current problem and asks whether it resembles past events. Use both KG and log search.

`work_order_lookup`
: User asks who worked on it, how long it took, what work order was involved, or what action was taken. Use logs/work orders.

### Example Questions and Route

```text
"Has this Ethernet issue ever happened on this IRC5?"
-> log_history_search

"The FlexPendant is not powering up, what should I check?"
-> troubleshooting_current

"Does this look like something we have already seen?"
-> hybrid_diagnosis_with_history

"Which IRC5 component has the most repeated warnings?"
-> log_analytics

"What did we do last time the drive module overheated?"
-> log_history_search or work_order_lookup
```

### Response Shape

Historical responses should include evidence:

```text
Yes. I found 3 similar IRC5 events.

Most recent:
- 2026-04-12 09:20 UTC
- Intermittent Ethernet communication loss
- Severity: ERROR
- Work order: WO-IRC5-1001
- Action: inspected Ethernet cabling, switch port, AXC LED and controller link state

Other similar cases:
1. 2026-03-28 - FlexPendant disconnected intermittently
2. 2026-02-19 - Main computer event log error count high
```

The assistant should not force every historical answer into a failure-mode diagnosis. If a graph link exists, it can say:

```text
This is related to the ontology failure mode "Ethernet network has problems".
```

but that link is supporting context, not the primary retrieval key.

## Graph Integration Without Ontology Changes

Do not write log events into `ontology.json`.

Instead, extend graph responses with an optional runtime overlay.

Example API:

```http
GET /v1/kg-agents/instances/{instance_id}/graph-data?include_logs=true&q=ethernet&limit_logs=20
```

Behavior:

1. Load normal ontology graph as today.
2. If `include_logs=true`, query log search.
3. Add only the relevant top log/signature nodes to the response.
4. Add virtual edges to existing ontology nodes when known.

Virtual node example:

```json
{
  "id": "log_irc5_0001",
  "label": "Ethernet packet loss",
  "group": "LogEvent",
  "title": "ERROR - 2026-04-12 - WO-IRC5-1001",
  "description": "Ethernet packet loss above threshold during robot operation."
}
```

Virtual edges:

```json
[
  {"from": "log_irc5_0001", "to": "asset_irc5", "label": "LOG_FOR_ASSET"},
  {"from": "log_irc5_0001", "to": "comp_communications", "label": "OBSERVED_ON"},
  {"from": "log_irc5_0001", "to": "fm_ethernet_network_has_problems", "label": "SIMILAR_TO_FAILURE_MODE"}
]
```

The graph UI should never request all logs by default. It should request a filtered overlay based on query, selected node, selected date range, or current chat result.

## API Plan

Add APIs under the existing versioned contract:

```http
GET /v1/kg-agents/instances/{instance_id}/logs
POST /v1/kg-agents/instances/{instance_id}/log-search
GET /v1/kg-agents/instances/{instance_id}/logs/summary
GET /v1/kg-agents/instances/{instance_id}/logs/{log_id}
```

### `GET /logs`

Structured listing with filters:

```text
q
event_category
maintenance_type
status
severity_min
component_id
linked_failure_mode_id
date_from
date_to
limit
offset
```

### `POST /log-search`

Semantic and hybrid search:

```json
{
  "query": "Has Ethernet packet loss happened before?",
  "date_from": null,
  "date_to": null,
  "component_id": null,
  "limit": 10,
  "include_occurrences": true
}
```

Response:

```json
{
  "query": "...",
  "matches": [
    {
      "event_signature_id": "irc5_communications_ethernet_packet_loss",
      "score": 0.87,
      "occurrence_count": 3,
      "last_seen_at": "2026-04-12T09:20:00Z",
      "latest_log": {...},
      "linked_failure_mode_id": "fm_ethernet_network_has_problems"
    }
  ]
}
```

### `GET /logs/summary`

Aggregates:

```text
top_event_signatures
top_components
events_by_month
open_events
downtime_by_component
severity_distribution
```

## MVP Implementation Plan

### Phase 1 - Data Contract

1. Create `kg_agents/data/instances/irc5-default-instance/logs/machine_logs.csv`.
2. Seed realistic IRC5 rows using the canonical schema.
3. Include explicit examples for:
   - Ethernet communication issue;
   - FlexPendant disconnect;
   - controller power supply issue;
   - overheating;
   - brake release problem;
   - DSQC module fault;
   - calibration or TCP drift;
   - repeated preventive/predictive maintenance cases.

### Phase 2 - Loader and Normalization

1. Add `kg_agents/engine/log_loader.py`.
2. Parse CSV with strict field normalization.
3. Validate required fields:
   - `log_id`;
   - `occurred_at`;
   - `instance_id`;
   - `asset_id`;
   - `event_name`;
   - `body` or `semantic_text`.
4. Return warnings for quality issues rather than failing the whole file.

### Phase 3 - Local Search MVP

1. Implement deterministic keyword search over `semantic_text`, `title`, `body`, `action_taken`, codes, and component names.
2. Add optional local embedding file:

   ```text
   kg_agents/data/instances/<instance_id>/logs/log_embeddings.json
   ```

3. Use current OpenAI embedding helper to embed query text.
4. Rank results by:
   - structured filters;
   - exact code/tag matches;
   - keyword overlap;
   - embedding similarity;
   - recency boost when relevant.

### Phase 4 - APIs

1. Add `kg_agents/routers/logs.py`.
2. Register it in `kg_agents/main.py`.
3. Expose:
   - `GET /logs`;
   - `POST /log-search`;
   - `GET /logs/summary`.

### Phase 5 - Chat Intent Router

1. Add a deterministic first-pass intent classifier.
2. Route historical questions to log search.
3. Keep current troubleshooting behavior unchanged for normal diagnosis.
4. Add hybrid mode when user asks about a current issue plus prior occurrences.

### Phase 6 - Graph Overlay

1. Extend `graph-data` with optional query params:

   ```text
   include_logs=false
   log_query=
   limit_logs=20
   ```

2. Add `LogEvent` or `LogSignature` virtual nodes only in the response payload.
3. Add virtual edge labels without updating ontology schema.

### Phase 7 - Scale Path

When CSV/local JSON is no longer enough:

1. Move occurrences to SQLite/Postgres.
2. Add full-text index for exact terms and codes.
3. Move embeddings to Qdrant or pgvector.
4. Partition by `instance_id`, time, and possibly asset.
5. Store embeddings for signatures first, occurrences second only when needed.
6. Run embedding generation as background/batch jobs.

## Open Questions

1. Should `event_signature_id` be generated deterministically from normalized fields, or curated manually during mock-data creation?
2. Should the frontend expose a separate "History" panel, or should historical answers only appear through chat?
3. Should work orders and machine logs be one table in MVP, or separate tables joined by `work_order_id`?
4. Should log-to-KG linking be rule-based first, embedding-based first, or manually seeded for IRC5?
5. What is the minimum set of historical questions the demo must answer convincingly?

## Recommended MVP Position

For this project, the best MVP is:

```text
canonical IRC5 log CSV
  + local loader
  + hybrid search over semantic_text
  + chat intent routing
  + optional graph overlay for top results
```

This keeps the ontology clean, supports questions beyond troubleshooting, demonstrates a realistic future ingestion flow, and leaves a clear path to production-scale log volumes.

## References

- OpenTelemetry Logs Data Model: https://opentelemetry.io/docs/specs/otel/logs/data-model/
- OpenAI Embeddings Guide: https://developers.openai.com/api/docs/guides/embeddings
- OpenAI Batch API Guide: https://developers.openai.com/api/docs/guides/batch
- Qdrant Hybrid Queries: https://qdrant.tech/documentation/search/hybrid-queries/
- pgvector documentation: https://access.crunchydata.com/documentation/pgvector/0.8.1/pdf/
