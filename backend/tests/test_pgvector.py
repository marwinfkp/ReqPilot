"""The pgvector code path of the agent retrieval against a real PostgreSQL database.

The rest of the suite runs on SQLite (where similarity is computed in Python), so
this test starts a separate process with DATABASE_URL pointing to PostgreSQL and a
temporary schema (see pgvector_check.py). It is skipped when PostgreSQL is not
reachable. Database: REQPILOT_TEST_PG_URL, else the default development database.
"""
import json
import os
import subprocess
import sys
import uuid

import pytest

from app import config

PG_URL = os.getenv("REQPILOT_TEST_PG_URL",
                   "postgresql+psycopg://reqpilot:reqpilot@localhost:5433/reqpilot")


def _reachable(url: str) -> bool:
    try:
        import psycopg

        with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://"), connect_timeout=3) as conn:
            return conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone() is not None
    except Exception:
        return False


@pytest.mark.skipif(not _reachable(PG_URL), reason="PostgreSQL with pgvector is not reachable")
def test_agent_retrieval_uses_pgvector_cosine_distance():
    schema = f"reqpilot_test_{uuid.uuid4().hex[:10]}"
    env = {**os.environ, "DATABASE_URL": PG_URL, "OPENAI_API_KEY": "", "PYTHONDONTWRITEBYTECODE": "1"}
    import psycopg

    plain_url = PG_URL.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(plain_url, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        documents_before = conn.execute("SELECT count(*) FROM public.knowledge_documents").fetchone()
    try:
        result = subprocess.run([sys.executable, "-m", "tests.pgvector_check", schema], cwd=config.BACKEND_DIR,
                                env=env, capture_output=True, text=True, timeout=120)
    finally:
        with psycopg.connect(plain_url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            documents_after = conn.execute("SELECT count(*) FROM public.knowledge_documents").fetchone()
    assert result.returncode == 0, result.stderr[-2000:]
    assert documents_after == documents_before  # the application's own tables were not touched
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["security_method"] == "semantic (pgvector)" and data["uses_cosine_operator"]
    # Nearest first; the closer engineering-guideline chunk is filtered out by document type.
    assert data["security_refs"] == ["PG-SEC-1", "PG-SEC-2"]
    assert data["security_scores"][0] > data["security_scores"][1]
    assert data["quality_refs"] == ["PG-ENG-1"] and data["page"] == 1
    # A failing pgvector query: lexical fallback, the session still works, semantic search pauses.
    assert data["failure_method"] == "lexical" and data["failure_refs"][0] == "PG-SEC-2"
    assert data["session_usable_after_failure"] and data["method_while_paused"] == "lexical"
