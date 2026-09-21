"""Migration tests (ADR-003).

Two things are checked without needing PostgreSQL:

1. The migration **executes** and creates every foundation table.
2. The migration and the ORM models **agree**. Schema drift between a hand-written
   migration and the models it is supposed to create is a classic silent failure,
   so it is asserted rather than assumed.

PostgreSQL-only behaviour - the append-only trigger, the ``REVOKE``, pgvector -
is covered in ``test_postgres_specific.py`` and skips without a live database.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from reqpilot.config import get_settings
from reqpilot.domain.models import Base

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Tables the foundation phase is responsible for.
FOUNDATION_TABLES = {
    "app_user",
    "project",
    "project_member",
    "audit_event",
    "graph_run",
    "agent_run",
}


def alembic_config(database_url: str) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def sqlite_url(path: Path) -> str:
    """Build a SQLite URL with forward slashes, as SQLAlchemy expects."""
    return f"sqlite+pysqlite:///{path.as_posix()}"


@pytest.fixture
def migrated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Apply the migration to a throwaway SQLite database."""
    url = sqlite_url(tmp_path / "migrated.db")
    monkeypatch.setenv("DATABASE_URL", url)
    # Settings are cached process-wide; clear so the patched URL is honoured
    # even if an earlier test already resolved them.
    get_settings.cache_clear()
    command.upgrade(alembic_config(url), "head")
    engine = create_engine(url, future=True)
    try:
        yield engine
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_migration_runs(migrated_db) -> None:
    assert inspect(migrated_db).get_table_names()


def test_migration_creates_every_foundation_table(migrated_db) -> None:
    present = set(inspect(migrated_db).get_table_names()) - {"alembic_version"}
    assert present >= FOUNDATION_TABLES


#: Tables the requirements-repository phase adds.
REQUIREMENTS_REPOSITORY_TABLES = {
    "requirement",
    "requirement_version",
    "approval_task",
    "approval_decision",
    "baseline",
    "baseline_member",
}


def test_migration_creates_nothing_beyond_the_current_phase(migrated_db) -> None:
    """The schema must not run ahead of the roadmap.

    This assertion moves forward one phase at a time: each phase adds its own
    table set here, and anything outside the union is a table that arrived early.
    """
    present = set(inspect(migrated_db).get_table_names()) - {"alembic_version"}
    permitted = FOUNDATION_TABLES | REQUIREMENTS_REPOSITORY_TABLES
    unexpected = present - permitted
    assert not unexpected, f"migrations created out-of-scope tables: {sorted(unexpected)}"


def test_migration_matches_the_orm_models(migrated_db) -> None:
    """Guard against drift between the hand-written migration and the models."""
    inspector = inspect(migrated_db)
    for table_name, table in Base.metadata.tables.items():
        assert table_name in inspector.get_table_names(), f"{table_name} missing from migration"
        migrated_columns = {c["name"] for c in inspector.get_columns(table_name)}
        model_columns = {c.name for c in table.columns}
        assert model_columns == migrated_columns, (
            f"{table_name} columns differ.\n"
            f"  only in models:    {sorted(model_columns - migrated_columns)}\n"
            f"  only in migration: {sorted(migrated_columns - model_columns)}"
        )


def test_audit_event_has_the_chain_columns(migrated_db) -> None:
    columns = {c["name"] for c in inspect(migrated_db).get_columns("audit_event")}
    assert {"seq", "prev_hash", "row_hash"} <= columns


def test_downgrade_removes_the_foundation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A migration that cannot be reversed is a migration that cannot be trusted."""
    url = sqlite_url(tmp_path / "down.db")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = alembic_config(url)

    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_engine(url, future=True)
    try:
        remaining = set(inspect(engine).get_table_names()) - {"alembic_version"}
        assert not remaining, f"downgrade left tables behind: {sorted(remaining)}"
    finally:
        engine.dispose()
