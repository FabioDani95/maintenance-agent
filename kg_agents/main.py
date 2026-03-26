from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

# Add troubleshooting_agent to sys.path for legacy module imports
_TS_DIR = Path(__file__).resolve().parent.parent / "troubleshooting_agent"
if str(_TS_DIR) not in sys.path:
    sys.path.insert(0, str(_TS_DIR))

from kg_agents.routers import agents, instances, chat, graph, devices
from kg_agents.services.agent_store import seed_default_agent
from kg_agents.services.instance_store import seed_default_instance


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Seed default data on startup
    agent_id = seed_default_agent()
    seed_default_instance(agent_id)
    yield


app = FastAPI(
    title="Knowledge Agents API",
    description="Multi-agent knowledge graph platform for troubleshooting and diagnostics",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(agents.router)
app.include_router(instances.router)
app.include_router(chat.router)
app.include_router(graph.router)
app.include_router(devices.router)


# Mount static files from troubleshooting_agent
_STATIC_DIR = _TS_DIR / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# Serve manuals
_MANUALS_DIR = Path(__file__).resolve().parent.parent / "data" / "manuals"
if _MANUALS_DIR.exists():
    app.mount("/manuals", StaticFiles(directory=str(_MANUALS_DIR)), name="manuals")


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """Serve the standalone HTML UI."""
    template_path = _TS_DIR / "templates" / "index.html"
    if not template_path.exists():
        return HTMLResponse("<h1>Template not found</h1>", status_code=404)
    with template_path.open("r", encoding="utf-8") as f:
        html = f.read()
    # Replace Flask template tags with direct paths
    html = html.replace("{{ url_for('static', filename='css/app.css') }}", "/static/css/app.css")
    html = html.replace("{{ url_for('static', filename='js/app.js') }}", "/static/js/app.js")
    return HTMLResponse(html)


if __name__ == "__main__":
    import uvicorn
    from kg_agents.config import AGENT_PORT
    uvicorn.run("kg_agents.main:app", host="0.0.0.0", port=AGENT_PORT, reload=True)
