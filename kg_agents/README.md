# Knowledge Agents API

FastAPI backend for the Knowledge Agents platform: a multi-agent, multi-instance troubleshooting system backed by a knowledge graph.

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

## Environment Variables

Create a `.env` file in the project root:

```env
OPENAI_API_KEY=sk-...
AGENT_PORT=8030
```

Notes:

- `OPENAI_API_KEY` is required because query matching uses OpenAI embeddings.
- Neo4j is not used by the current implementation. Persistence is file-based under `data/`.

## What This Service Does

- exposes versioned APIs under `/v1/kg-agents/*`
- manages agent definitions and per-agent ontology instances
- runs troubleshooting chat for a specific instance
- returns graph payloads for visualization
- stores linked devices and failure-mode measurement mappings
- seeds a default `Maintenance Troubleshooting Agent` and a default `Bambu Lab P1P` instance on startup

## Architecture

```text
kg_agents/
  main.py              # FastAPI app entry point, CORS, startup seeding
  config.py            # environment variables, paths, thresholds
  models.py            # Pydantic request/response models
  routers/
    agents.py          # CRUD for agents
    instances.py       # CRUD for ontology instances
    chat.py            # chat, next-issue, reset, product-info, status
    graph.py           # vis-network graph payload
    devices.py         # device linking and measurement mappings
  services/
    agent_store.py     # JSON persistence for agents
    instance_store.py  # per-instance directories, metadata, devices, seeding
```

The service reuses the troubleshooting engine from `troubleshooting_agent/` at runtime:

- ontology loading
- symptom embedding lookup
- similarity scoring
- graph traversal
- deterministic response formatting
- telemetry payload building

`kg_agents/main.py` also mounts static assets from `troubleshooting_agent/` and serves a legacy HTML shell at `/`, but the supported integration contract is the versioned REST API.

## Data Layout

```text
data/
  agents.json
  instances/
    <instance_id>/
      meta.json
      ontology.json
      symptom_embeddings.json
      devices.json
      telemetry/
```

On first startup the app seeds:

- agent: `maintenance-agent-default`
- instance: `p1p-default-instance`

The seeded instance is copied from the repository defaults:

- root `ontology.json`
- `troubleshooting_agent/symptom_embeddings.json`
- optional telemetry from `troubleshooting_agent/telemetry/`

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
| GET | `/v1/kg-agents/agents/{agent_id}/instances` | List instances for an agent |
| POST | `/v1/kg-agents/agents/{agent_id}/instances` | Create instance |
| GET | `/v1/kg-agents/instances/{instance_id}` | Get instance metadata |
| PUT | `/v1/kg-agents/instances/{instance_id}` | Update instance metadata or ontology |
| DELETE | `/v1/kg-agents/instances/{instance_id}` | Delete instance |

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
| GET | `/v1/kg-agents/instances/{instance_id}/graph-data` | vis-network graph payload |

### Devices

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/kg-agents/instances/{instance_id}/devices` | List linked devices |
| POST | `/v1/kg-agents/instances/{instance_id}/devices` | Link or upsert a device |
| DELETE | `/v1/kg-agents/instances/{instance_id}/devices/{device_id}` | Unlink device |
| POST | `/v1/kg-agents/instances/{instance_id}/devices/{device_id}/measurement-mappings` | Save measurement mappings for a device |
| GET | `/v1/kg-agents/instances/{instance_id}/failure-modes-measurements` | List failure modes that expose `related_measurements` |

## Chat Pipeline

```text
User message
  -> error code detection (if present)
  -> query embedding via OpenAI
  -> cosine similarity against symptom embeddings
  -> domain relevance check
  -> graph traversal
  -> deterministic grounded answer
  -> trace payload for graph highlighting
  -> optional telemetry payload
```

Current behavior:

- embeddings are generated through OpenAI
- domain relevance is deterministic
- response formatting is deterministic
- returned facts come from the ontology and linked telemetry/manual metadata

## Ontology Expectations

The default flow expects:

- top-level keys: `metadata`, `nodes`, `relationships`
- node types such as `Component`, `Symptom`, `FailureMode`, `CorrectiveAction`, `ErrorCode`
- identifiers such as `component_id`, `symptom_id`, `failure_mode_id`, `action_id`, `error_code_id`
- troubleshooting relationships such as `MAY_INDICATE`, `RESOLVED_BY`, `AFFECTS`, `INDICATES`, `GENERATES_ERROR`

Each agent can carry its own ontology schema. Each instance is a concrete dataset implementing that schema.
