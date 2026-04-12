from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BASE_DIR = Path(__file__).resolve().parent.parent
KG_DIR = Path(__file__).resolve().parent
DATA_DIR = KG_DIR / "data"
LEGACY_APP_DIR = BASE_DIR / "troubleshooting_agent"
SEED_DIR = LEGACY_APP_DIR
ENGINE_DIR = KG_DIR / "engine"
DEV_UI_DIR = KG_DIR / "dev_ui"
DEFAULT_ONTOLOGY_PATH = BASE_DIR / "ontology.json"
DEFAULT_EMBEDDINGS_PATH = LEGACY_APP_DIR / "symptom_embeddings.json"
DEFAULT_TELEMETRY_PATH = LEGACY_APP_DIR / "telemetry" / "telemetry_p1p.csv"
DEFAULT_MANUALS_DIR = LEGACY_APP_DIR / "manuals"

# Ensure data directories exist
DATA_DIR.mkdir(exist_ok=True)
(DATA_DIR / "instances").mkdir(exist_ok=True)
(DATA_DIR / "extraction_summaries").mkdir(exist_ok=True)

OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-large"
OPENAI_CHAT_MODEL: str = "gpt-5-mini"

SIMILARITY_THRESHOLD: float = 0.45
HIGH_CONFIDENCE_THRESHOLD: float = 0.75
TOP_K_SYMPTOMS: int = 3

AGENT_PORT: int = int(os.getenv("AGENT_PORT", "8030"))

TELEMETRY_WINDOW_HOURS: int = 48
