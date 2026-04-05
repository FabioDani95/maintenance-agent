# Maintenance Agent

Repository for a knowledge-grounded troubleshooting platform built around a product ontology.

In this branch, the primary application is `kg_agents/`: a FastAPI backend that exposes versioned APIs for agents, ontology instances, chat, graph data, and device mappings.

`troubleshooting_agent/` is still kept in the repo because:

- it contains the domain logic reused by `kg_agents`
- it contains the legacy standalone UI and local single-instance app, useful to inspect the troubleshooting flow while working in this repository

The old ontology editor under `modify/` has been removed from this branch.

## Repository Structure

```text
maintenance-agent/
├── data/                     # JSON persistence for kg_agents
├── device/                   # auxiliary assets / experiments
├── kg_agents/                # primary FastAPI application for this branch
├── scripts/                  # support scripts
├── troubleshooting_agent/    # shared troubleshooting engine + legacy Flask UI
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
- seeds a default maintenance agent and a default Bambu Lab P1P instance on startup

Main characteristics:

- FastAPI app with Swagger at `/docs` and ReDoc at `/redoc`
- persistence on local JSON files under `data/`
- chat pipeline based on symptom embeddings, graph traversal, and deterministic grounded responses
- reuses the troubleshooting modules from `troubleshooting_agent/`

Run it with:

```bash
python3 -m kg_agents.main
```

Default port: `8030`

Full API and architecture details: [kg_agents/README.md](/Users/fabio.daniele/Coding/maintenance-agent/kg_agents/README.md)

## Secondary Application: `troubleshooting_agent`

`troubleshooting_agent` is the older single-instance Flask app. It is not the main integration target for this branch, but it remains useful for local inspection because it ships the standalone UI, graph view, PDF manual overlay, and telemetry panel.

Run it with:

```bash
python3 troubleshooting_agent/agent.py
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

The repository root still contains the default seed ontology and schema used to initialize the first instance.

## Notes

- `OPENAI_API_KEY` is required to generate query embeddings.
- If you change `Symptom` nodes, regenerate embeddings before using the updated instance.
- The versioned API under `/v1/kg-agents/*` is the contract to consume from other applications.
