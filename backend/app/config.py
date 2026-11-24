"""Application settings, read once from environment variables (and backend/.env).

Everything configurable lives here so it is easy to find and explain.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
load_dotenv(BACKEND_DIR / ".env")


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


# --- Database -------------------------------------------------------------
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://reqpilot:reqpilot@localhost:5433/reqpilot"
)

# --- OpenAI (the only runtime LLM provider) --------------------------------
# The key stays on the backend; it is never returned by any API endpoint.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIMENSIONS = 1536  # fixed size of the pgvector column
OPENAI_TIMEOUT_SECONDS = _int("OPENAI_TIMEOUT_SECONDS", 120)
OPENAI_MAX_RETRIES = _int("OPENAI_MAX_RETRIES", 3)
LLM_CONCURRENCY = _int("LLM_CONCURRENCY", 4)  # parallel LLM calls for batched agents
LLM_BATCH_SIZE = _int("LLM_BATCH_SIZE", 20)  # requirements per LLM call

# When no API key is configured ReqPilot runs in clearly-labelled DEMONSTRATION
# mode: every agent uses its deterministic rule-based baseline only.
DEMO_MODE = not OPENAI_API_KEY

# --- Security --------------------------------------------------------------
SESSION_HOURS = _int("SESSION_HOURS", 12)
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 10)
MAX_REQUIREMENTS_CHARS = _int("MAX_REQUIREMENTS_CHARS", 300_000)
DEMO_USER_PASSWORD = os.getenv("DEMO_USER_PASSWORD", "ReqPilot@2026")
DEFAULT_RETENTION_DAYS = _int("DEFAULT_RETENTION_DAYS", 365)
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")

# --- Content folders -------------------------------------------------------
KNOWLEDGE_BASE_DIR = PROJECT_ROOT / "knowledge_base"
EVALUATION_DIR = PROJECT_ROOT / "evaluation"
