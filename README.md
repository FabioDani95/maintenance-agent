# Maintenance Agent

Repository for a knowledge-grounded troubleshooting platform built around a product ontology.

In this branch, the primary application is `kg_agents/`: a FastAPI backend that exposes versioned APIs for agents, ontology instances, chat, graph data, and device mappings.

`troubleshooting_agent/` is still kept in the repo because:

- it contains the legacy Flask compatibility app
- it still holds legacy assets such as manuals and the old standalone UI shell
- its engine-facing Python modules now act as compatibility wrappers around `kg_agents/engine`

The old ontology editor under `modify/` has been removed from this branch.

## Repository Structure

```text
maintenance-agent/
├── device/                   # auxiliary assets / experiments
├── kg_agents/                # primary FastAPI application for this branch
│   ├── data/                 # JSON persistence for kg_agents (source of truth)
│   ├── engine/               # shared troubleshooting engine used by the API
│   └── dev_ui/               # local development UI served by FastAPI
├── scripts/                  # support scripts
├── troubleshooting_agent/    # legacy Flask compatibility layer
├── ontology.json             # default seeded ontology instance
├── ontology_schema.JSON      # default ontology schema
├── sources.csv               # source material used to build the ontology
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
- seeds a default maintenance agent and a default IRC5 instance on startup (idempotent)

Main characteristics:

- FastAPI app with Swagger at `/docs` and ReDoc at `/redoc`
- local development UI served by the same backend at `/dev-ui` with `/` redirecting there
- persistence on local JSON files under `kg_agents/data/`
- chat pipeline based on symptom embeddings, deterministic cause reranking, graph traversal, and grounded responses
- explicit no-fit fallback when the user names a technical entity that is not supported by any retrieved troubleshooting path
- shared troubleshooting engine lives in `kg_agents/engine`

Run it with:

```bash
python3 -m kg_agents.main
```

Default port: `8030`

Full API and architecture details: [kg_agents/README.md](kg_agents/README.md)

## Secondary Application: `troubleshooting_agent`

`troubleshooting_agent` is the older single-instance Flask app. It is no longer the recommended local run path because the same repository now exposes the dev UI from `kg_agents` itself.

Run it with:

```bash
python3 troubleshooting_agent/agent.py
```

Default port: `5001`

Details and local endpoints: [troubleshooting_agent/README.md](troubleshooting_agent/README.md)

## Data Model

The default troubleshooting flow assumes an ontology shaped around:

- `Symptom`
- `FailureMode`
- `CorrectiveAction`

and operational relationships such as:

- `MAY_INDICATE`
- `RESOLVED_BY`
- `AFFECTS`

`kg_agents` stores concrete data per instance under `kg_agents/data/instances/<instance_id>/`, including:

- `meta.json`
- `ontology.json`
- `symptom_embeddings.json`
- `devices.json`
- optional telemetry files

The repository root still contains the default seed ontology and schema used to initialize the first instance.

## Notes

- `OPENAI_API_KEY` is required for runtime embeddings used by symptom retrieval and candidate cause reranking.
- If you change `Symptom` nodes, regenerate embeddings before using the updated instance.
- The versioned API under `/v1/kg-agents/*` is the contract to consume from other applications.
- The preferred local workflow is a single process: start `kg_agents`, use `/docs` for the API and `/dev-ui` to test the same instance-scoped routes the external frontend consumes.
