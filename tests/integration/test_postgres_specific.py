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


# ---------------------------------------------------------------------------
# Requirements-repository phase: the database-level baseline invariant
# ---------------------------------------------------------------------------


def test_p1_tables_exist(pg_engine) -> None:
    expected = {
        "requirement",
        "requirement_version",
        "approval_task",
        "approval_decision",
        "baseline",
        "baseline_member",
    }
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        ).scalars()
        present = set(rows)
    assert expected <= present


def test_baseline_member_trigger_exists(pg_engine) -> None:
    """The second of the three layers protecting the baseline invariant."""
    with pg_engine.connect() as conn:
        row = conn.execute(
            text("SELECT 1 FROM pg_trigger WHERE tgname = 'baseline_member_requires_approval'")
        ).first()
    assert row is not None, "the baseline-membership trigger is missing"


def test_database_refuses_an_unapproved_baseline_member(pg_engine) -> None:
    """Even a direct SQL insert cannot put an unapproved version in a baseline.

    This is the check that makes the invariant a property of the database rather
    than of the service layer alone.
    """
    with pg_engine.begin() as conn:
        project = conn.execute(
            text(
                "INSERT INTO project (id, name, domain, lifecycle_state, created_at) "
                "VALUES (gen_random_uuid(), 'trigger-test', 'loan', 'elicitation', now()) "
                "RETURNING id"
            )
        ).scalar()
        requirement = conn.execute(
            text(
                "INSERT INTO requirement (id, project_id, human_id, created_at) "
                "VALUES (gen_random_uuid(), :p, 'FR-TRG-001', now()) RETURNING id"
            ),
            {"p": project},
        ).scalar()
        version = conn.execute(
            text(
                "INSERT INTO requirement_version "
                "(id, requirement_id, project_id, version_no, state, statement, "
                " dependencies, assumptions, source_refs, content_hash, created_at) "
                "VALUES (gen_random_uuid(), :r, :p, 1, 'CANDIDATE', 'unapproved', "
                " '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, repeat('a', 64), now()) "
                "RETURNING id"
            ),
            {"r": requirement, "p": project},
        ).scalar()
        task = conn.execute(
            text(
                "INSERT INTO approval_task (id, project_id, gate, subject_type, subject_id, "
                " subject_version_hash, required_role, status, blocking, created_at) "
                "VALUES (gen_random_uuid(), :p, 'G1', 'requirement_version', :v, "
                " repeat('a', 64), 'analyst', 'OPEN', true, now()) RETURNING id"
            ),
            {"p": project, "v": version},
        ).scalar()
        decision = conn.execute(
            text(
                "INSERT INTO approval_decision (id, task_id, project_id, decided_by, "
                " role_exercised, decision, subject_version_hash, decided_at) "
                "VALUES (gen_random_uuid(), :t, :p, gen_random_uuid(), 'ANALYST', "
                " 'APPROVE', repeat('a', 64), now()) RETURNING id"
            ),
            {"t": task, "p": project},
        ).scalar()
        baseline = conn.execute(
            text(
                "INSERT INTO baseline (id, project_id, label, approval_decision_id, frozen_at) "
                "VALUES (gen_random_uuid(), :p, 'trigger-test', :d, now()) RETURNING id"
            ),
            {"p": project, "d": decision},
        ).scalar()

    with pg_engine.begin() as conn, pytest.raises(Exception) as excinfo:
        conn.execute(
            text(
                "INSERT INTO baseline_member "
                "(id, baseline_id, requirement_version_id, project_id, version_hash, created_at) "
                "VALUES (gen_random_uuid(), :b, :v, :p, repeat('a', 64), now())"
            ),
            {"b": baseline, "v": version, "p": project},
        )
    assert "unapproved requirement version" in str(excinfo.value).lower()
