# Log Integration Design

## Implementation Status

Implemented end-to-end on the IRC5 default instance. Six incremental phases shipped:

| Phase | Deliverable | Files |
|-------|-------------|-------|
| 1 — Data seed | 277-row canonical CSV + embedding index (3072-dim, text-embedding-3-large) | `kg_agents/scripts/{generate_irc5_logs,verify_log_links,embed_logs}.py`, `kg_agents/scripts/log_seed_plan.json`, `kg_agents/data/instances/irc5-default-instance/logs/{machine_logs.csv,log_embeddings.json}` |
| 2 — Engine | Hybrid retrieval (dense large + sparse TF-IDF + RRF + optional LLM rerank) and analytics | `kg_agents/engine/log_loader.py`, `kg_agents/engine/log_search.py` |
| 3 — HTTP API | Four endpoints under the existing versioned contract | `kg_agents/routers/logs.py` |
| 4 — Chat intent routing | Deterministic fast-path router with LLM fallback + four log handlers; existing KG flow preserved | `kg_agents/engine/intent_router.py`, `kg_agents/engine/log_chat.py`, `kg_agents/routers/chat.py` |
| 5 — Graph overlay | Opt-in `LogEvent` virtual nodes + three virtual edge types, no ontology mutation | `kg_agents/routers/graph.py` |
| 6 — Fast UX | Fast mode default, structured response templates, timing instrumentation, curated suggested questions | `kg_agents/config.py`, `kg_agents/models.py`, `kg_agents/dev_ui/*`, `kg_agents/routers/chat.py` |

Frontend (`kg_agents/dev_ui/`) extended with: Fast model default, curated suggested question chips, structured markdown rendering for Fast templates, intent badge, expandable Evidence panel, graph overlay toolbar, and auto-sync of overlay query when chat returns evidence. Diamond-shaped LogEvent nodes (teal `#14B8A6`) distinguish history from ontology.

### Five intents handled by the chat router

```text
troubleshooting_current        -> existing KG flow (unchanged)
log_history_search             -> log retrieval + Fast structured template, or LLM-composed reply outside Fast
log_analytics                  -> deterministic summary aggregation + Fast structured template, or LLM-composed reply outside Fast
work_order_lookup              -> exact WO lookup when possible + Fast structured template, or LLM-composed reply outside Fast
hybrid_diagnosis_with_history  -> KG flow PLUS a "Past similar events" appendix +
                                  evidence list, stashed across clarification turns
```

### Latency notes

The original log-chat path paid for three expensive stages on many turns: LLM intent classifier, log retrieval embedding, and LLM compose. That produced ~9-45s responses depending on branch and network/model latency.

Current Fast mode changes that shape:

- `OPENAI_CHAT_MODEL` defaults to `gpt-5-nano`.
- Common routes are classified by `classify_intent_fast` without an LLM call.
- KG query embeddings are deferred until the selected route actually needs the KG.
- `log_history_search` skips LLM rerank and LLM compose in Fast mode; it uses RRF plus a structured template.
- `log_analytics` uses aggregate counters plus a structured template; no model call is needed.
- `work_order_lookup` with an explicit `WO-*` id uses direct row lookup plus a structured template.
- Each `ChatResponse` includes `timings`, so regressions are visible per stage (`intent_fast_path_s`, `log_history_search_total_s`, `query_embedding_s`, `rerank_alignment_s`, `total_s`, etc.).

Observed smoke-test timings on the local IRC5 seed after the Fast-mode change:

| Branch | Typical Fast result |
|--------|---------------------|
| `log_analytics` | ~0.005-0.18s |
| exact `work_order_lookup` | ~0.005s |
| `log_history_search` | ~0.15-1.3s, mostly query embedding latency |
| `hybrid_diagnosis_with_history` | ~2.5-3s when KG rerank/alignment and clarification are involved |

Guided modes keep the same retrieval improvements, but can still call the LLM composer and therefore remain slower.

### Open questions: resolved

1. **`event_signature_id` deterministic vs curated** → curated for IRC5 demo via `log_seed_plan.json`. A deterministic slugify is fine for real ingest, but a stable per-pattern id lets the demo show recurring events cleanly.
2. **Frontend: separate History panel vs in-chat** → in-chat. An expandable Evidence panel under each assistant message + an intent badge above. The graph overlay is the only "out of chat" surface and is opt-in.
3. **Work orders vs machine logs: one table or two** → one. `work_order_id` is an optional column on the canonical log row. Sparse search picks WO ids exactly via TF-IDF; a separate WO table would not improve retrieval at MVP scale.
4. **Log → KG linking strategy** → LLM seed + embedding verification. The generator seeds `linked_failure_mode_id` per scenario, then `verify_log_links.py` recomputes the match with `text-embedding-3-large` and overrides only when the embedding strongly disagrees (delta > 0.04 and score > the FM threshold). Quality flags (`link_inferred_by_embedding`, `link_replaced_by_embedding`) make the decision auditable. About 26% of links were embedding-replaced in the IRC5 seed; spot checks showed most replacements were genuinely more accurate than the LLM seed.
5. **Minimum convincing demo set** -> covered by 10 canonical queries in `kg_agents/scripts/smoke_test_log_search.py` and 15 broader checks in the integration test suite - see "Test Plan" below.

