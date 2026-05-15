# Knowledge Agents API

FastAPI backend for the Knowledge Agents platform — a multi-agent knowledge graph system for diagnostics, troubleshooting, and quality analysis. It supports multi-instance troubleshooting backed by a knowledge graph.

This is the primary application for the current branch.

## Quick Start

```bash
pip install -r requirements.txt
python3 -m kg_agents.main
```

Default port: `8030`

API docs:

- `http://localhost:8030/docs`
- `http://localhost:8030/redoc`
- `http://localhost:8030/dev-ui`

## Environment Variables

Create a `.env` file in the project root:

```env
OPENAI_API_KEY=sk-...
AGENT_PORT=8030                  # optional, defaults to 8030
OPENAI_CHAT_MODEL=gpt-5-nano     # optional, defaults to Fast mode
OPENAI_ROUTER_MODEL=gpt-5-nano   # optional, deterministic fast path handles common routes first
```

Notes:

- `OPENAI_API_KEY` is required because runtime matching uses OpenAI embeddings for symptom retrieval, candidate cause reranking, log search, and Guided reply composition.
- Three model knobs are exposed in `kg_agents/config.py` and can be overridden through env vars: `OPENAI_EMBEDDING_MODEL` (`text-embedding-3-large`), `OPENAI_CHAT_MODEL` (`gpt-5-nano`), and `OPENAI_ROUTER_MODEL` (`gpt-5-nano`).
- `gpt-5-nano` is the default Fast mode. For log history, analytics, and exact work-order lookups, Fast mode uses deterministic structured templates instead of an LLM composition call. Other chat models can still be selected in the dev UI when a more narrative response is worth the extra latency.
- Neo4j is not used by the current implementation. Persistence is file-based under `kg_agents/data/`.

## What This Service Does

