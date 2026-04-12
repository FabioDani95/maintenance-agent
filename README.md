# Maintenance Agent

Repository for a knowledge-grounded troubleshooting platform built around a product ontology.

In this branch, the primary application is `kg_agents/`: a FastAPI backend that exposes versioned APIs for agents, ontology instances, chat, graph data, device mappings, and intervention outcome logging.

`troubleshooting_agent/` is still kept in the repo because:

- it contains the legacy Flask compatibility app
- it still holds legacy assets such as manuals and the old standalone UI shell
- its engine-facing Python modules now act as compatibility wrappers around `kg_agents/engine`

The old ontology editor under `modify/` has been removed from this branch.

## Repository Structure

```text
maintenance-agent/
├── data/                     # kg_agents persistence (instances, manuals, interventions.db)
├── device/                   # auxiliary assets / experiments
├── kg_agents/                # primary FastAPI application for this branch
│   ├── engine/               # shared troubleshooting engine used by the API
│   └── dev_ui/               # local development UI served by FastAPI
├── scripts/                  # support scripts
├── troubleshooting_agent/    # legacy Flask compatibility layer
├── irc5_abb_robotics_V0.json      # dedicated ABB IRC5 seed ontology
├── irc5_abb_robotics_telemetry.csv # 7-day simulated telemetry for IRC5 (102 signals)
├── ontology.json                  # legacy single-instance ontology consumed by troubleshooting_agent
├── ontology_schema.JSON           # default troubleshooting schema
├── sources.csv                    # source material used to build the ontology
├── requirements.txt
└── README.md
```

## Primary Application: `kg_agents`

`kg_agents` is the supported API surface for this branch.

What it does:

- manages knowledge agents and their ontology schemas
- manages per-agent ontology instances
- runs troubleshooting chat over a knowledge graph
- returns graph payloads for visualization
- stores linked devices and measurement mappings
- persists intervention outcomes and per-path success metrics
- seeds a default maintenance agent and a default ABB IRC5 instance on startup

Main characteristics:

- FastAPI app with Swagger at `/docs` and ReDoc at `/redoc`
- local development UI served by the same backend at `/dev-ui` with `/` redirecting there
- the dev UI now shows per-path historical outcome stats and exposes `Resolved` / `Next cause` controls during diagnosis
- persistence on local JSON files plus `data/interventions.db`
- chat pipeline based on symptom embeddings, graph traversal, and deterministic grounded responses
- shared troubleshooting engine lives in `kg_agents/engine`

Frontend behavior in the bundled dev UI:

- each suggested corrective action is shown with exact-path history for `symptom -> failure_mode -> action`
- if the same path was already resolved in previous sessions, the UI shows that immediately in the assistant response
- `Resolved` persists the selected corrective-action path outcome
- `Next cause` advances to the next ranked failure mode
- when the current issue has exactly one corrective action, `Next cause` also records that path as `not_resolved` before moving on

Run it with:

```bash
python3 -m kg_agents.main
```

Default port: `8030`

Full API and architecture details: [kg_agents/README.md](/Users/fabio.daniele/Coding/maintenance-agent/kg_agents/README.md)

## Secondary Application: `troubleshooting_agent`

`troubleshooting_agent` is the older single-instance Flask app. It is no longer the recommended local run path because the same repository now exposes the dev UI from `kg_agents` itself.

Run it with:

```bash
PYTHONPATH=. python3 troubleshooting_agent/agent.py
```

Default port: `5001`

Details and local endpoints: [troubleshooting_agent/README.md](/Users/fabio.daniele/Coding/maintenance-agent/troubleshooting_agent/README.md)

## Data Model

The default troubleshooting flow assumes an ontology shaped around:

- `Symptom`
- `FailureMode`
- `CorrectiveAction`

and operational relationships such as:

- `MAY_INDICATE`
- `RESOLVED_BY`
- `AFFECTS`

`kg_agents` stores concrete data per instance under `data/instances/<instance_id>/`, including:

- `meta.json`
- `ontology.json`
- `symptom_embeddings.json`
- `devices.json`
- optional telemetry files

Cross-session intervention outcomes and aggregate path success metrics are stored in:

- `data/interventions.db`

Manual PDFs are served from:

- `data/manuals/` first, when present
- otherwise `troubleshooting_agent/manuals/`

The repository root contains the dedicated IRC5 seed assets used to initialise the default `kg_agents` instance:

- `irc5_abb_robotics_V0.json` — knowledge graph for the ABB IRC5 robot controller
- `irc5_abb_robotics_telemetry.csv` — 7-day simulated timeseries at 5-minute resolution (102 signal columns matching `related_measurements` in the ontology)

The legacy single-instance Flask app still reads:

- root `ontology.json`
- `troubleshooting_agent/symptom_embeddings.json`
- `troubleshooting_agent/manuals/`

At the moment those legacy single-instance assets are aligned to IRC5 as well.

## Notes

- `OPENAI_API_KEY` is required to generate query embeddings.
- If you change `Symptom` nodes, regenerate embeddings before using the updated instance.
- The checked-in `data/instances/` directory can contain additional concrete instances beyond the current default seed, such as `p1p-default-instance`.
- Intervention statistics are tracked per canonical `symptom -> failure_mode -> corrective action` path, not by mutating the ontology JSON.
- Historical stats shown in the UI are exact-path stats, not generic failure-mode popularity.
- The versioned API under `/v1/kg-agents/*` is the contract to consume from other applications.
- The preferred local workflow is a single process: start `kg_agents`, use `/docs` for the API and `/dev-ui` to test the same instance-scoped routes the external frontend consumes.
