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

- `OPENAI_API_KEY` is required because runtime matching uses OpenAI embeddings for symptom retrieval, candidate cause reranking, log search, and non-Fast reply composition.
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
- log-chat handlers with Fast-mode structured templates, non-Fast LLM composition, and an evidence list per assistant turn

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
| GET | `/v1/kg-agents/instances/{instance_id}/product-info` | Product metadata and suggested symptoms |
| GET | `/v1/kg-agents/instances/{instance_id}/status` | Ontology status and counts |

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
  -> intent branch:
       * log_history_search       -> hybrid log retrieval + Fast template, or LLM-composed reply in non-Fast mode
       * log_analytics            -> aggregate summary + Fast template, or LLM-composed reply in non-Fast mode
       * work_order_lookup        -> direct WO lookup when id is present + Fast template, or LLM-composed reply in non-Fast mode
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
- non-Fast models still use the same retrieval and evidence payloads, but may call the LLM to compose a freer narrative response

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
- Fast mode uses deterministic structured templates for `log_history_search`, `log_analytics`, and exact `work_order_lookup`; non-Fast modes can still use LLM composition over the same retrieved evidence.
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
