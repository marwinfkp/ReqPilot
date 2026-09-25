"""P8 approval, traceability and documents: the trace graph and generated artefacts.

Additive only. No earlier migration is touched; no existing column changes.

What carries architectural weight:

* ``traceability_link`` (architecture N.1): **one typed edge table**. A ``CHECK``
  built from the closed allowlist in :mod:`reqpilot.domain.traceability` refuses
  any other ``(from_type, link_type, to_type)`` triple at the database, whatever
  wrote it, and a unique key refuses a duplicate edge. Rows are append-only
  (``FR-TRC-004``: history is never rewritten).
* ``artifact`` / ``artifact_version`` / ``artifact_section`` (architecture G.8,
  ``FR-DOC-008``, ``FR-DOC-009``): an artefact version is immutable, names the
  exact baseline it was rendered from through a composite foreign key (so it can
  only name a baseline *of its own project*), and carries the generation
  metadata. Sections are rows so that trace links can point at them.
* ``baseline`` gains a unique ``(id, project_id)`` so that composite key exists;
  ``approval_task`` gains a nullable ``assignee_user_id`` for the named party of
  a G4 co-approval (architecture M.3: "Analyst + affected stakeholders").

Revision ID: 0010_p8_trace_documents
Revises: 0009_p7_risk_register
Create Date: P8
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_p8_trace_documents"
down_revision: str | None = "0009_p7_risk_register"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

P8_AUDIT_EVENT_TYPES = (
    "ARCHITECTURE_CRITICAL_FLAGGED",
    "CHANGE_GATE_SETTLED",
    "CONFLICT_GATE_SETTLED",
    "TRACE_LINKS_SYNCED",
    "ARTIFACT_GENERATED",
    "ARTIFACT_VERSION_CREATED",
    "ARTIFACT_GENERATION_REFUSED",
    "ARTIFACT_EXPORTED",
)

ARTIFACT_TYPES = (
    "SRS",
    "RTM",
    "USER_STORIES",
    "USE_CASES",
    "COMPLIANCE_MATRIX",
    "RISK_REGISTER",
    "ASSUMPTIONS_DEPENDENCIES",
    "OPEN_ISSUES",
)

GUARDS_SQL = """
-- Trace links, artefact versions and artefact sections are append-only (the P3
-- function; the project-deletion cascade still passes at depth > 1).
CREATE TRIGGER traceability_link_append_only
    BEFORE UPDATE OR DELETE ON traceability_link
    FOR EACH ROW EXECUTE FUNCTION reqpilot_project_content_append_only();
CREATE TRIGGER artifact_version_append_only
    BEFORE UPDATE OR DELETE ON artifact_version
    FOR EACH ROW EXECUTE FUNCTION reqpilot_project_content_append_only();
CREATE TRIGGER artifact_section_append_only
    BEFORE UPDATE OR DELETE ON artifact_section
    FOR EACH ROW EXECUTE FUNCTION reqpilot_project_content_append_only();

-- An artefact's identity is fixed; only its current-version pointer moves, and
-- only to a version of the same artefact.
CREATE OR REPLACE FUNCTION reqpilot_artifact_guard()
RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'artefacts are never deleted; history is preserved';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.artifact_type IS DISTINCT FROM OLD.artifact_type
       OR NEW.title IS DISTINCT FROM OLD.title
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'an artefact''s identity is immutable';
    END IF;
    IF NEW.current_version_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM artifact_version v
        WHERE v.id = NEW.current_version_id AND v.artifact_id = NEW.id
    ) THEN
        RAISE EXCEPTION 'an artefact''s current version must be one of its own versions';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER artifact_guard
    BEFORE UPDATE OR DELETE ON artifact
    FOR EACH ROW EXECUTE FUNCTION reqpilot_artifact_guard();
