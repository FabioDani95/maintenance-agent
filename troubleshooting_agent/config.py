from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BASE_DIR = Path(__file__).resolve().parent.parent
ONTOLOGY_PATH = BASE_DIR / "ontology.json"
EMBEDDINGS_PATH = Path(__file__).resolve().parent / "symptom_embeddings.json"

OPENAI_API_KEY: str = os.environ["OPENAI_API_KEY"]
OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-large"
OPENAI_CHAT_MODEL: str = "gpt-5-mini"

SIMILARITY_THRESHOLD: float = 0.45
HIGH_CONFIDENCE_THRESHOLD: float = 0.75
TOP_K_SYMPTOMS: int = 3

AGENT_PORT: int = int(os.getenv("AGENT_PORT", "5001"))

# Telemetry
TELEMETRY_DIR = Path(__file__).resolve().parent / "telemetry"
TELEMETRY_PATH = TELEMETRY_DIR / "telemetry_p1p.csv"
TELEMETRY_WINDOW_HOURS: int = 48
