# Troubleshooting Agent

Legacy Flask troubleshooting app for a single ontology instance.

In this branch it is no longer the main backend surface. The primary application is `kg_agents/`, which now also serves the local development UI from the same FastAPI process.

This folder is still important because it provides:

- a compatibility Flask app for older local flows
- legacy UI assets and manuals
- compatibility wrappers that forward engine imports to `kg_agents/engine`

## What Is Here

```text
troubleshooting_agent/
  agent.py                 # standalone entry point
  app_factory.py           # Flask app factory
  frontend_routes.py       # route for the HTML UI
  api_routes.py            # unversioned JSON endpoints
  ontology_loader.py       # compatibility wrapper to kg_agents.engine
  embeddings.py            # compatibility wrapper to kg_agents.engine
  similarity.py            # compatibility wrapper to kg_agents.engine
  graph_traversal.py       # compatibility wrapper to kg_agents.engine
  orchestrator.py          # multi-turn session handling
  response_builder.py      # compatibility wrapper to kg_agents.engine
  telemetry_loader.py      # compatibility wrapper to kg_agents.engine
  templates/index.html     # UI shell
  static/css/app.css       # UI styles
  static/js/app.js         # UI behavior
```

## Recommended Local Flow

Use `kg_agents` for day-to-day development:

```bash
python3 -m kg_agents.main
```

Then open:

- `http://localhost:8030/docs`
- `http://localhost:8030/dev-ui`

## Legacy Quick Start

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

The actual shared troubleshooting engine now lives in `kg_agents/engine/`.

This folder keeps thin compatibility wrappers for:

- ontology loading
- query embeddings
- symptom matching
- graph traversal
- response formatting
- telemetry payload assembly

That means runtime logic changes should now be made in `kg_agents/engine/`, not here.

Changes in this folder mainly affect:

- the standalone Flask app
- legacy compatibility behavior

## Data Assumptions

The default flow expects an ontology built around:

- `Symptom`
- `FailureMode`
- `CorrectiveAction`

and relationships such as:

- `MAY_INDICATE`
- `RESOLVED_BY`
- `AFFECTS`

The default seeded ontology is the ABB IRC5 robot controller (`irc5_abb_robotics_V0.json`). The accompanying telemetry file is `irc5_abb_robotics_telemetry.csv` at the repository root.

If `Symptom` nodes change, regenerate embeddings with:

```bash
python3 troubleshooting_agent/embeddings.py
```

## Current Role In This Branch

- use `kg_agents` when you need the supported API surface
- use the dev UI under `kg_agents` when you want to inspect chat, graph highlighting, manuals, and telemetry against the real versioned API
- use `troubleshooting_agent` only when you explicitly need the old Flask compatibility flow
