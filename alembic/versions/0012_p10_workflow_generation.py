"""P10 workflow generation: the generated project workflow, its provenance and change log.

Additive only. No earlier migration is touched; no existing column changes.

What carries architectural weight:

* ``workflow`` / ``workflow_phase`` / ``workflow_activity`` / ``workflow_gate``
  (architecture G.8): a workflow names the exact G6-selected SDLC run, the selected
  candidate row and the baseline it realises through composite foreign keys (so
  only rows *of its own project*), pins the template version and content hash, keeps
  the generated structure unchanged forever, and is idempotent per (run, approved
  inputs). A partial unique index allows **one live workflow per project**, and
  another allows **one production-readiness gate per workflow**.
* ``workflow_source`` - the provenance of every derived element (N.2 #24-#26) - and
  ``workflow_change`` - the ``FR-WFL-007`` change log - are **append-only**.
* The children's keys, kinds, ``mandatory`` flags, phases and ordering are fixed;
  only their wording moves, and only through the edit path that writes a
  change-log row. A mandatory activity and any gate or phase is never deleted
  (PostgreSQL triggers below; the ORM guard in ``domain/models/workflow.py``).
* PostgreSQL: the P10 audit event types. They are not removed on downgrade:
  PostgreSQL cannot drop an enum value, and audit history is kept.
* ``traceability_link``'s allowlist ``CHECK`` is widened to the P10 triples
  (architecture N.2 #24-#26, plus the two P10 additions). Downgrade restores the P9
  allowlist exactly, deleting any P10 edges first.

**No ReqPilot gate is added.** The ``gate_enum`` type and ``approval_task`` are
untouched: production readiness is a *row of the generated workflow*
(architecture M.4), not a ninth platform gate.

Revision ID: 0012_p10_workflow_generation
Revises: 0011_p9_sdlc_recommendation
Create Date: P10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_p10_workflow_generation"
down_revision: str | None = "0011_p9_sdlc_recommendation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

P10_AUDIT_EVENT_TYPES = (
    "WORKFLOW_GENERATED",
    "WORKFLOW_GENERATION_REFUSED",
    "WORKFLOW_EDITED",
    "WORKFLOW_EXPORTED",
    "WORKFLOW_SUPERSEDED",
)
WORKFLOW_STATUSES = ("COMPLETE", "OPEN_ITEMS", "SUPERSEDED")
TRACE_CHECK = "ck_traceability_link_allowed_triple"
P10_NODE_TYPES = ("workflow", "workflow_activity", "workflow_gate")

ACTIVITY_KINDS_SQL = (
    "kind IN ('template', 'security', 'risk_treatment', 'risk_verification', 'manual')"
)
GATE_KINDS_SQL = "kind IN ('phase_approval', 'compliance_checkpoint', 'production_readiness')"
ORIGINS_SQL = "origin IN ('generated', 'edited', 'manual')"
SOURCE_TYPES_SQL = (
    "source_type IN ('sdlc_candidate', 'compliance_mapping', 'risk', 'risk_mitigation', "
    "'security_privacy_finding')"
)
ELEMENT_TYPES_SQL = "element_type IN ('workflow', 'phase', 'activity', 'gate')"
OPERATIONS_SQL = "operation IN ('update', 'add', 'remove')"
LIVE_WORKFLOW_SQL = "status <> 'SUPERSEDED'"

GUARDS_SQL = """
-- Provenance and the change log are append-only (the P3 function; the
-- project-deletion cascade still passes at depth > 1).
CREATE TRIGGER workflow_source_append_only
    BEFORE UPDATE OR DELETE ON workflow_source
    FOR EACH ROW EXECUTE FUNCTION reqpilot_project_content_append_only();
CREATE TRIGGER workflow_change_append_only
    BEFORE UPDATE OR DELETE ON workflow_change
    FOR EACH ROW EXECUTE FUNCTION reqpilot_project_content_append_only();

