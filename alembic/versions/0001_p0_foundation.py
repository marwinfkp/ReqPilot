"""P0 foundation schema: identity, projects, audit, runs.

Creates only the tables the foundation itself needs. The requirement lifecycle
tables belong to the Requirements Repository phase and are deliberately absent.

The part of this migration that carries architectural weight is the audit
immutability block at the end: a ``BEFORE UPDATE OR DELETE`` trigger plus a
``REVOKE``. Together they make append-only a property of the database rather
than of developer discipline (architecture ADR-010).

Revision ID: 0001_p0_foundation
Revises:
Create Date: P0
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_p0_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The application role that must be denied UPDATE/DELETE on audit_event.
# Read from the connection so the migration works whatever role is configured.
AUDIT_IMMUTABILITY_SQL = """
CREATE OR REPLACE FUNCTION reqpilot_forbid_audit_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'audit_event is append-only; % is not permitted (architecture ADR-010)',
        TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_event_append_only
    BEFORE UPDATE OR DELETE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION reqpilot_forbid_audit_mutation();
"""

AUDIT_IMMUTABILITY_DOWN_SQL = """
DROP TRIGGER IF EXISTS audit_event_append_only ON audit_event;
DROP FUNCTION IF EXISTS reqpilot_forbid_audit_mutation();
"""


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        # Required by the retrieval subsystem in a later roadmap phase. Created
        # now so the database foundation is complete and the health check can
        # report it.
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    json_type = postgresql.JSONB() if is_postgres else sa.JSON()

    # --- identity ------------------------------------------------------
    op.create_table(
        "app_user",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_app_user"),
        sa.UniqueConstraint("email", name="uq_app_user_email"),
    )

    op.create_table(
        "project",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("domain", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("lifecycle_state", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_project"),
    )

    role_enum = sa.Enum(
        "ANALYST",
        "STAKEHOLDER",
        "COMPLIANCE_OFFICER",
        "SECURITY_REVIEWER",
        "PROJECT_MANAGER",
        "AUDITOR",
        "KB_ADMIN",
        name="role_enum",
    )
    op.create_table(
        "project_member",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", role_enum, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_project_member_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name="fk_project_member_user_id_app_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_project_member"),
        sa.UniqueConstraint(
            "project_id", "user_id", "role", name="ck_project_member_project_user_role"
        ),
    )
    op.create_index("ix_project_member_project_id", "project_member", ["project_id"])
    op.create_index("ix_project_member_user_id", "project_member", ["user_id"])

    # --- audit (append-only) -------------------------------------------
    op.create_table(
        "audit_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        # Chain position. Ordering by `seq` rather than by timestamp keeps the
        # chain unambiguous when several appends share a wall-clock value.
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "actor_kind",
            sa.Enum("HUMAN", "AGENT_ROLE", "SYSTEM", name="actor_kind_enum"),
            nullable=False,
        ),
        sa.Column("actor_ref", sa.String(length=200), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(
                "RUN_STARTED",
                "RUN_SUSPENDED",
                "RUN_RESUMED",
                "RUN_COMPLETED",
                "RUN_FAILED",
                "NODE_STARTED",
                "NODE_COMPLETED",
                "NODE_FAILED",
                "PROJECT_CREATED",
                "PROJECT_DELETED",
                "MEMBER_ADDED",
                "PERMISSION_DENIED",
                name="audit_event_type_enum",
            ),
            nullable=False,
        ),
        sa.Column("subject_type", sa.String(length=100), nullable=True),
        sa.Column("subject_id", sa.String(length=100), nullable=True),
        sa.Column("subject_version", sa.String(length=50), nullable=True),
        sa.Column("graph_run_id", sa.Uuid(), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=True),
        sa.Column("row_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_audit_event_project_id_project",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_event"),
        sa.UniqueConstraint("project_id", "seq", name="uq_audit_event_project_seq"),
    )
    op.create_index("ix_audit_event_project_id", "audit_event", ["project_id"])
    op.create_index("ix_audit_event_graph_run_id", "audit_event", ["graph_run_id"])
    op.create_index("ix_audit_event_project_seq", "audit_event", ["project_id", "seq"])
    op.create_index(
        "ix_audit_event_subject", "audit_event", ["project_id", "subject_type", "subject_id"]
    )

    # --- runs -----------------------------------------------------------
    op.create_table(
        "graph_run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("graph_name", sa.String(length=100), nullable=False),
        sa.Column("thread_id", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "RUNNING",
                "SUSPENDED",
                "STALLED",
                "COMPLETED",
                "FAILED",
                name="graph_run_status_enum",
            ),
            nullable=False,
        ),
        sa.Column("started_by", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_graph_run_project_id_project",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_graph_run"),
        sa.UniqueConstraint("thread_id", name="uq_graph_run_thread_id"),
    )
    op.create_index("ix_graph_run_project_id", "graph_run", ["project_id"])

    op.create_table(
        "agent_run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("graph_run_id", sa.Uuid(), nullable=False),
        sa.Column("node", sa.String(length=100), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "COORDINATOR",
                "STAKEHOLDER_INTERACTION",
                "REQUIREMENT_EXTRACTION",
                "CLARIFICATION",
                "CLASSIFICATION",
                "CONFLICT_DETECTION",
                "COMPLIANCE",
                "SECURITY_PRIVACY",
                "RISK_ANALYSIS",
                "SDLC_SELECTION",
                "DOCUMENTATION",
                "VALIDATION",
                "HUMAN_APPROVAL",
                name="agent_role_enum",
            ),
            nullable=False,
        ),
        sa.Column("prompt_template_id", sa.String(length=100), nullable=True),
        sa.Column("model_version_id", sa.String(length=100), nullable=True),
        sa.Column("input_refs", json_type, nullable=False),
        sa.Column("output_refs", json_type, nullable=False),
        sa.Column("evidence_ids", json_type, nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("OK", "PARTIAL", "FAILED", name="agent_run_status_enum"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["graph_run_id"],
            ["graph_run.id"],
            name="fk_agent_run_graph_run_id_graph_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_run"),
    )
    op.create_index("ix_agent_run_graph_run_id", "agent_run", ["graph_run_id"])

    # --- audit immutability (ADR-010) -----------------------------------
    # Layer 2 of 3. Layer 1 is that the application exposes no mutation path;
    # layer 3 is the hash chain on each row.
    if is_postgres:
        op.execute(AUDIT_IMMUTABILITY_SQL)
        # Defence in depth: even a direct SQL session as the application role
        # cannot UPDATE or DELETE audit rows. CURRENT_USER is used rather than a
        # queried role name so the statement also works in offline (--sql) mode.
        op.execute("REVOKE UPDATE, DELETE ON audit_event FROM CURRENT_USER")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("GRANT UPDATE, DELETE ON audit_event TO CURRENT_USER")
        op.execute(AUDIT_IMMUTABILITY_DOWN_SQL)

    op.drop_table("agent_run")
    op.drop_table("graph_run")
    op.drop_table("audit_event")
    op.drop_table("project_member")
    op.drop_table("project")
    op.drop_table("app_user")

    for enum_name in (
        "agent_run_status_enum",
        "agent_role_enum",
        "graph_run_status_enum",
        "audit_event_type_enum",
        "actor_kind_enum",
        "role_enum",
    ):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
