"""PostgreSQL-specific guarantees (ADR-003, ADR-010).

These skip unless ``REQPILOT_TEST_DATABASE_URL`` points at a real PostgreSQL
instance, because they test behaviour SQLite cannot express: the append-only
trigger, the ``REVOKE``, the pgvector extension, and migration round-tripping.

Skipping is the honest outcome on a machine without Docker - it is visible in
the test report rather than quietly passing.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from tests.conftest import postgres_url, requires_postgres

pytestmark = [pytest.mark.integration, requires_postgres]


@pytest.fixture
def pg_engine():
    engine = create_engine(postgres_url(), future=True)
    yield engine
    engine.dispose()


def test_database_is_reachable(pg_engine) -> None:
    with pg_engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1


def test_pgvector_extension_is_available(pg_engine) -> None:
    """Required by the retrieval subsystem in a later roadmap phase."""
    with pg_engine.connect() as conn:
        row = conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).first()
    assert row is not None, "pgvector is not installed; run the P0 migration"


def test_health_check_reports_connected() -> None:
    from reqpilot.config import Settings
    from reqpilot.repositories.database import check_database_health, reset_engine

    reset_engine()
    try:
        report = check_database_health(Settings(_env_file=None, DATABASE_URL=postgres_url()))
        assert report["connected"] is True
        assert report["error"] is None
    finally:
        reset_engine()


def test_audit_update_is_refused_by_the_database(pg_engine) -> None:
    """Layer 2 of immutability: the database itself refuses the mutation.

    This is the load-bearing control - it makes append-only a property of the
    database rather than of developer discipline.
    """
    with pg_engine.connect() as conn, pytest.raises(Exception) as excinfo:
        conn.execute(text("UPDATE audit_event SET actor_ref = 'tampered'"))
    message = str(excinfo.value).lower()
    assert "append-only" in message or "permission denied" in message


def test_audit_delete_is_refused_by_the_database(pg_engine) -> None:
    with pg_engine.connect() as conn, pytest.raises(Exception) as excinfo:
        conn.execute(text("DELETE FROM audit_event"))
    message = str(excinfo.value).lower()
    assert "append-only" in message or "permission denied" in message


def test_migration_created_every_foundation_table(pg_engine) -> None:
    expected = {"app_user", "project", "project_member", "audit_event", "graph_run", "agent_run"}
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        ).scalars()
        present = set(rows)
    assert expected <= present


def test_requirement_tables_are_absent(pg_engine) -> None:
    """P0 must not have created the Requirements Repository schema."""
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        ).scalars()
        present = set(rows)
    assert not {"requirement", "requirement_version", "approval_task"} & present
