"""Lightweight, idempotent schema migrations.

ReqPilot creates tables with SQLAlchemy `create_all`, which creates missing
TABLES but never adds COLUMNS to tables that already exist. When the
two-stage analysis was introduced, existing tables gained a few columns; this
module adds them to an existing database (PostgreSQL or SQLite) without
touching any data. New tables (clarification questions, responses, stakeholder
interactions, requirement changes, regeneration runs) are created by
`create_all` itself.

Each entry is (table, column, SQL type, SQL default). Running it again is safe:
columns that already exist are skipped.
"""
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

ADDED_COLUMNS = [
    # Two-stage analysis and versioning
    ("analyses", "stage1_status", "VARCHAR(30)", "'completed'"),  # analyses created before = one-shot runs
    ("analyses", "stage2_status", "VARCHAR(30)", "'not_started'"),
    ("analyses", "version", "INTEGER", "1"),
    ("analyses", "parent_analysis_id", "INTEGER", None),
    ("analyses", "root_analysis_id", "INTEGER", None),
    ("analyses", "preliminary", "JSON", "'{}'"),
    ("analyses", "submission_hash", "VARCHAR(64)", "''"),
    # Stage-specific agent log
    ("agent_runs", "stage", "VARCHAR(20)", "'stage1'"),
    # Supplementary information kept apart from the original requirement text
    ("requirements", "supplementary", "JSON", "'[]'"),
    # Resolution status of findings after clarification
    ("findings", "resolution", "VARCHAR(30)", "''"),
    ("findings", "clarification_refs", "JSON", "'[]'"),
    ("trace_records", "clarifications", "JSON", "'[]'"),
    # Human risk acceptance
    ("risks", "status", "VARCHAR(30)", "'open'"),
]


def migrate(engine: Engine) -> list[str]:
    """Add missing columns; returns the list of columns that were added."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    added = []
    with engine.begin() as conn:
        for table, column, sql_type, default in ADDED_COLUMNS:
            if table not in tables:
                continue  # create_all will create the table with every column
            existing = {c["name"] for c in inspector.get_columns(table)}
            if column in existing:
                continue
            ddl = f'ALTER TABLE {table} ADD COLUMN "{column}" {sql_type}'
            if default is not None:
                ddl += f" DEFAULT {default}"
            conn.execute(text(ddl))
            added.append(f"{table}.{column}")
        if "analyses.stage1_status" in added:
            # Analyses that existed before the upgrade keep their real outcome.
            conn.execute(text("UPDATE analyses SET stage1_status = 'failed' WHERE status = 'failed'"))
    return added
