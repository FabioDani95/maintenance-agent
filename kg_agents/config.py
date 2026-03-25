from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SEED_DIR = BASE_DIR / "troubleshooting_agent"

# Ensure data directories exist
DATA_DIR.mkdir(exist_ok=True)
(DATA_DIR / "instances").mkdir(exist_ok=True)

OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-large"
OPENAI_CHAT_MODEL: str = "gpt-5-mini"

SIMILARITY_THRESHOLD: float = 0.45
HIGH_CONFIDENCE_THRESHOLD: float = 0.75
TOP_K_SYMPTOMS: int = 3

AGENT_PORT: int = int(os.getenv("AGENT_PORT", "8030"))

TELEMETRY_WINDOW_HOURS: int = 48
