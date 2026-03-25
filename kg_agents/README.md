# Knowledge Agents API

FastAPI backend for the Knowledge Agents platform — a multi-agent knowledge graph system for troubleshooting and diagnostics.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server (port 8030)
python -m kg_agents.main
```

The API docs are available at `http://localhost:8030/docs` (Swagger) and `http://localhost:8030/redoc`.

## Environment Variables

Create a `.env` file in the project root:

```env
OPENAI_API_KEY=sk-...
NEO4J_URI=neo4j+s://...
NEO4J_USERNAME=...
NEO4J_PASSWORD=...
NEO4J_DATABASE=neo4j
AGENT_PORT=8030          # optional, defaults to 8030
```

## Architecture

```
kg_agents/
  main.py              # FastAPI app entry point, CORS, static files, lifespan seeding
  config.py            # Environment variables, paths, model thresholds
  models.py            # Pydantic models (Agent, Instance, DeviceLink, Chat, Graph, etc.)
  routers/
    agents.py          # CRUD for agents
    instances.py       # CRUD for ontology instances
    chat.py            # Chat, next-issue, reset, product-info, status
    graph.py           # vis-network graph data
    devices.py         # Device linking and measurement mappings
  services/
    agent_store.py     # JSON-file persistence for agents
    instance_store.py  # Per-instance directory management, device links, seeding
```

The chat pipeline reuses the existing `troubleshooting_agent/` modules (orchestrator, embeddings, similarity, graph traversal, response builder) via `sys.path`, scoped per-instance at runtime.

## Data Layout

```
data/
  agents.json                          # Agent registry
  instances/
    <instance_id>/
      meta.json                        # Instance metadata (name, agent_id, timestamps)
      ontology.json                    # Knowledge graph data (nodes + relationships)
      symptom_embeddings.json          # OpenAI embeddings for symptom matching
      devices.json                     # Linked devices and measurement mappings
      telemetry/                       # Optional CSV telemetry data
```

On first startup, the app seeds a **Maintenance Troubleshooting Agent** with a **Bambu Lab P1P** instance (73 nodes, 98 relationships, 16 symptom embeddings).

## API Endpoints

### Agents

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/kg-agents/agents` | List all agents |
| POST | `/v1/kg-agents/agents` | Create agent |
| GET | `/v1/kg-agents/agents/{agent_id}` | Get agent |
| GET | `/v1/kg-agents/agents/{agent_id}/schema` | Get ontology schema |
| DELETE | `/v1/kg-agents/agents/{agent_id}` | Delete agent |

### Instances

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/kg-agents/agents/{agent_id}/instances` | List instances for agent |
| POST | `/v1/kg-agents/agents/{agent_id}/instances` | Create instance |
| GET | `/v1/kg-agents/instances/{instance_id}` | Get instance |
| PUT | `/v1/kg-agents/instances/{instance_id}` | Update instance |
| DELETE | `/v1/kg-agents/instances/{instance_id}` | Delete instance |

### Chat

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/kg-agents/instances/{instance_id}/chat` | Send message (returns diagnosis) |
| POST | `/v1/kg-agents/instances/{instance_id}/next-issue` | Get next ranked cause |
| POST | `/v1/kg-agents/instances/{instance_id}/reset` | Reset chat session |
| GET | `/v1/kg-agents/instances/{instance_id}/product-info` | Product metadata + suggested symptoms |
| GET | `/v1/kg-agents/instances/{instance_id}/status` | Ontology stats |

### Graph

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/kg-agents/instances/{instance_id}/graph-data` | vis-network nodes/edges payload |

### Devices

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/kg-agents/instances/{instance_id}/devices` | List linked devices |
| POST | `/v1/kg-agents/instances/{instance_id}/devices` | Link device |
| DELETE | `/v1/kg-agents/instances/{instance_id}/devices/{device_id}` | Unlink device |
| POST | `/v1/kg-agents/instances/{instance_id}/devices/{device_id}/measurement-mappings` | Save measurement-to-subscription mappings |

## Chat Pipeline

```
User Message
  |
  +--> Error Code Detection (HMS-format regex)
  |      |-> Match: ErrorCode -> FailureMode -> CorrectiveAction
  |
  +--> OpenAI Embedding (text-embedding-3-large)
  |      |-> Cosine similarity vs symptom embeddings
  |      |-> Top-K filtering (threshold: 0.45)
  |
  +--> Domain Relevance Check (deterministic, no LLM)
  |      |-> Keyword scan against ontology domain_topics
  |
  +--> Graph Traversal
  |      |-> Symptom -> FailureMode -> CorrectiveAction
  |      |-> Group by failure mode, rank by score
  |
  +--> Response (deterministic formatting, no LLM)
         |-> Markdown with corrective actions + sources
         |-> Highlight trace for graph visualization
         |-> Telemetry payload (if related_measurements exist)
```

All responses are grounded in the knowledge graph — zero LLM-generated facts.

## Ontology Schema

The default schema (`ontology_schema.JSON`) defines:

**Node types:** Asset, Component, Symptom, FailureMode, CorrectiveAction, ErrorCode

**Relationships:** HAS_COMPONENT, RELATED_TO, MAY_INDICATE, AFFECTS, RESOLVED_BY, GENERATES_ERROR, INDICATES

Each agent can define its own ontology schema. Instances are instantiations of that schema with concrete data.
