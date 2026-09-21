"""P1 requirements repository: requirements, versions, approvals, baselines.

Additive only. The foundation migration is not touched.

The part carrying architectural weight is the baseline-membership trigger at the
end: on PostgreSQL it refuses to insert a ``baseline_member`` whose version is
not APPROVED or BASELINED, and refuses one whose project does not match the
baseline's. That is the database-level layer of the invariant in architecture
H.4 - the service layer is the first layer, this is the second.

Revision ID: 0002_p1_requirements_repository
Revises: 0001_p0_foundation
Create Date: P1
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_p1_requirements_repository"
down_revision: str | None = "0001_p0_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


BASELINE_MEMBER_GUARD_SQL = """
CREATE OR REPLACE FUNCTION reqpilot_check_baseline_member()
RETURNS trigger AS $$
DECLARE
    v_state TEXT;
    v_project UUID;
    b_project UUID;
BEGIN
    SELECT state::TEXT, project_id INTO v_state, v_project
    FROM requirement_version WHERE id = NEW.requirement_version_id;

    IF v_state IS NULL THEN
        RAISE EXCEPTION 'baseline member references a nonexistent requirement version';
    END IF;

    IF v_state NOT IN ('APPROVED', 'BASELINED') THEN
        RAISE EXCEPTION
            'no unapproved requirement version may enter a baseline: version % is %',
            NEW.requirement_version_id, v_state;
    END IF;

    SELECT project_id INTO b_project FROM baseline WHERE id = NEW.baseline_id;

    IF b_project IS DISTINCT FROM v_project OR b_project IS DISTINCT FROM NEW.project_id THEN
        RAISE EXCEPTION
            'a baseline may not contain a requirement version from another project';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER baseline_member_requires_approval
    BEFORE INSERT ON baseline_member
    FOR EACH ROW EXECUTE FUNCTION reqpilot_check_baseline_member();
