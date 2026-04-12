from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from kg_agents.config import AGENT_PORT, DATA_DIR, DEFAULT_MANUALS_DIR, DEV_UI_DIR
from kg_agents.routers import agents, chat, devices, graph, instances
from kg_agents.services.agent_store import seed_default_agent
from kg_agents.services.intervention_store import init_db
from kg_agents.services.instance_store import seed_default_instance


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
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

app.include_router(agents.router)
app.include_router(instances.router)
app.include_router(chat.router)
app.include_router(graph.router)
app.include_router(devices.router)

_DEV_UI_STATIC_DIR = DEV_UI_DIR / "static"
if _DEV_UI_STATIC_DIR.exists():
    app.mount("/dev-ui/static", StaticFiles(directory=str(_DEV_UI_STATIC_DIR)), name="dev-ui-static")

_ROOT_MANUALS_DIR = DATA_DIR / "manuals"
_MANUALS_DIR = _ROOT_MANUALS_DIR if _ROOT_MANUALS_DIR.exists() else DEFAULT_MANUALS_DIR
if _MANUALS_DIR.exists():
    app.mount("/manuals", StaticFiles(directory=str(_MANUALS_DIR)), name="manuals")


@app.get("/", include_in_schema=False)
async def index():
    return RedirectResponse(url="/dev-ui", status_code=307)


@app.get("/dev-ui", response_class=HTMLResponse, include_in_schema=False)
async def serve_dev_ui():
    template_path = DEV_UI_DIR / "templates" / "index.html"
    if not template_path.exists():
        return HTMLResponse("<h1>Dev UI not found</h1>", status_code=404)
    with template_path.open("r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


if __name__ == "__main__":
    import os
    import signal
    import subprocess
    import uvicorn

    # Kill any process already listening on the port before starting
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{AGENT_PORT}"],
            capture_output=True, text=True
        )
        pids = result.stdout.strip().split()
        for pid in pids:
            if pid:
                os.kill(int(pid), signal.SIGKILL)
                print(f"Killed existing process on port {AGENT_PORT} (PID {pid})")
        if pids:
            import time
            time.sleep(1)
    except Exception:
        pass

    uvicorn.run("kg_agents.main:app", host="0.0.0.0", port=AGENT_PORT, reload=True)