-- A workflow's run, candidate, baseline, template, inputs and generated structure
-- are fixed at insertion; only its status (one way, to SUPERSEDED), revision,
-- current hash and open items move, and a superseded workflow never moves again.
CREATE OR REPLACE FUNCTION reqpilot_workflow_guard()
RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'workflows are never deleted; a workflow''s history is preserved';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.sdlc_run_id IS DISTINCT FROM OLD.sdlc_run_id
       OR NEW.selected_candidate_id IS DISTINCT FROM OLD.selected_candidate_id
       OR NEW.candidate_key IS DISTINCT FROM OLD.candidate_key
       OR NEW.baseline_id IS DISTINCT FROM OLD.baseline_id
       OR NEW.graph_run_id IS DISTINCT FROM OLD.graph_run_id
       OR NEW.supersedes_workflow_id IS DISTINCT FROM OLD.supersedes_workflow_id
       OR NEW.template_ref IS DISTINCT FROM OLD.template_ref
       OR NEW.templates_sha256 IS DISTINCT FROM OLD.templates_sha256
       OR NEW.input_fingerprint IS DISTINCT FROM OLD.input_fingerprint
       OR NEW.generated_structure::text IS DISTINCT FROM OLD.generated_structure::text
       OR NEW.generated_hash IS DISTINCT FROM OLD.generated_hash
       OR NEW.generated_by IS DISTINCT FROM OLD.generated_by
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION
            'a workflow''s run, candidate, template and generated structure are immutable';
    END IF;
    IF OLD.status = 'SUPERSEDED' THEN
        RAISE EXCEPTION 'a superseded workflow never changes again';
    END IF;
    IF NEW.revision < OLD.revision THEN
        RAISE EXCEPTION 'a workflow''s revision never goes back';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER workflow_guard
    BEFORE UPDATE OR DELETE ON workflow
    FOR EACH ROW EXECUTE FUNCTION reqpilot_workflow_guard();

-- A child's identity, parent, ordering, kind and mandatory flag are fixed; phases
-- and gates are never deleted, and an activity only when it is not mandatory.
CREATE OR REPLACE FUNCTION reqpilot_workflow_element_guard()
RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        -- Nested so OLD.mandatory is only read on the table that has it.
        IF TG_TABLE_NAME = 'workflow_activity' THEN
            IF NOT OLD.mandatory THEN
                RETURN OLD;
            END IF;
        END IF;
        RAISE EXCEPTION '% rows that are mandatory, and all phases and gates, are never deleted',
            TG_TABLE_NAME;
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.workflow_id IS DISTINCT FROM OLD.workflow_id
       OR NEW.position IS DISTINCT FROM OLD.position
       OR NEW.key IS DISTINCT FROM OLD.key
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION '% identity, parent and ordering are fixed at generation', TG_TABLE_NAME;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION reqpilot_workflow_derived_guard()
RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        RETURN NEW;
    END IF;
    IF NEW.phase_id IS DISTINCT FROM OLD.phase_id
       OR NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.mandatory IS DISTINCT FROM OLD.mandatory THEN
        RAISE EXCEPTION '% phase, kind and mandatory flag are fixed at generation', TG_TABLE_NAME;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER workflow_phase_guard
    BEFORE UPDATE OR DELETE ON workflow_phase
    FOR EACH ROW EXECUTE FUNCTION reqpilot_workflow_element_guard();
CREATE TRIGGER workflow_activity_guard
    BEFORE UPDATE OR DELETE ON workflow_activity
    FOR EACH ROW EXECUTE FUNCTION reqpilot_workflow_element_guard();
CREATE TRIGGER workflow_gate_guard
    BEFORE UPDATE OR DELETE ON workflow_gate
    FOR EACH ROW EXECUTE FUNCTION reqpilot_workflow_element_guard();
CREATE TRIGGER workflow_activity_derived_guard
    BEFORE UPDATE ON workflow_activity
    FOR EACH ROW EXECUTE FUNCTION reqpilot_workflow_derived_guard();
CREATE TRIGGER workflow_gate_derived_guard
    BEFORE UPDATE ON workflow_gate
    FOR EACH ROW EXECUTE FUNCTION reqpilot_workflow_derived_guard();

CREATE OR REPLACE FUNCTION reqpilot_workflow_phase_shape_guard()
RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        RETURN NEW;
    END IF;
    IF NEW.stages::text IS DISTINCT FROM OLD.stages::text
       OR NEW.cycle IS DISTINCT FROM OLD.cycle
       OR NEW.verifies_phase_key IS DISTINCT FROM OLD.verifies_phase_key THEN
        RAISE EXCEPTION 'a workflow phase''s stages, cycle and V-Model pairing are fixed';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER workflow_phase_shape_guard
    BEFORE UPDATE ON workflow_phase
    FOR EACH ROW EXECUTE FUNCTION reqpilot_workflow_phase_shape_guard();