"""

GUARDS_DOWN_SQL = """
DROP TRIGGER IF EXISTS artifact_guard ON artifact;
DROP FUNCTION IF EXISTS reqpilot_artifact_guard();
DROP TRIGGER IF EXISTS artifact_section_append_only ON artifact_section;
DROP TRIGGER IF EXISTS artifact_version_append_only ON artifact_version;
DROP TRIGGER IF EXISTS traceability_link_append_only ON traceability_link;
"""


def _allowed_triple_check() -> str:
    """The allowlist, from the one place it is defined (architecture N.1)."""
    from reqpilot.domain.traceability import allowed_triple_check_sql

    return allowed_triple_check_sql()


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    json_type = postgresql.JSONB() if is_postgres else sa.JSON()

    if is_postgres:
        for value in P8_AUDIT_EVENT_TYPES:
            op.execute(f"ALTER TYPE audit_event_type_enum ADD VALUE IF NOT EXISTS '{value}'")

    # --- existing tables: additive columns and keys ---------------------------
    op.add_column("approval_task", sa.Column("assignee_user_id", sa.Uuid(), nullable=True))
    if is_postgres:
        op.create_unique_constraint("uq_baseline_id", "baseline", ["id", "project_id"])
    else:
        # SQLite accepts a unique index as a foreign-key target; no table rebuild.
        op.create_index("uq_baseline_id", "baseline", ["id", "project_id"], unique=True)

    if is_postgres:
        postgresql.ENUM(*ARTIFACT_TYPES, name="artifact_type_enum").create(bind, checkfirst=True)
        artifact_type = postgresql.ENUM(
            *ARTIFACT_TYPES, name="artifact_type_enum", create_type=False
        )
    else:
        artifact_type = sa.Enum(*ARTIFACT_TYPES, name="artifact_type_enum")

    # --- the typed trace graph (N.1) ----------------------------------------
    op.create_table(
        "traceability_link",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("from_type", sa.String(40), nullable=False),
        sa.Column("from_id", sa.String(100), nullable=False),
        sa.Column("link_type", sa.String(40), nullable=False),
        sa.Column("to_type", sa.String(40), nullable=False),
        sa.Column("to_id", sa.String(100), nullable=False),
        sa.Column("anchor_version_id", sa.Uuid(), nullable=True),
        sa.Column("origin", sa.String(80), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["anchor_version_id", "project_id"],
            ["requirement_version.id", "requirement_version.project_id"],
            name="fk_traceability_link_anchor_version",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "project_id",
            "from_type",
            "from_id",
            "link_type",
            "to_type",
            "to_id",
            name="uq_traceability_link_project_id",
        ),
        sa.CheckConstraint(_allowed_triple_check(), name="ck_traceability_link_allowed_triple"),
        sa.CheckConstraint(
            "length(from_id) >= 1 AND length(to_id) >= 1",
            name="ck_traceability_link_ends_present",
        ),
    )
    op.create_index(
        "ix_traceability_link_from", "traceability_link", ["project_id", "from_type", "from_id"]
    )
    op.create_index(
        "ix_traceability_link_to", "traceability_link", ["project_id", "to_type", "to_id"]
    )
    op.create_index(
        "ix_traceability_link_anchor", "traceability_link", ["project_id", "anchor_version_id"]
    )

    # --- artefacts (G.8) -----------------------------------------------------
    op.create_table(
        "artifact",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_type", artifact_type, nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "artifact_type", name="uq_artifact_project_id"),
        sa.UniqueConstraint("id", "project_id", name="uq_artifact_id"),
    )
    op.create_index("ix_artifact_project_id", "artifact", ["project_id"])

    op.create_table(
        "artifact_version",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("artifact_type", artifact_type, nullable=False),
        sa.Column("baseline_id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.String(100), nullable=False),
        sa.Column("template_version", sa.String(20), nullable=False),
        sa.Column("generator", sa.String(100), nullable=False),
        sa.Column("model_identifier", sa.String(200), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=True),
        sa.Column("kb_version", sa.Integer(), nullable=True),
        sa.Column("kb_version_source", sa.String(20), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("structure", json_type, nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("markdown_sha256", sa.String(64), nullable=False),
        sa.Column("section_count", sa.Integer(), nullable=False),
        sa.Column("cited_version_count", sa.Integer(), nullable=False),
        sa.Column("generated_by", sa.Uuid(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["artifact_id", "project_id"],
            ["artifact.id", "artifact.project_id"],
            name="fk_artifact_version_artifact",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["baseline_id", "project_id"],
            ["baseline.id", "baseline.project_id"],
            name="fk_artifact_version_baseline",
        ),
        sa.UniqueConstraint("artifact_id", "version_no", name="uq_artifact_version_artifact_id"),
        sa.UniqueConstraint("id", "project_id", name="uq_artifact_version_id"),
        sa.CheckConstraint("version_no >= 1", name="ck_artifact_version_version_no_positive"),
        sa.CheckConstraint(
            "length(content_hash) = 64", name="ck_artifact_version_content_hash_present"
        ),
        sa.CheckConstraint(
            "length(model_identifier) >= 1", name="ck_artifact_version_model_identifier_present"
        ),
    )
    op.create_index("ix_artifact_version_project_id", "artifact_version", ["project_id"])
    op.create_index(
        "ix_artifact_version_artifact", "artifact_version", ["artifact_id", "version_no"]
    )
    op.create_index(
        "ix_artifact_version_baseline", "artifact_version", ["project_id", "baseline_id"]
    )

    op.create_table(
        "artifact_section",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("artifact_version_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("section_key", sa.String(120), nullable=False),
        sa.Column("number", sa.String(20), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["artifact_version_id", "project_id"],
            ["artifact_version.id", "artifact_version.project_id"],
            name="fk_artifact_section_version",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "artifact_version_id", "section_key", name="uq_artifact_section_artifact_version_id"
        ),
        sa.UniqueConstraint("id", "project_id", name="uq_artifact_section_id"),
    )
    op.create_index("ix_artifact_section_project_id", "artifact_section", ["project_id"])
    op.create_index(
        "ix_artifact_section_artifact_version_id", "artifact_section", ["artifact_version_id"]
    )

    if is_postgres:
        op.execute(GUARDS_SQL)


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        op.execute(GUARDS_DOWN_SQL)

    op.drop_table("artifact_section")
    op.drop_table("artifact_version")
    op.drop_table("artifact")
    op.drop_table("traceability_link")
    sa.Enum(name="artifact_type_enum").drop(bind, checkfirst=True)

    if is_postgres:
        op.drop_constraint("uq_baseline_id", "baseline", type_="unique")
    else:
        op.drop_index("uq_baseline_id", table_name="baseline")
    with op.batch_alter_table("approval_task") as batch:
        batch.drop_column("assignee_user_id")
    # The P8 audit event values stay on their enum (PostgreSQL cannot remove enum
    # values). Audit events are history and are kept.
