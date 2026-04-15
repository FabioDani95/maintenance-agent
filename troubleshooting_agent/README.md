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
PYTHONPATH=. python3 troubleshooting_agent/agent.py
```

Default port: `5001`

Optional override:

```bash
PYTHONPATH=. AGENT_PORT=5002 python3 troubleshooting_agent/agent.py
```

Requirements:

- `OPENAI_API_KEY`
- dependencies from `requirements.txt`
- `symptom_embeddings.json` aligned with ontology symptoms
- root `ontology.json` aligned with the same product as the embeddings file

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
- query-to-KG alignment and no-fit guardrails
- deterministic failure-mode reranking
- graph traversal
- response formatting
- telemetry payload assembly

That means runtime logic changes should now be made in `kg_agents/engine/`, not here.

In particular, the legacy Flask app now uses the same shared reranking and no-fit behavior as `kg_agents`:

- failure modes are reranked against the current user message before the first issue is shown
- if the user explicitly names a technical entity and no retrieved troubleshooting path supports it, the app returns a no-fit fallback instead of forcing an unrelated cause

The supported FastAPI path in `kg_agents` has now moved one step further:

- it can ask one targeted clarification question before the first cause when the top candidates are still too close
- it rejects clarification replies that do not make sense for the troubleshooting question and re-asks the same clarification

The legacy Flask app in this folder does not yet mirror that clarification-state machine.

Changes in this folder mainly affect:

- the standalone Flask app
- legacy compatibility behavior

This legacy app does **not** expose the newer intervention outcome APIs. Outcome logging, path statistics, structured `current_issue` payloads, and the `Resolved` / `Next cause` intervention UI are implemented only in `kg_agents`.

Put differently:

- the legacy Flask UI can still show chat, graph, manuals, and telemetry
- it does not show exact-path intervention history
- it does not persist `Resolved` / `not_resolved` outcomes
- it does not expose the structured action selection payload used by the newer FastAPI dev UI

## Data Assumptions

The default flow expects an ontology built around:

- `Symptom`
- `FailureMode`
- `CorrectiveAction`

and relationships such as:

- `MAY_INDICATE`
- `RESOLVED_BY`
- `AFFECTS`

At runtime, this Flask app reads:

- root `ontology.json`
- `troubleshooting_agent/symptom_embeddings.json`
- manuals from `troubleshooting_agent/manuals/`
- telemetry from `troubleshooting_agent/telemetry/telemetry_p1p.csv`

The current checked-in single-instance data is aligned to the ABB IRC5 controller, and `troubleshooting_agent/manuals/IRC5.pdf` is available for manual page links. The older `Bambu Lab P1 series manual.pdf` is still present as a legacy asset.

If `Symptom` nodes change, regenerate embeddings with:

```bash
PYTHONPATH=. python3 troubleshooting_agent/embeddings.py
```

## Current Role In This Branch

- use `kg_agents` when you need the supported API surface
- use the dev UI under `kg_agents` when you want to inspect chat, graph highlighting, manuals, telemetry, and intervention logging against the supported versioned API
- use `troubleshooting_agent` only when you explicitly need the old Flask compatibility flow
