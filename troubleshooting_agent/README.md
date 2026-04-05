# Troubleshooting Agent

Standalone Flask troubleshooting app for a single ontology instance.

In this branch it is no longer the main backend surface. The primary application is `kg_agents/`, but this folder is still important because it provides:

- the shared troubleshooting engine reused by `kg_agents`
- the local standalone UI used to inspect chat, graph highlighting, manuals, and telemetry while developing in this repository

## What Is Here

```text
troubleshooting_agent/
  agent.py                 # standalone entry point
  app_factory.py           # Flask app factory
  frontend_routes.py       # route for the HTML UI
  api_routes.py            # unversioned JSON endpoints
  ontology_loader.py       # ontology parsing and indexing
  embeddings.py            # symptom embedding generation and query embedding
  similarity.py            # cosine similarity ranking
  graph_traversal.py       # graph traversal for troubleshooting paths
  orchestrator.py          # multi-turn session handling
  response_builder.py      # deterministic grounded response formatting
  telemetry_loader.py      # CSV telemetry loading and stats
  templates/index.html     # UI shell
  static/css/app.css       # UI styles
  static/js/app.js         # UI behavior
```

## Quick Start

```bash
python3 troubleshooting_agent/agent.py
```

Default port: `5001`

Optional override:

```bash
AGENT_PORT=5002 python3 troubleshooting_agent/agent.py
```

Requirements:

- `OPENAI_API_KEY`
- dependencies from `requirements.txt`
- `symptom_embeddings.json` aligned with ontology symptoms

## Endpoints

These are the standalone Flask endpoints used by the legacy UI:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Chat + graph UI |
| POST | `/chat` | Send a troubleshooting message |
| POST | `/next_issue` | Show the next ranked possible cause |
| POST | `/reset` | Reset the session |
| GET | `/graph_data` | Full graph payload for vis-network |
| GET | `/product_info` | Product metadata and suggested symptoms |
| GET | `/status` | Ontology version and counts |
| GET | `/manuals/<filename>` | Serve local PDF manuals |
| GET | `/telemetry/<signal_name>` | Raw telemetry series and statistics |

## Relationship With `kg_agents`

`kg_agents` imports these modules at runtime and reuses them per instance:

- ontology loading
- query embeddings
- symptom matching
- graph traversal
- response formatting
- telemetry payload assembly

That means changes in this folder can affect both:

- the standalone Flask app
- the FastAPI APIs in `kg_agents`

## Data Assumptions

The default flow expects an ontology built around:

- `Symptom`
- `FailureMode`
- `CorrectiveAction`

and relationships such as:

- `MAY_INDICATE`
- `RESOLVED_BY`
- `AFFECTS`

If `Symptom` nodes change, regenerate embeddings with:

```bash
python3 troubleshooting_agent/embeddings.py
```

## Current Role In This Branch

- use `kg_agents` when you need the supported API surface
- use `troubleshooting_agent` when you want to inspect the local UI or debug the shared troubleshooting engine directly