- exposes versioned APIs under `/v1/kg-agents/*`
- serves a local dev UI from the same FastAPI process at `/dev-ui`
- manages agent definitions and per-agent ontology instances
- runs troubleshooting chat for a specific instance, including fast-path routed log history / analytics / work-order lookups and hybrid diagnosis-plus-history answers — see [Log Integration](#log-integration)
- returns graph payloads for visualization, with an opt-in log-event overlay
- stores linked devices and failure-mode measurement mappings
- exposes maintenance log retrieval, listing, and analytics endpoints for any instance that provides a `logs/machine_logs.csv` + embedding index
- seeds a default `Maintenance Troubleshooting Agent` and a default `IRC5` instance on startup (idempotent; skipped if already present)

## Architecture

```
kg_agents/
  main.py              # FastAPI app entry point, CORS, static files, lifespan seeding
  config.py            # Environment variables, paths, model thresholds
  models.py            # Pydantic models (Agent, Instance, DeviceLink, Chat, Graph, Log, etc.)
  engine/              # Shared troubleshooting engine (retrieval, traversal, response building, telemetry, log search, intent routing)
  dev_ui/              # Local development UI served by FastAPI
  routers/
    agents.py          # CRUD for agents
    instances.py       # CRUD for ontology instances
    chat.py            # Chat with intent routing, next-issue, reset, product-info, status
    graph.py           # vis-network graph data with optional log overlay
    devices.py         # Device linking and measurement mappings
    logs.py            # Maintenance log summary, listing, detail, and hybrid search
  scripts/             # Seed and maintenance scripts (log generation, link verification, embeddings, smoke tests)
  services/
    agent_store.py     # JSON-file persistence for agents
    instance_store.py  # Per-instance directory management, device links, seeding, schema validation
```

The shared troubleshooting engine now lives inside `kg_agents/engine/`:

- ontology loading and indexing
- query embedding generation
- similarity scoring
- query-to-KG alignment and no-fit guardrails
- deterministic reranking of candidate failure modes
- ambiguity detection plus a single targeted clarification turn when the top causes remain too close
- graph traversal
- deterministic response formatting
- telemetry payload building
- deterministic fast-path chat intent routing with LLM fallback for ambiguous cases (routes to KG, log history, log analytics, work-order lookup, or hybrid)
- maintenance-log loader and hybrid retrieval (dense + sparse TF-IDF + RRF + optional LLM rerank)
- log-chat handlers with Fast-mode structured templates, Guided LLM composition, and an evidence list per assistant turn

`troubleshooting_agent/` remains in the repository as a legacy compatibility layer. The supported integration contract is still the versioned REST API under `/v1/kg-agents/*`.

## Data Layout

```
kg_agents/data/
  agents.json                          # Agent registry
  instances/
    <instance_id>/
      meta.json                        # Instance metadata (name, agent_id, timestamps)
      ontology.json                    # Knowledge graph data (nodes + relationships)
      symptom_embeddings.json          # OpenAI embeddings for symptom matching
      devices.json                     # Linked devices and measurement mappings
      telemetry/                       # Optional CSV telemetry data
      logs/                            # Optional per-machine maintenance log overlay
        machine_logs.csv               # Canonical work-order / event log (39 columns)
        log_embeddings.json            # text-embedding-3-large index over the CSV
  extraction_summaries/
    <instance_id>.json                 # Extraction metadata (1:1 with instance)
```

### Extraction Summaries

Each time a knowledge graph is extracted from source documents, an extraction summary is stored as `kg_agents/data/extraction_summaries/<instance_id>.json`. It contains:

- **context** — KG ID, version (linear: v1, v2, ...), ontology reference name, extraction timestamp
- **extraction_performance** — status, triplets validated, automation time, estimated cost, LLM token usage
- **model_usage** — primary and secondary models used during extraction
- **file_links** — relative paths to the instance's meta.json and ontology.json

Summaries are 1:1 with instances. The `version` field supports linear versioning for tracking changes when knowledge graphs are manually edited.

On first startup the app seeds:

- agent: `maintenance-agent-default`
- instance: `irc5-default-instance`

The seeded instance is copied from the repository defaults:

- root `irc5_abb_robotics_V0.json` (falls back to `ontology.json` if missing)
- `troubleshooting_agent/symptom_embeddings.json`
- optional telemetry from `troubleshooting_agent/telemetry/`

Node and relationship counts for the default instance follow the checked-in `irc5_abb_robotics_V0.json`. Create additional agents and instances via `/v1/kg-agents/agents` and `/v1/kg-agents/agents/{agent_id}/instances`.

## API Endpoints

### Agents

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/kg-agents/agents` | List all agents |
| POST | `/v1/kg-agents/agents` | Create agent |
| GET | `/v1/kg-agents/agents/{agent_id}` | Get agent |
| GET | `/v1/kg-agents/agents/{agent_id}/schema` | Get ontology schema for an agent |
| DELETE | `/v1/kg-agents/agents/{agent_id}` | Delete agent |

### Instances

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/kg-agents/instances` | List all instances across agents |
| GET | `/v1/kg-agents/agents/{agent_id}/instances` | List instances for an agent |
| POST | `/v1/kg-agents/agents/{agent_id}/instances` | Create instance |
| GET | `/v1/kg-agents/instances/{instance_id}` | Get instance metadata |
| PUT | `/v1/kg-agents/instances/{instance_id}` | Update instance metadata or ontology |
| DELETE | `/v1/kg-agents/instances/{instance_id}` | Delete instance |
| GET | `/v1/kg-agents/instances/{instance_id}/extraction-summary` | Get extraction summary |

### Chat

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/kg-agents/instances/{instance_id}/chat` | Send a message and get a diagnosis |
| POST | `/v1/kg-agents/instances/{instance_id}/next-issue` | Return the next ranked possible cause |
| POST | `/v1/kg-agents/instances/{instance_id}/reset` | Reset the chat session |
| POST | `/v1/kg-agents/instances/{instance_id}/log-outcome` | Record the result of a selected corrective action for intervention analytics |
| GET | `/v1/kg-agents/instances/{instance_id}/path-stats` | Return historical outcome stats for a path, failure mode, or action |
| GET | `/v1/kg-agents/instances/{instance_id}/product-info` | Product metadata and suggested symptoms |
| POST | `/v1/kg-agents/instances/{instance_id}/reload` | Evict in-memory caches for ontology, embeddings, telemetry, and logs |
| GET | `/v1/kg-agents/instances/{instance_id}/status` | Ontology status and counts |
| GET | `/v1/kg-agents/instances/{instance_id}/chat-sessions` | List persisted chat sessions for an instance |
| GET | `/v1/kg-agents/instances/{instance_id}/chat-sessions/{session_id}` | Return persisted messages for one session |

### Graph

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/kg-agents/instances/{instance_id}/graph-data` | vis-network graph payload (supports `include_logs`, `log_query`, `limit_logs` for the log overlay) |

### Logs

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/kg-agents/instances/{instance_id}/logs/summary` | Aggregate analytics (top signatures, top components, severity distribution, monthly counts, downtime by component) |
| GET | `/v1/kg-agents/instances/{instance_id}/logs/{log_id}` | Single log record |
| GET | `/v1/kg-agents/instances/{instance_id}/logs` | Filtered listing (`q`, `event_category`, `maintenance_type`, `status`, `severity_min`, `component_id`, `linked_failure_mode_id`, `event_signature_id`, `date_from`, `date_to`, `limit`, `offset`) |
| POST | `/v1/kg-agents/instances/{instance_id}/log-search` | Hybrid retrieval (dense + sparse + RRF + optional LLM rerank) returning signature-level matches |

### Log API Contract

The log APIs are instance-scoped and are the stable contract for external clients that need machine-history data without going through chat.

`GET /logs/summary` returns aggregate analytics. It accepts the same structured filters used by `GET /logs` except pagination and free-text `q`: `event_category`, `maintenance_type`, `status`, `severity_min`, `component_id`, `linked_failure_mode_id`, `event_signature_id`, `date_from`, and `date_to`. Date-only `date_to` is treated as an exclusive upper bound by convention, so `date_from=2026-04-01&date_to=2026-05-01` covers April 2026.

```json
{
  "instance_id": "irc5-default-instance",
  "row_count": 277,
  "top_event_signatures": [{"event_signature_id": "irc5_drive_motor_overtemperature", "occurrence_count": 15}],
  "top_components": [{"component_id": "comp_robot_brakes", "count": 41}],
  "severity_distribution": {"INFO": 74, "WARN": 103, "ERROR": 94, "FATAL": 6},
  "events_by_month": {"2026-04": 8},
  "open_events": 62,
  "downtime_by_component_min": {"comp_robot_brakes": 1234}
}
```

`GET /logs` is structured filtering and pagination. It returns `LogListResponse`:

```json
{
  "instance_id": "irc5-default-instance",
  "total": 12,
  "limit": 50,
  "offset": 0,
  "items": [{ "log_id": "log_irc5_0001", "event_signature_id": "irc5_communications_ethernet_packet_loss" }]
}
```

`POST /log-search` is semantic/hybrid retrieval. Request body:

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
  "limit": 5,
  "use_llm_rerank": false
}
```

Response body:

```json
{
  "query": "Has Ethernet packet loss happened before?",
  "instance_id": "irc5-default-instance",
  "match_count": 1,
  "matches": [
    {
      "event_signature_id": "irc5_communications_ethernet_packet_loss",
      "score": 0.0325,
      "occurrence_count": 12,
      "first_seen_at": "2024-03-05T10:11:00Z",
      "last_seen_at": "2026-04-11T15:53:55Z",
      "linked_failure_mode_id": "fm_ethernet_network_has_problems",
      "linked_symptom_id": "",
      "top_match_log": { "log_id": "log_irc5_0007" },
      "most_recent_log": { "log_id": "log_irc5_0007" },
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

`LogRecord` contains the canonical CSV fields: `log_id`, source fields, timestamps, `instance_id`, asset/device/equipment fields, event category/status/severity, component and code fields, observed/threshold values, `work_order_id`, `title`, `body`, `action_taken`, `outcome`, duration fields, `semantic_text`, `event_signature_id`, KG link ids, `quality_flags`, and `attributes_json`.

### Chat Response Contract

`POST /chat` accepts an optional `behavior_mode` request field. External frontends should send it when the user has explicitly selected a product mode:

```json
{
  "message": "Motor overload on axis 2",
  "session_id": null,
  "mode": "fast",
  "behavior_mode": "solve_current_problem"
}
```

Accepted `behavior_mode` values:

- `solve_current_problem` — run the troubleshooting flow. This is KG-first diagnosis and may still attach retrieved historical log evidence in `log_evidence`.
- `search_past_events` — run the past-events/log-search flow. This returns history, analytics, or work-order style answers and does not promote the request into a current diagnosis.

If `behavior_mode` is omitted, the backend keeps the legacy behavior and uses intent routing to choose the product mode. If it is present, it is treated as the user's explicit UI choice and takes precedence over intent routing for the coarse product mode. The intent classifier still runs as an implementation detail to classify subtypes such as log history, analytics, work-order lookup, hybrid evidence, and follow-up context.

In `search_past_events`, chat questions may include natural English date scopes. The deterministic router converts supported phrases into the same structured `date_from` / `date_to` filters used by the log APIs, then removes the temporal wording from the semantic retrieval query. Supported examples include `past 2 months`, `last 3 months`, `past year` (rolling year), `last year` / `previous year` (previous calendar year), `from January 10 to March 4`, and `from 10 January to 4 March`. Date-only `date_to` remains exclusive, so an end date mentioned by the user is sent as the following day. If the fast deterministic router detects date-like wording that it cannot parse, it falls back to the LLM classifier instead of returning unfiltered history.

`POST /chat` still returns the same troubleshooting payload, with additive log fields:

- `intent`: one of `troubleshooting_current`, `log_history_search`, `log_analytics`, `work_order_lookup`, `hybrid_diagnosis_with_history`
- `behavior_mode`: one of `solve_current_problem`, `search_past_events`
- `log_evidence`: compact list of retrieved log matches used for the answer
- `metrics.log_filters`: structured filters used by log/history answers, when applicable
- `metrics.log_summary`: aggregate summary used by log analytics answers, when applicable
- `timings`: per-stage latency map for observability

Existing clients that only read `reply`, `session_id`, `highlight`, `current_issue`, or `telemetry` can keep doing so. Log-aware clients should use `behavior_mode`, `intent`, and `log_evidence` to decide whether to render a diagnosis card, a past-events card, and/or a troubleshooting evidence panel.

### Devices

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/kg-agents/instances/{instance_id}/devices` | List linked devices |
| POST | `/v1/kg-agents/instances/{instance_id}/devices` | Link or upsert a device |
| DELETE | `/v1/kg-agents/instances/{instance_id}/devices/{device_id}` | Unlink device |
| POST | `/v1/kg-agents/instances/{instance_id}/devices/{device_id}/measurement-mappings` | Save measurement mappings for a device |
| GET | `/v1/kg-agents/instances/{instance_id}/failure-modes-measurements` | List failure modes that expose `related_measurements` |

## Local Development UI

`kg_agents` serves a lightweight development UI at `/dev-ui`. It is intended for local testing of the same API contract consumed by the external frontend.

Characteristics:

- uses the versioned instance-scoped routes under `/v1/kg-agents/*`
- runs against the same seeded or user-created instances exposed by the API
- does not require running the external frontend repository
- supports chat, next-issue navigation, graph highlighting, manuals, and telemetry

The root path `/` redirects to `/dev-ui` for convenience.

## Chat Pipeline

```text
User message
  -> error code detection (if present, runs the KG flow directly)
  -> deterministic fast-path intent router
  -> optional LLM intent classifier fallback only for ambiguous cases
  -> optional request `behavior_mode` override from the UI/external client:
       * solve_current_problem -> keep the flow in troubleshooting mode, while still attaching log evidence when available
       * search_past_events    -> keep the flow in log/history mode
  -> intent branch:
       * log_history_search       -> hybrid log retrieval + Fast template, or LLM-composed reply in Guided mode
       * log_analytics            -> aggregate summary + Fast template, or LLM-composed reply in Guided mode
       * work_order_lookup        -> direct WO lookup when id is present + Fast template, or LLM-composed reply in Guided mode
       * hybrid_diagnosis_w/hist  -> KG flow PLUS "Past similar events" appendix
       * troubleshooting_current  -> existing KG flow (default fallback)
  -> KG flow (when applicable):
       query embedding via OpenAI
       cosine similarity against symptom embeddings
       -> domain relevance check
       -> graph traversal
       -> deterministic candidate reranking (symptom score + KG term overlap + semantic cause similarity)
       -> no-fit guardrail when the query names a technical entity unsupported by the retrieved paths
       -> optional clarification question when the top two causes are still too close and meaningfully different
       -> deterministic grounded answer
       -> trace payload for graph highlighting
       -> optional telemetry payload
  -> intent tag + log evidence list attached to every response
```

Current behavior:

- embeddings are generated through OpenAI
- domain relevance is deterministic
- failure modes are not shown in raw ontology order; they are reranked against the user query before the first issue is returned
- if the user explicitly mentions a technical entity such as `ethernet` or `joystick` and no retrieved path supports it, the assistant returns a no-fit fallback instead of forcing an unrelated cause
- if the user message is too broad to separate two nearby causes confidently, the API asks one focused clarification question before returning the first issue
- if the clarification answer is unrelated or nonsensical, the API rejects it and repeats the same clarification instead of treating it as a new diagnosis
- response formatting is deterministic
- returned facts come from the ontology and linked telemetry/manual metadata
- telemetry is resolved per instance when a telemetry CSV is present under `kg_agents/data/instances/<instance_id>/telemetry/`
- every chat response includes a `timings` map with per-stage latency, for example `setup_s`, `intent_fast_path_s`, `log_history_search_total_s`, `kg_similarity_s`, `response_build_s`, and `total_s`

## Fast Mode

Fast mode is the default (`gpt-5-nano`) and is intentionally optimized for common operator questions. It keeps the KG troubleshooting behavior intact, but avoids avoidable LLM calls around log/history flows.

Fast-mode behavior:

- common intents are routed by deterministic string/pattern matching before any LLM classifier is considered
- KG query embeddings are deferred until the request actually needs the KG flow
- log history uses dense + sparse retrieval with RRF, then renders a structured template
- log analytics reads aggregate counters and renders a structured template
- exact work-order queries use direct lookup by `WO-*` id and render a structured template
- log analytics respects explicit time filters such as `last month`, `this month`, `last 30 days`, and `last week`
- count-style analytics for a named problem, such as `How many times has motor overload happened?`, uses log retrieval and returns matching evidence rather than only global summary rows
- Guided models still use the same retrieval and evidence payloads, but may call the LLM to compose a freer narrative response

The default suggested chat chips are tuned for these fast routes:

| Label | Query |
|-------|-------|
| Fix now | `Robot brake voltage too low, how do I fix it?` |
| Past drive cases | `Show me past drive motor overtemperature cases` |
| Past network cases | `Show me past Ethernet packet loss cases` |
| Repeated events | `Which IRC5 component has the most repeated events?` |
| Work order | `Show me details for work order WO-IRC5-1042` |
| Symptom + history | `FlexPendant just disconnected - has this happened before and how was it fixed?` |

Write Fast-mode questions with a clear route signal:

- use `past`, `history`, `have we seen`, or `has this happened` for historical lookup
- use `which component`, `most repeated`, `how many`, or `how often` for analytics
- include the exact `WO-*` id for work-order lookup
- combine a current symptom with `has this happened before` for hybrid diagnosis plus history

## Log Integration

Each instance can carry a per-machine maintenance log overlay under `instances/<id>/logs/`. The chat assistant uses it to answer historical, analytical, work-order, and hybrid diagnosis-plus-history questions while the existing knowledge-graph troubleshooting flow stays unchanged.

### Design and rationale

Full design notes including data model, retrieval strategy, intent routing, graph overlay shape, and resolved open questions live in [`log_integration.md`](log_integration.md). Short version:

- Logs are **not** ontology nodes — they live in a CSV alongside the ontology, with an embedding index, and are linked back to the graph only through optional `linked_failure_mode_id` / `linked_symptom_id` fields plus virtual edges in graph responses.
- Retrieval is **hybrid**: dense (`text-embedding-3-large`) plus sparse TF-IDF with Reciprocal Rank Fusion, with optional LLM rerank for ambiguous non-exact lookups.
- Intent routing is **fast-path first**: common history, analytics, work-order, hybrid, and troubleshooting phrases are routed deterministically. Ambiguous messages can still fall back to `OPENAI_ROUTER_MODEL`.
- Fast mode uses deterministic structured templates for `log_history_search`, `log_analytics`, and exact `work_order_lookup`; Guided modes can still use LLM composition over the same retrieved evidence.
- The graph overlay is **opt-in**. The default `/graph-data` response is byte-identical to the pre-log-integration behaviour.

### Seeding logs for a new instance

Three idempotent scripts under `kg_agents/scripts/` produce the canonical artifacts:

```bash
# 1. Generate or refresh realistic CMMS-style log rows anchored to the ontology
python -m kg_agents.scripts.generate_irc5_logs --force

# 2. Recompute log -> ontology links via embedding similarity and flag dirty rows
python -m kg_agents.scripts.verify_log_links

# 3. Batch-embed semantic_text for occurrences and signatures
python -m kg_agents.scripts.embed_logs --force
```

The scenario plan lives in `kg_agents/scripts/log_seed_plan.json` (20 scenarios for IRC5, tweakable without code changes). Generation uses an LLM to vary phrasing per occurrence so retrieval testing exercises real linguistic diversity.

### Testing

```bash
# Targeted retrieval demo over the IRC5 seed (10 canonical queries)
python -m kg_agents.scripts.smoke_test_log_search

# Full end-to-end suite (15 checks: intents, retrieval, filters, replies,
# edge cases, HTTP API, graph overlay, KG-flow regression)
python -m kg_agents.scripts.test_log_integration_suite
```

Current result on the IRC5 seed: `15/15` passing.

### Frontend behaviour

The dev UI at `/dev-ui` renders log responses inline:

- The model selector defaults to **Fast** (`gpt-5-nano`).
- The top chat chips use the Fast-mode suggestions listed above.
- Fast templates render as section headings plus bullets, not plain markdown text.
- An **intent badge** above the assistant message identifies non-default routing (Historical lookup, Log analytics, Work order, Diagnosis + history).
- An expandable **Evidence panel** under each message shows the retrieved log occurrences with severity pill, date, work order id, body excerpt, action_taken, and outcome.
- The graph panel exposes a small **Show logs** toolbar (toggle + query input). When chat returns evidence, the overlay query is auto-prefilled with the user's question; if the toggle is on, the graph reloads to surface `LogEvent` diamond nodes (teal `#14B8A6`) wired to the relevant `asset_*`, `comp_*`, and `fm_*` nodes via three virtual edge types.

### Chat ranking notes

Within a matched symptom, candidate failure modes are ordered by a deterministic reranker that combines:

- the original matched-symptom score
- overlap between the user query and failure mode / component terms from the ontology
- normalized concept families such as `communication`, `power`, `input`, `software`, `display`, `mechanical`, and `thermal`
- semantic similarity between the full user query and a compact text representation of each failure mode group

This means `/next-issue` iterates over the reranked order, not just the original graph insertion order.

## Ontology Expectations

Each agent defines its own ontology schema (node types, properties, relationship types with domain/range). Instances are instantiations of that schema with concrete, asset-specific data. Ontology data is validated against the agent's schema on create and update — unknown node types, relationship types, or domain/range mismatches return a 422 with specific errors.


### Maintenance Troubleshooting schema (`ontology_schema.JSON`)

The default flow expects:

- top-level keys: `metadata`, `nodes`, `relationships`
- node types such as `Component`, `Symptom`, `FailureMode`, `CorrectiveAction`, `ErrorCode`
- identifiers such as `component_id`, `symptom_id`, `failure_mode_id`, `action_id`, `error_code_id`
- troubleshooting relationships such as `MAY_INDICATE`, `RESOLVED_BY`, `AFFECTS`, `INDICATES`, `GENERATES_ERROR`

Each agent can carry its own ontology schema. Each instance is a concrete dataset implementing that schema.