### Test plan and current results

Two complementary suites live under `kg_agents/scripts/`:

- `smoke_test_log_search.py` — 10 canonical queries against the hybrid retrieval module, useful for inspecting retrieval quality in isolation.
- `test_log_integration_suite.py` — 15 end-to-end checks against the running FastAPI app (TestClient, no live server needed).

Coverage in the end-to-end suite:

- intent classification across the five intents (allowing analytics/history overlap on ambiguous wording)
- retrieval top-1 / top-2 accuracy for eight historical questions
- exact work order id surfacing for three known WOs
- reply grounding (must cite dates, WO ids, action keywords, counts)
- severity filter behaviour, no-match path, operator-note signature (no KG link)
- KG flow not regressed by intent routing
- multi-turn hybrid + clarification: appendix attaches only to the final answer
- `GET /logs/summary`, `GET /logs` with filters, `POST /log-search`
- `GET /graph-data` overlay off by default; overlay on produces LogEvent nodes + virtual edges; query-filtered overlay matches the intended topic

Current results on the IRC5 seed: **15/15 passing**.

Two fixes that made the suite stable with the expanded 277-row seed:

- malformed or unpresentable `event_signature_id` values are filtered out of aggregated search results and analytics summaries;
- the fast filter extraction no longer treats every occurrence of the word `errors` as a severity filter, so queries like "USB communication errors we have seen" can still retrieve WARN/INFO USB history.

Run the full suite from the repo root:

```bash
python -m kg_agents.scripts.test_log_integration_suite
```

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

The current IRC5 implementation uses `text-embedding-3-large` for both occurrence and signature embeddings. That keeps retrieval quality high for the demo seed and matches the checked-in `log_embeddings.json`.

For larger or cost-sensitive backfills, evaluate `text-embedding-3-small` or dimension reduction. The retrieval contract does not depend on the embedding model as long as the index is regenerated consistently.

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
  -> deterministic fast-path intent router, with LLM fallback only when needed
  -> extract filters and entities
  -> run structured filters
  -> run sparse keyword search
  -> run dense embedding search
  -> fuse/rerank candidates
  -> aggregate occurrences
  -> generate grounded answer or Fast structured template
```

Reciprocal rank fusion is a reasonable first approach to combine sparse and dense candidate sets.

## Agent Behavior

The chat endpoint includes an intent-routing step before the current troubleshooting pipeline.

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

"Which IRC5 component has the most repeated events?"
-> log_analytics

"What did we do last time the drive module overheated?"
-> log_history_search or work_order_lookup
```

### Response Shape

Historical responses should include evidence. In Fast mode the response is rendered as a structured template:

```text
**Snapshot**
- Matching occurrences: **12**
- Best matching pattern: `irc5_communications_ethernet_packet_loss`
- Most relevant work order: `WO-IRC5-1001`

**Best Match**
- Date: 2026-04-12
- Severity: ERROR
- Status: open
- Title: Intermittent Ethernet communication loss
- Outcome: monitoring

**Action Taken**
Inspect controller Ethernet cabling, switch port, AXC LED and link state.

**Other Relevant Patterns**
- `irc5_communications_ethernet_packet_loss` - 12 occurrences - 2026-04-12 - Intermittent Ethernet communication loss
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
GET /v1/kg-agents/instances/{instance_id}/graph-data?include_logs=true&log_query=ethernet&limit_logs=20
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

## Implemented API

APIs under the existing versioned contract:

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
event_signature_id
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
  "linked_failure_mode_id": null,
  "maintenance_type": null,
  "event_category": null,
  "event_signature_id": null,
  "status": null,
  "severity_min": null,
  "limit": 10,
  "use_llm_rerank": false
}
```

Response:

```json
{
  "query": "Has Ethernet packet loss happened before?",
  "instance_id": "irc5-default-instance",
  "match_count": 1,
  "matches": [
    {
      "event_signature_id": "irc5_communications_ethernet_packet_loss",
      "score": 0.87,
      "occurrence_count": 12,
      "first_seen_at": "2024-03-05T10:11:00Z",
      "last_seen_at": "2026-04-12T09:20:00Z",
      "linked_failure_mode_id": "fm_ethernet_network_has_problems",
      "linked_symptom_id": "",
      "top_match_log": {
        "log_id": "log_irc5_0001",
        "occurred_at": "2026-04-12T09:20:00Z",
        "severity_text": "ERROR",
        "work_order_id": "WO-IRC5-1001",
        "title": "Intermittent Ethernet communication loss"
      },
      "most_recent_log": {
        "log_id": "log_irc5_0007",
        "occurred_at": "2026-04-11T15:53:55Z",
        "severity_text": "ERROR",
        "title": "Switch SFP/transceiver dirty or failing causing packet errors"
      },
      "all_log_ids": ["log_irc5_0001", "log_irc5_0007"],
      "rerank_rationale": null
    }
  ],
  "diagnostics": {
    "dense_candidates": 25,
    "sparse_candidates": 12,
    "rerank_used": false,
    "timings": {"total_s": 0.238}
  }
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

## Implemented MVP Plan

### Phase 1 - Data Contract

1. Created `kg_agents/data/instances/irc5-default-instance/logs/machine_logs.csv`.
2. Seeded realistic IRC5 rows using the canonical schema.
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

1. Added `kg_agents/engine/log_loader.py`.
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

1. Implemented sparse keyword search over `semantic_text`, `title`, `body`, `action_taken`, codes, and component names.
2. Added local embedding file:

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

1. Added `kg_agents/routers/logs.py`.
2. Registered it in `kg_agents/main.py`.
3. Exposed:
   - `GET /logs`;
   - `POST /log-search`;
   - `GET /logs/summary`.

### Phase 5 - Chat Intent Router

1. Added a deterministic first-pass intent classifier with LLM fallback.
2. Routed historical questions to log search.
3. Kept current troubleshooting behavior unchanged for normal diagnosis.
4. Added hybrid mode when user asks about a current issue plus prior occurrences.

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

## Scaling to 1M Rows for a Real Industrial Case

The current implementation comfortably handles the IRC5 seed (277 rows). For a real factory where a single machine can accumulate hundreds of thousands to millions of log rows over a few years, the architecture needs to change in three specific places. This section documents the analysis and the migration plan - not yet implemented.

### What scales linearly today (and why it breaks)

Three components in the current MVP grow linearly with row count:

1. **`occurrence_embeddings` in memory.** `LogStore` keeps a `dict[log_id, list[float]]` with one 3072-dim vector per row. At 277 rows this is still small. At 1M rows in pure Python list-of-floats this is ~85 GB. Even encoded as numpy float32 it is ~12 GB. The JSON-on-disk format (`log_embeddings.json`) is also unusable at that size - it would be ~12 GB of text.
2. **`LogStore.rows` and the per-id / per-signature dicts.** A row dict with ~40 fields averages ~1 KB; at 1M rows that is ~1 GB of resident Python objects, plus another ~200 MB for the `rows_by_id` / `rows_by_signature` index dicts. Tolerable on a large box but wasteful, and forces a full reload from CSV on every cache evict.
3. **`_dense_scores` linear scan in `log_search.py`.** For every history query it iterates the occurrence embedding dict and computes cosine similarity against the query vector. At 1M rows x 3072 dims that is ~3B float multiplications per query, which in numpy/Python lands at **10-30 s per query for the dense scan alone** before any Guided model composition is considered.

The sparse TF-IDF index built by scikit-learn handles 1M docs reasonably (build ~1-5 min, ~500 MB - 1 GB resident) but the **first request after process start pays the full build cost** because nothing is cached on disk.

### Embedding cost is not the constraint

Embedding 1M `semantic_text` rows with `text-embedding-3-large` is a one-time job, not a recurring per-query cost. Two paths:

- **Online API**, the same batching loop the current `embed_logs.py` already uses: 1M / 256 ≈ 3,900 batches × ~1-2 s each ≈ **1-2 hours wall clock**, ~$10 in API cost (80 tokens/row × 1M rows × $0.13/Mtok).
- **OpenAI Batch API**, asynchronous, ~24h turnaround: **~50% cheaper** (~$5) and avoids hitting RPM limits. The current loader does not use this — adding it is ~30 lines.

Either way, embeddings can be regenerated overnight. They are not the bottleneck; the bottleneck is the *retrieval shape* the embeddings feed into.

### The right architecture for 1M rows

The design doc anticipated this in the Embeddings Strategy section: prefer signature-level embeddings over per-occurrence embeddings. A factory with 1M log rows typically has **hundreds, not millions, of distinct patterns** — once normalized into `event_signature_id`. The shift is to embed those, and keep occurrences in structured storage for exact filters, dates, and aggregates.

Concretely, the migration changes three layers:

**Storage layer.** Replace the in-memory `LogStore.rows` plus the CSV reload with an **SQLite database per instance** at `instances/<id>/logs/machine_logs.sqlite`. Tables: `event_occurrences` (1M rows, indexed on `event_signature_id`, `occurred_at`, `component_id`, `work_order_id`) and `event_signatures` (a few thousand rows, with `canonical_text`, `occurrence_count`, `first_seen_at`, `last_seen_at`, `linked_failure_mode_id`). The loader exposes the same `LogStore` interface but reads from SQL on demand instead of holding everything in RAM. Memory becomes O(1) in row count, queries become indexed-scan instead of full-Python-iteration. Postgres is the same model when multi-machine deployments need it.

**Embedding layer.** Stop embedding every `log_id`. Embed only the `event_signatures.canonical_text` — a few hundred to a few thousand vectors. At those volumes the existing `_dense_scores` linear scan (now over signatures, not occurrences) drops to ~10 ms per query and the full embedding index fits in ~25 MB of RAM. Rare, unique events become single-occurrence signatures and are still covered. This is a ~5-line change in `log_search.py` plus a rewrite of `embed_logs.py` to skip the per-occurrence pass.

**Retrieval layer.** The hybrid flow becomes: dense search over signatures (~10 ms) → for each top signature, pull the matching occurrences from SQLite via `WHERE event_signature_id = ?` joined with the structured filters (~5 ms). Sparse TF-IDF still runs over the occurrence text to catch exact tokens like work order ids and error codes; for 1M docs scikit-learn TF-IDF queries land at ~200 ms, but the index should be pickled to disk after first build so subsequent process starts don't pay the build cost again. For Postgres deployments the sparse step can be replaced by native `tsvector` full-text search.

### Latency projection

With this architecture, the Fast-mode per-query budget at 1M rows is dominated by query embedding for history search and by indexed lookup for exact work orders. Analytics stays aggregate-only. Guided modes may still be dominated by the LLM composer.

| Step | 277 rows (Fast today) | 1M rows (signature-based Fast) |
|------|-----------------|---------------------------|
| Intent routing | deterministic, ~0 ms | deterministic, ~0 ms |
| Query embedding for history | ~0.15-1.3 s | ~0.15-1.3 s |
| Dense scan over signatures/occurrences | current in-memory scan | ~10 ms over signatures |
| Sparse TF-IDF query | ~5 ms | ~200 ms |
| Fetch occurrences for top signatures | dict lookup | ~5 ms (SQL) |
| Aggregation | <1 ms | <1 ms |
| Compose reply | Fast template, ~0 ms | Fast template, ~0 ms |
| **End-to-end history** | **~0.15-1.3 s typical** | **~0.4-1.6 s projected** |

Total response time does **not** grow with row count once the retrieval layer is properly indexed. The "the agent gets slower as we add data" outcome only happens if we keep iterating embedding dicts in Python.

### Vector database — when (and when not) it is needed

A dedicated vector store (Qdrant, FAISS, pgvector) becomes relevant only if the use case requires **dense lookup across individual occurrences**, for example "find the single past event whose narrative most resembles this one" rather than "find similar event patterns". For the canonical historical questions this MVP serves — has it happened before, how often, what was done — signature-level dense search is sufficient and adds zero infrastructure. If individual-occurrence semantic match becomes a requirement later, HNSW indexes on the 1M occurrence vectors give ~10 ms top-k retrieval; the integration is a one-time effort.

### Migration plan (when this becomes a real need)

Ordered by effort and value:

1. Rewrite `scripts/embed_logs.py` to embed only `event_signatures`. ~30 lines. Drops embedding cost from $10 to ~$0.01 and embedding time from hours to seconds for the same 1M-row dataset.
2. Update `engine/log_search.py::_dense_scores` to iterate `store.signature_embeddings` instead of `store.occurrence_embeddings`. ~5 lines.
3. Add a CSV → SQLite importer in `scripts/` and change `log_loader.py` to back `LogStore` with SQLite on demand. ~100 lines.
4. Pickle the sparse TF-IDF index to disk so process restart does not rebuild it. ~20 lines.
5. (Optional) Add OpenAI Batch API support to `embed_logs.py` for the initial signature embedding pass when bootstrapping a large dataset. ~30 lines.
6. (Optional) Migrate to Postgres with `pgvector` and native full-text search when multi-machine or multi-tenant deployments require it. Larger change, deferred until the constraint shows up.

Steps 1-4 are the 80% that turns the system production-ready for 1M rows on a single machine; together they are roughly half a day of work and require no external services. Steps 5-6 are infrastructure choices that depend on operational scale.

## Historical Questions: Resolved

1. `event_signature_id` is curated for the IRC5 demo and can be generated deterministically in future ingest pipelines.
2. Historical answers appear in chat, with an expandable evidence panel and optional graph overlay.
3. Work orders and machine logs use one canonical row model; `work_order_id` is an optional field.
4. Log-to-KG links are seeded and then verified by embedding similarity, with quality flags for auditability.
5. The current convincing demo set is covered by `smoke_test_log_search.py` plus the 15-check integration suite.

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
