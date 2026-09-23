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


#: Tables the knowledge-base phase adds (architecture G.5, plus G.6 ``evidence``).
KNOWLEDGE_BASE_TABLES = {
    "normative_source",
    "control",
    "knowledge_item",
    "knowledge_chunk",
    "source_allowlist",
    "evidence",
}


#: Tables the extraction-and-classification phase adds (architecture G.3, G.4,
#: G.7, M.5): the project corpus, proposals, labels, criteria, the review queue,
#: and prompt and model provenance.
EXTRACTION_TABLES = {
    "source_document",
    "source_chunk",
    "extraction_candidate",
    "requirement_classification",
    "acceptance_criterion",
    "review_item",
    "prompt_template",
    "model_version",
}

#: Tables the elicitation-and-clarification phase adds (architecture G.3, G.4):
#: stakeholders, interview sessions, utterances, quality findings, clarifications.
ELICITATION_TABLES = {
    "stakeholder",
    "interview_session",
    "utterance",
    "quality_finding",
    "clarification",
}


#: Tables the quality-and-conflict phase adds (architecture G.4, G.5): conflicts
#: and the project glossary.
QUALITY_TABLES = {"conflict", "glossary_term"}

#: Tables the compliance-and-security phase adds (architecture G.6): candidate
#: compliance mappings and their evidence links, rule-engine gaps, and derived
#: security/privacy findings and their evidence links.
COMPLIANCE_TABLES = {
    "compliance_mapping",
    "compliance_mapping_evidence",
    "compliance_gap",
    "security_privacy_finding",
    "security_privacy_finding_evidence",
}


def test_migration_creates_nothing_beyond_the_current_phase(migrated_db) -> None:
    """The schema must not run ahead of the roadmap.

    This assertion moves forward one phase at a time: each phase adds its own
    table set here, and anything outside the union is a table that arrived early.
    """
    present = set(inspect(migrated_db).get_table_names()) - {"alembic_version"}
    permitted = (
        FOUNDATION_TABLES
        | REQUIREMENTS_REPOSITORY_TABLES
        | KNOWLEDGE_BASE_TABLES
        | EXTRACTION_TABLES
        | ELICITATION_TABLES
        | QUALITY_TABLES
        | COMPLIANCE_TABLES
    )
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


def test_downgrading_p2_removes_exactly_the_knowledge_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rolling P2 back leaves P1 and the foundation intact, including ``project``."""
    url = sqlite_url(tmp_path / "p2-down.db")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = alembic_config(url)
    command.upgrade(config, "head")
    command.downgrade(config, "0003_p1_postgres_enum_repair")

    engine = create_engine(url, future=True)
    try:
        inspector = inspect(engine)
        present = set(inspector.get_table_names()) - {"alembic_version"}
        assert not present & KNOWLEDGE_BASE_TABLES, "downgrade left P2 tables behind"
        assert present >= FOUNDATION_TABLES | REQUIREMENTS_REPOSITORY_TABLES
        project_columns = {c["name"] for c in inspector.get_columns("project")}
        assert not {"jurisdiction_scope", "kb_version_pin"} & project_columns
    finally:
        engine.dispose()
        get_settings.cache_clear()
    command.upgrade(config, "head")


def test_downgrading_p3_removes_exactly_the_extraction_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rolling P3 back leaves P0-P2 intact, including ``agent_run`` as P0 made it."""
    url = sqlite_url(tmp_path / "p3-down.db")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = alembic_config(url)
    command.upgrade(config, "head")
    command.downgrade(config, "0004_p2_knowledge_base")

    engine = create_engine(url, future=True)
    try:
        inspector = inspect(engine)
        present = set(inspector.get_table_names()) - {"alembic_version"}
        assert not present & EXTRACTION_TABLES, "downgrade left P3 tables behind"
        assert present >= FOUNDATION_TABLES | REQUIREMENTS_REPOSITORY_TABLES | KNOWLEDGE_BASE_TABLES
        agent_run_columns = {c["name"] for c in inspector.get_columns("agent_run")}
        assert not {"attempts", "error_code", "review_signal", "cost_estimate", "finished_at"} & (
            agent_run_columns
        )
    finally:
        engine.dispose()
        get_settings.cache_clear()
    command.upgrade(config, "head")


def test_downgrading_p4_removes_exactly_the_elicitation_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rolling P4 back leaves P0-P3 intact."""
    url = sqlite_url(tmp_path / "p4-down.db")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = alembic_config(url)
    command.upgrade(config, "head")
    command.downgrade(config, "0005_p3_extraction")
    engine = create_engine(url, future=True)
    try:
        present = set(inspect(engine).get_table_names())
        assert not present & ELICITATION_TABLES, "downgrade left P4 tables behind"
        assert present >= (
            FOUNDATION_TABLES
            | REQUIREMENTS_REPOSITORY_TABLES
            | KNOWLEDGE_BASE_TABLES
            | EXTRACTION_TABLES
        )
    finally:
        engine.dispose()
        get_settings.cache_clear()
    command.upgrade(config, "head")


def test_downgrading_p5_removes_exactly_the_quality_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rolling P5 back leaves P0-P4 intact, with the P4 finding columns only."""
    url = sqlite_url(tmp_path / "p5-down.db")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = alembic_config(url)
    command.upgrade(config, "head")
    command.downgrade(config, "0006_p4_elicitation")
    engine = create_engine(url, future=True)
    try:
        inspector = inspect(engine)
        present = set(inspector.get_table_names())
        assert not present & QUALITY_TABLES, "downgrade left P5 tables behind"
        assert present >= ELICITATION_TABLES
        finding_columns = {c["name"] for c in inspector.get_columns("quality_finding")}
        assert not {"rule_id", "evidence", "resolution_reason"} & finding_columns
    finally:
        engine.dispose()
        get_settings.cache_clear()
    command.upgrade(config, "head")


def test_downgrading_p6_removes_exactly_the_compliance_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rolling P6 back leaves P0-P5 intact, without the P6 composite keys."""
    url = sqlite_url(tmp_path / "p6-down.db")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = alembic_config(url)
    command.upgrade(config, "head")
    command.downgrade(config, "0007_p5_quality_conflict")
    engine = create_engine(url, future=True)
    try:
        inspector = inspect(engine)
        present = set(inspector.get_table_names())
        assert not present & COMPLIANCE_TABLES, "downgrade left P6 tables behind"
        assert present >= QUALITY_TABLES | KNOWLEDGE_BASE_TABLES
        evidence_indexes = {i["name"] for i in inspector.get_indexes("evidence")}
        assert "uq_evidence_id" not in evidence_indexes
    finally:
        engine.dispose()
        get_settings.cache_clear()
    command.upgrade(config, "head")


def test_every_revision_id_fits_the_alembic_version_column() -> None:
    """``alembic_version.version_num`` is VARCHAR(32) on PostgreSQL.

    SQLite does not enforce the length, so a longer revision id passes every
    SQLite test and then fails the first real upgrade. Found in P3; pinned here.
    """
    import re

    for path in sorted((REPO_ROOT / "alembic" / "versions").glob("*.py")):
        match = re.search(r'^revision: str = "([^"]+)"', path.read_text(encoding="utf-8"), re.M)
        assert match is not None, f"{path.name} declares no revision"
        assert len(match.group(1)) <= 32, f"{path.name}: revision id longer than 32 characters"