"""

BASELINE_MEMBER_GUARD_DOWN_SQL = """
DROP TRIGGER IF EXISTS baseline_member_requires_approval ON baseline_member;
DROP FUNCTION IF EXISTS reqpilot_check_baseline_member();
"""


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    json_type = postgresql.JSONB() if is_postgres else sa.JSON()

    requirement_state = sa.Enum(
        "CANDIDATE",
        "EXTRACTED",
        "CLASSIFIED",
        "ANALYZED",
        "CLARIFICATION_REQUIRED",
        "CLARIFIED",
        "VALIDATED",
        "PENDING_APPROVAL",
        "APPROVED",
        "REJECTED",
        "BASELINED",
        "SUPERSEDED",
        "WITHDRAWN",
        "INVALID",
        name="requirement_state_enum",
    )
    requirement_category = sa.Enum(
        "BUSINESS",
        "STAKEHOLDER",
        "FUNCTIONAL",
        "SECURITY",
        "PRIVACY",
        "REGULATORY",
        "PERFORMANCE",
        "AVAILABILITY",
        "USABILITY",
        "DATA_MANAGEMENT",
        "INTEGRATION",
        "AUDIT_REPORTING",
        "OPERATIONAL",
        name="requirement_category_enum",
    )
    requirement_priority = sa.Enum(
        "MUST", "SHOULD", "COULD", "WONT", name="requirement_priority_enum"
    )
    gate_enum = sa.Enum("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", name="gate_enum")
    task_status = sa.Enum(
        "OPEN", "APPROVED", "REJECTED", "CANCELLED", name="approval_task_status_enum"
    )
    decision_type = sa.Enum("APPROVE", "REJECT", "MODIFY", name="approval_decision_type_enum")

    # --- requirement -----------------------------------------------------
    op.create_table(
        "requirement",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("human_id", sa.String(length=64), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("baselined_version_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_requirement_project_id_project",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_requirement"),
        sa.UniqueConstraint("project_id", "human_id", name="uq_requirement_project_human_id"),
    )
    op.create_index("ix_requirement_project_id", "requirement", ["project_id"])
    op.create_index("ix_requirement_project_human", "requirement", ["project_id", "human_id"])

    # --- requirement_version --------------------------------------------
    op.create_table(
        "requirement_version",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("requirement_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("state", requirement_state, nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=True),
        sa.Column("category", requirement_category, nullable=True),
        sa.Column("priority", requirement_priority, nullable=True),
        sa.Column("justification", sa.Text(), nullable=True),
        sa.Column("dependencies", json_type, nullable=False),
        sa.Column("assumptions", json_type, nullable=False),
        sa.Column("source_refs", json_type, nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("review_signal", sa.Float(), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["requirement.id"],
            name="fk_requirement_version_requirement_id_requirement",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_requirement_version_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["requirement_version.id"],
            name="fk_requirement_version_superseded_by_id_requirement_version",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_requirement_version"),
        sa.UniqueConstraint(
            "requirement_id", "version_no", name="uq_requirement_version_requirement_version_no"
        ),
        sa.CheckConstraint("version_no >= 1", name="ck_requirement_version_version_no_positive"),
    )
    op.create_index(
        "ix_requirement_version_requirement_id", "requirement_version", ["requirement_id"]
    )
    op.create_index("ix_requirement_version_project_id", "requirement_version", ["project_id"])
    op.create_index("ix_requirement_version_state", "requirement_version", ["project_id", "state"])

    # --- approval_task ---------------------------------------------------
    op.create_table(
        "approval_task",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("gate", gate_enum, nullable=False),
        sa.Column("task_group_id", sa.Uuid(), nullable=True),
        sa.Column("subject_type", sa.String(length=100), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("subject_version", sa.String(length=50), nullable=True),
        sa.Column("subject_version_hash", sa.String(length=64), nullable=False),
        # Singular by design (architecture G.7): a gate needing several roles is
        # one task per role sharing a task_group_id, not a widened column.
        sa.Column(
            "required_role",
            sa.Enum(
                "ANALYST",
                "STAKEHOLDER",
                "COMPLIANCE_OFFICER",
                "SECURITY_REVIEWER",
                "PROJECT_MANAGER",
                "AUDITOR",
                "KB_ADMIN",
                name="role_enum",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("status", task_status, nullable=False),
        sa.Column("blocking", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_approval_task_project_id_project",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_approval_task"),
    )
    op.create_index("ix_approval_task_project_id", "approval_task", ["project_id"])
    op.create_index("ix_approval_task_project_status", "approval_task", ["project_id", "status"])
    op.create_index("ix_approval_task_group", "approval_task", ["task_group_id"])
    op.create_index(
        "ix_approval_task_subject", "approval_task", ["project_id", "subject_type", "subject_id"]
    )

    # --- approval_decision (append-only) ---------------------------------
    op.create_table(
        "approval_decision",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=False),
        sa.Column(
            "role_exercised",
            sa.Enum(
                "ANALYST",
                "STAKEHOLDER",
                "COMPLIANCE_OFFICER",
                "SECURITY_REVIEWER",
                "PROJECT_MANAGER",
                "AUDITOR",
                "KB_ADMIN",
                name="role_enum",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("decision", decision_type, nullable=False),
        sa.Column("justification", sa.Text(), nullable=True),
        sa.Column("subject_version_hash", sa.String(length=64), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["approval_task.id"],
            name="fk_approval_decision_task_id_approval_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_approval_decision_project_id_project",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_approval_decision"),
    )
    op.create_index("ix_approval_decision_project_id", "approval_decision", ["project_id"])
    op.create_index("ix_approval_decision_task", "approval_decision", ["task_id"])

    # --- baseline --------------------------------------------------------
    op.create_table(
        "baseline",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("approval_decision_id", sa.Uuid(), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_baseline_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approval_decision_id"],
            ["approval_decision.id"],
            name="fk_baseline_approval_decision_id_approval_decision",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_baseline"),
        sa.UniqueConstraint("project_id", "label", name="uq_baseline_project_label"),
    )
    op.create_index("ix_baseline_project_id", "baseline", ["project_id"])
    op.create_index("ix_baseline_project", "baseline", ["project_id"])

    op.create_table(
        "baseline_member",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("baseline_id", sa.Uuid(), nullable=False),
        sa.Column("requirement_version_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("version_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["baseline_id"],
            ["baseline.id"],
            name="fk_baseline_member_baseline_id_baseline",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requirement_version_id"],
            ["requirement_version.id"],
            name="fk_baseline_member_requirement_version_id_requirement_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_baseline_member_project_id_project",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_baseline_member"),
        sa.UniqueConstraint(
            "baseline_id", "requirement_version_id", name="uq_baseline_member_baseline_version"
        ),
    )
    op.create_index("ix_baseline_member_project_id", "baseline_member", ["project_id"])
    op.create_index("ix_baseline_member_baseline", "baseline_member", ["baseline_id"])

    # --- the baseline invariant, at database level -----------------------
    if is_postgres:
        op.execute(BASELINE_MEMBER_GUARD_SQL)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(BASELINE_MEMBER_GUARD_DOWN_SQL)

    op.drop_table("baseline_member")
    op.drop_table("baseline")
    op.drop_table("approval_decision")
    op.drop_table("approval_task")
    op.drop_table("requirement_version")
    op.drop_table("requirement")

    for enum_name in (
        "approval_decision_type_enum",
        "approval_task_status_enum",
        "gate_enum",
        "requirement_priority_enum",
        "requirement_category_enum",
        "requirement_state_enum",
    ):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