"""

GUARDS_DOWN_SQL = """
DROP TRIGGER IF EXISTS workflow_phase_shape_guard ON workflow_phase;
DROP FUNCTION IF EXISTS reqpilot_workflow_phase_shape_guard();
DROP TRIGGER IF EXISTS workflow_gate_derived_guard ON workflow_gate;
DROP TRIGGER IF EXISTS workflow_activity_derived_guard ON workflow_activity;
DROP FUNCTION IF EXISTS reqpilot_workflow_derived_guard();
DROP TRIGGER IF EXISTS workflow_gate_guard ON workflow_gate;
DROP TRIGGER IF EXISTS workflow_activity_guard ON workflow_activity;
DROP TRIGGER IF EXISTS workflow_phase_guard ON workflow_phase;
DROP FUNCTION IF EXISTS reqpilot_workflow_element_guard();
DROP TRIGGER IF EXISTS workflow_guard ON workflow;
DROP FUNCTION IF EXISTS reqpilot_workflow_guard();
DROP TRIGGER IF EXISTS workflow_change_append_only ON workflow_change;
DROP TRIGGER IF EXISTS workflow_source_append_only ON workflow_source;
"""


def _trace_check(*, before_p10: bool) -> str:
    # Imported here so the migration reads the allowlist the code enforces.
    from reqpilot.domain.traceability import allowed_triple_check_sql

    return allowed_triple_check_sql(before_p10=before_p10)


def _replace_trace_check(*, before_p10: bool) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint(TRACE_CHECK, "traceability_link", type_="check")
        op.create_check_constraint(
            TRACE_CHECK, "traceability_link", sa.text(_trace_check(before_p10=before_p10))
        )
        return
    # SQLite cannot alter a CHECK in place: rebuild the table (batch mode).
    with op.batch_alter_table("traceability_link", recreate="always") as batch:
        batch.drop_constraint(TRACE_CHECK, type_="check")
        batch.create_check_constraint(TRACE_CHECK, sa.text(_trace_check(before_p10=before_p10)))


def _json(is_postgres: bool) -> sa.types.TypeEngine:  # type: ignore[type-arg]
    return postgresql.JSONB() if is_postgres else sa.JSON()


def _timestamps() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    json_type = _json(is_postgres)

    if is_postgres:
        for value in P10_AUDIT_EVENT_TYPES:
            op.execute(f"ALTER TYPE audit_event_type_enum ADD VALUE IF NOT EXISTS '{value}'")
        postgresql.ENUM(*WORKFLOW_STATUSES, name="workflow_status_enum").create(
            bind, checkfirst=True
        )
        status_type: sa.types.TypeEngine = postgresql.ENUM(  # type: ignore[type-arg]
            *WORKFLOW_STATUSES, name="workflow_status_enum", create_type=False
        )
    else:
        status_type = sa.Enum(*WORKFLOW_STATUSES, name="workflow_status_enum")

    op.create_table(
        "workflow",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("sdlc_run_id", sa.Uuid(), nullable=False),
        sa.Column("selected_candidate_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_key", sa.String(60), nullable=False),
        sa.Column("baseline_id", sa.Uuid(), nullable=False),
        sa.Column("graph_run_id", sa.Uuid(), nullable=True),
        sa.Column("supersedes_workflow_id", sa.Uuid(), nullable=True),
        sa.Column("status", status_type, nullable=False),
        sa.Column("template_ref", sa.String(100), nullable=False),
        sa.Column("templates_sha256", sa.String(64), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("generated_structure", json_type, nullable=False),
        sa.Column("generated_hash", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("open_items", json_type, nullable=False),
        sa.Column("generated_by", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_workflow"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_workflow_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["graph_run_id"],
            ["graph_run.id"],
            name="fk_workflow_graph_run_id_graph_run",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("id", "project_id", name="uq_workflow_id_project"),
        sa.UniqueConstraint(
            "project_id", "sdlc_run_id", "input_fingerprint", name="uq_workflow_run_inputs"
        ),
        sa.ForeignKeyConstraint(
            ["sdlc_run_id", "project_id"],
            ["sdlc_run.id", "sdlc_run.project_id"],
            name="fk_workflow_sdlc_run",
        ),
        sa.ForeignKeyConstraint(
            ["selected_candidate_id", "project_id"],
            ["sdlc_candidate.id", "sdlc_candidate.project_id"],
            name="fk_workflow_selected_candidate",
        ),
        sa.ForeignKeyConstraint(
            ["baseline_id", "project_id"],
            ["baseline.id", "baseline.project_id"],
            name="fk_workflow_baseline",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_workflow_id", "project_id"],
            ["workflow.id", "workflow.project_id"],
            name="fk_workflow_supersedes",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_workflow_revision_positive"),
        sa.CheckConstraint(
            "length(generated_hash) = 64", name="ck_workflow_generated_hash_present"
        ),
        sa.CheckConstraint("length(content_hash) = 64", name="ck_workflow_content_hash_present"),
        sa.CheckConstraint(
            "length(input_fingerprint) = 64", name="ck_workflow_input_fingerprint_present"
        ),
    )
    op.create_index("ix_workflow_project", "workflow", ["project_id", "created_at"])
    op.create_index(
        "uq_workflow_one_live_per_project",
        "workflow",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text(LIVE_WORKFLOW_SQL),
        sqlite_where=sa.text(LIVE_WORKFLOW_SQL),
    )

    op.create_table(
        "workflow_phase",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(60), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("stages", json_type, nullable=False),
        sa.Column("cycle", sa.String(200), nullable=True),
        sa.Column("verifies_phase_key", sa.String(60), nullable=True),
        sa.Column("responsible_roles", json_type, nullable=False),
        sa.Column("deliverables", json_type, nullable=False),
        sa.Column("entry_criteria", json_type, nullable=False),
        sa.Column("exit_criteria", json_type, nullable=False),
        sa.Column("testing_requirements", json_type, nullable=False),
        sa.Column("traceability_requirements", json_type, nullable=False),
        sa.Column("origin", sa.String(20), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_phase"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_workflow_phase_project_id_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id", "project_id", name="uq_workflow_phase_id_project"),
        sa.UniqueConstraint("id", "workflow_id", name="uq_workflow_phase_id_workflow"),
        sa.UniqueConstraint("workflow_id", "position", name="uq_workflow_phase_position"),
        sa.UniqueConstraint("workflow_id", "key", name="uq_workflow_phase_key"),
        sa.ForeignKeyConstraint(
            ["workflow_id", "project_id"],
            ["workflow.id", "workflow.project_id"],
            name="fk_workflow_phase_workflow",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("position >= 1", name="ck_workflow_phase_position_positive"),
        sa.CheckConstraint(ORIGINS_SQL, name="ck_workflow_phase_origin_known"),
    )
    op.create_index("ix_workflow_phase_workflow", "workflow_phase", ["workflow_id"])

    for table, kinds_sql, derived_sql, text_column in (
        (
            "workflow_activity",
            ACTIVITY_KINDS_SQL,
            "kind IN ('template', 'manual') OR mandatory",
            "description",
        ),
        (
            "workflow_gate",
            GATE_KINDS_SQL,
            "kind = 'phase_approval' OR mandatory",
            "purpose",
        ),
    ):
        extra: list[sa.Column] = (  # type: ignore[type-arg]
            [
                sa.Column("responsible_roles", json_type, nullable=False),
                sa.Column("deliverables", json_type, nullable=False),
            ]
            if table == "workflow_activity"
            else [
                sa.Column("approver_roles", json_type, nullable=False),
                sa.Column("required_evidence", json_type, nullable=False),
                sa.Column("entry_criteria", json_type, nullable=False),
                sa.Column("exit_criteria", json_type, nullable=False),
            ]
        )
        op.create_table(
            table,
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("project_id", sa.Uuid(), nullable=False),
            sa.Column("workflow_id", sa.Uuid(), nullable=False),
            sa.Column("phase_id", sa.Uuid(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("key", sa.String(200), nullable=False),
            sa.Column("kind", sa.String(30), nullable=False),
            sa.Column("name", sa.String(300), nullable=False),
            sa.Column(text_column, sa.Text(), nullable=False),
            *extra,
            sa.Column("mandatory", sa.Boolean(), nullable=False),
            sa.Column("origin", sa.String(20), nullable=False),
            *_timestamps(),
            sa.PrimaryKeyConstraint("id", name=f"pk_{table}"),
            sa.ForeignKeyConstraint(
                ["project_id"],
                ["project.id"],
                name=f"fk_{table}_project_id_project",
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint("id", "project_id", name=f"uq_{table}_id_project"),
            sa.UniqueConstraint("workflow_id", "key", name=f"uq_{table}_key"),
            sa.UniqueConstraint("phase_id", "position", name=f"uq_{table}_position"),
            sa.ForeignKeyConstraint(
                ["workflow_id", "project_id"],
                ["workflow.id", "workflow.project_id"],
                name=f"fk_{table}_workflow",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["phase_id", "workflow_id"],
                ["workflow_phase.id", "workflow_phase.workflow_id"],
                name=f"fk_{table}_phase",
                ondelete="CASCADE",
            ),
            sa.CheckConstraint("position >= 1", name=f"ck_{table}_position_positive"),
            sa.CheckConstraint(kinds_sql, name=f"ck_{table}_kind_known"),
            sa.CheckConstraint(ORIGINS_SQL, name=f"ck_{table}_origin_known"),
            sa.CheckConstraint(derived_sql, name=f"ck_{table}_derived_mandatory"),
        )
        op.create_index(f"ix_{table}_workflow", table, ["workflow_id"])
    op.create_index(
        "uq_workflow_gate_one_production_readiness",
        "workflow_gate",
        ["workflow_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'production_readiness'"),
        sqlite_where=sa.text("kind = 'production_readiness'"),
    )

    op.create_table(
        "workflow_source",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("element_type", sa.String(20), nullable=False),
        sa.Column("element_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("relation", sa.String(30), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_source"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_workflow_source_project_id_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "workflow_id",
            "element_type",
            "element_id",
            "source_type",
            "source_id",
            "relation",
            name="uq_workflow_source_link",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "project_id"],
            ["workflow.id", "workflow.project_id"],
            name="fk_workflow_source_workflow",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(ELEMENT_TYPES_SQL, name="ck_workflow_source_element_type_known"),
        sa.CheckConstraint(SOURCE_TYPES_SQL, name="ck_workflow_source_source_type_known"),
    )
    op.create_index("ix_workflow_source_workflow", "workflow_source", ["workflow_id"])
    op.create_index(
        "ix_workflow_source_source", "workflow_source", ["project_id", "source_type", "source_id"]
    )

    op.create_table(
        "workflow_change",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(20), nullable=False),
        sa.Column("element_type", sa.String(20), nullable=False),
        sa.Column("element_id", sa.Uuid(), nullable=False),
        sa.Column("element_key", sa.String(200), nullable=False),
        sa.Column("changes", json_type, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("role_exercised", sa.String(40), nullable=False),
        sa.Column("content_hash_before", sa.String(64), nullable=False),
        sa.Column("content_hash_after", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_change"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_workflow_change_project_id_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workflow_id", "revision", name="uq_workflow_change_revision"),
        sa.ForeignKeyConstraint(
            ["workflow_id", "project_id"],
            ["workflow.id", "workflow.project_id"],
            name="fk_workflow_change_workflow",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("revision >= 2", name="ck_workflow_change_revision_after_generation"),
        sa.CheckConstraint(OPERATIONS_SQL, name="ck_workflow_change_operation_known"),
        sa.CheckConstraint(ELEMENT_TYPES_SQL, name="ck_workflow_change_element_type_known"),
        sa.CheckConstraint("length(trim(reason)) >= 1", name="ck_workflow_change_reason_recorded"),
    )
    op.create_index("ix_workflow_change_workflow", "workflow_change", ["workflow_id"])

    _replace_trace_check(before_p10=False)

    if is_postgres:
        op.execute(GUARDS_SQL)


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        op.execute(GUARDS_DOWN_SQL)
        # P10 trace edges cannot survive the P9 allowlist; they go with the tables
        # they point at. The append-only trigger is lifted for this one statement.
        op.execute("ALTER TABLE traceability_link DISABLE TRIGGER traceability_link_append_only")
    op.execute(
        "DELETE FROM traceability_link WHERE from_type IN ('workflow', 'workflow_activity', "
        "'workflow_gate') OR to_type IN ('workflow', 'workflow_activity', 'workflow_gate')"
    )
    if is_postgres:
        op.execute("ALTER TABLE traceability_link ENABLE TRIGGER traceability_link_append_only")
    _replace_trace_check(before_p10=True)

    op.drop_table("workflow_change")
    op.drop_table("workflow_source")
    op.drop_table("workflow_gate")
    op.drop_table("workflow_activity")
    op.drop_table("workflow_phase")
    op.drop_table("workflow")
    sa.Enum(name="workflow_status_enum").drop(bind, checkfirst=True)
    # The P10 audit event values stay on their enum (PostgreSQL cannot drop enum
    # values); audit history is kept.
