"""P5 quality and conflict detection: detected findings, conflicts, the glossary.

Additive only. No earlier migration is touched; no existing column changes.

What carries architectural weight:

* ``quality_finding`` (G.4) gains the detector's provenance - rule id, review
  signal, evidence, the related version of a duplicate, the run and agent run -
  and its human resolution. Content stays immutable; the status moves once,
  from ``open`` to ``resolved`` or ``dismissed``, with a reason.
* ``requirement_version`` gains a unique ``(id, project_id)`` so that findings
  and conflicts can reference a version *in their own project* with a composite
  foreign key.
* ``conflict`` (G.4; ``[DESIGN] D12``): a guard, never a lifecycle state. Both
  versions must be in its project (composite foreign keys); content immutable;
  at most one active conflict per version pair (a partial unique index); the
  status moves only forward, and only a human moves it.
* ``glossary_term`` (G.5): the project glossary undefined-term detection uses.

Triggers repeat, at the database, what the ORM guards already enforce. Rows may
still disappear through the project-deletion cascade (``FR-ADM-006``): those
deletes run nested in the foreign-key action, at ``pg_trigger_depth() > 1``.

Revision ID: 0007_p5_quality_conflict
Revises: 0006_p4_elicitation
Create Date: P5
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_p5_quality_conflict"
down_revision: str | None = "0006_p4_elicitation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

P5_AUDIT_EVENT_TYPES = (
    "QUALITY_FINDING_RESOLVED",
    "QUALITY_FINDING_DISMISSED",
    "CONFLICT_SHORTLISTED",
    "CONFLICT_PROPOSED",
    "CONFLICT_REVIEWED",
    "CONFLICT_RESOLVED",
    "CONFLICT_DISMISSED",
    "GLOSSARY_TERM_ADDED",
)
P5_FINDING_TYPES = (
    "NEAR_DUPLICATE",
    "INFEASIBILITY",
    "MISSING_SECURITY_CONSIDERATION",
    "MISSING_PRIVACY_CONSIDERATION",
)

#: Enum types this migration creates (and its downgrade drops).
ENUM_NAMES = (
    "conflict_class_enum",
    "conflict_kind_enum",
    "conflict_status_enum",
    "conflict_resolution_enum",
)

FINDING_COLUMNS = (
    "rule_id",
    "review_signal",
    "evidence",
    "related_version_id",
    "graph_run_id",
    "agent_run_id",
    "resolution_reason",
    "resolved_by",
    "resolved_at",
)

GUARDS_SQL = """
-- P5: a quality finding's content - including its detection provenance - is
-- immutable; its status moves once, with its resolution.
CREATE OR REPLACE FUNCTION reqpilot_quality_finding_guard()
RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'quality findings are never deleted';
    END IF;
    IF NEW.requirement_version_id IS DISTINCT FROM OLD.requirement_version_id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.finding_type IS DISTINCT FROM OLD.finding_type
       OR NEW.severity IS DISTINCT FROM OLD.severity
       OR NEW.rationale IS DISTINCT FROM OLD.rationale
       OR NEW.span_quote IS DISTINCT FROM OLD.span_quote
       OR NEW.detected_by IS DISTINCT FROM OLD.detected_by
       OR NEW.recorded_by IS DISTINCT FROM OLD.recorded_by
       OR NEW.rule_id IS DISTINCT FROM OLD.rule_id
       OR NEW.review_signal IS DISTINCT FROM OLD.review_signal
       OR NEW.evidence IS DISTINCT FROM OLD.evidence
       OR NEW.related_version_id IS DISTINCT FROM OLD.related_version_id
       OR NEW.graph_run_id IS DISTINCT FROM OLD.graph_run_id
       OR NEW.agent_run_id IS DISTINCT FROM OLD.agent_run_id THEN
        RAISE EXCEPTION 'a quality finding''s content is immutable';
    END IF;
    IF OLD.status <> 'OPEN' AND (
           NEW.status IS DISTINCT FROM OLD.status
           OR NEW.resolution_reason IS DISTINCT FROM OLD.resolution_reason
           OR NEW.resolved_by IS DISTINCT FROM OLD.resolved_by
           OR NEW.resolved_at IS DISTINCT FROM OLD.resolved_at) THEN
        RAISE EXCEPTION 'a quality finding is resolved or dismissed once';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- A conflict's versions, class and evidence are immutable; its status only
-- moves forward, and a closed conflict never reopens (D12, G4).
CREATE OR REPLACE FUNCTION reqpilot_conflict_guard()
RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'conflicts are never deleted';
    END IF;
    IF NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.version_a_id IS DISTINCT FROM OLD.version_a_id
       OR NEW.version_b_id IS DISTINCT FROM OLD.version_b_id
       OR NEW.conflict_class IS DISTINCT FROM OLD.conflict_class
       OR NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.rationale IS DISTINCT FROM OLD.rationale
       OR NEW.evidence_a IS DISTINCT FROM OLD.evidence_a
       OR NEW.evidence_b IS DISTINCT FROM OLD.evidence_b
       OR NEW.severity IS DISTINCT FROM OLD.severity
       OR NEW.review_signal IS DISTINCT FROM OLD.review_signal
       OR NEW.involves_stakeholder_disagreement
          IS DISTINCT FROM OLD.involves_stakeholder_disagreement
       OR NEW.stakeholder_a IS DISTINCT FROM OLD.stakeholder_a
       OR NEW.stakeholder_b IS DISTINCT FROM OLD.stakeholder_b
       OR NEW.detected_by IS DISTINCT FROM OLD.detected_by
       OR NEW.rule_id IS DISTINCT FROM OLD.rule_id
       OR NEW.recorded_by IS DISTINCT FROM OLD.recorded_by THEN
        RAISE EXCEPTION 'a conflict''s versions and evidence are immutable';
    END IF;
    IF OLD.status IN ('RESOLVED', 'DISMISSED') AND (
           NEW.status IS DISTINCT FROM OLD.status
           OR NEW.resolution IS DISTINCT FROM OLD.resolution
           OR NEW.resolution_reason IS DISTINCT FROM OLD.resolution_reason) THEN
        RAISE EXCEPTION 'a resolved or dismissed conflict is closed';
    END IF;
    IF OLD.status = 'UNDER_REVIEW' AND NEW.status = 'OPEN' THEN
        RAISE EXCEPTION 'a conflict under review does not reopen';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER conflict_guard
    BEFORE UPDATE OR DELETE ON conflict
    FOR EACH ROW EXECUTE FUNCTION reqpilot_conflict_guard();

-- Glossary terms are added, never rewritten (a new term, not an edit).
CREATE TRIGGER glossary_term_append_only
    BEFORE UPDATE OR DELETE ON glossary_term
    FOR EACH ROW EXECUTE FUNCTION reqpilot_project_content_append_only();
"""

#: The P4 form of the finding guard, restored by the downgrade.
P4_FINDING_GUARD_SQL = """
CREATE OR REPLACE FUNCTION reqpilot_quality_finding_guard()
RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'quality findings are never deleted';
    END IF;
    IF NEW.requirement_version_id IS DISTINCT FROM OLD.requirement_version_id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.finding_type IS DISTINCT FROM OLD.finding_type
       OR NEW.severity IS DISTINCT FROM OLD.severity
       OR NEW.rationale IS DISTINCT FROM OLD.rationale
       OR NEW.span_quote IS DISTINCT FROM OLD.span_quote
       OR NEW.detected_by IS DISTINCT FROM OLD.detected_by
       OR NEW.recorded_by IS DISTINCT FROM OLD.recorded_by THEN
        RAISE EXCEPTION 'a quality finding''s content is immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

GUARDS_DOWN_SQL = """
DROP TRIGGER IF EXISTS glossary_term_append_only ON glossary_term;
DROP TRIGGER IF EXISTS conflict_guard ON conflict;
DROP FUNCTION IF EXISTS reqpilot_conflict_guard();
"""


def _existing_enum(name: str, *values: str, is_postgres: bool) -> sa.types.TypeEngine:
    """An enum type an earlier migration created: reused, never re-created."""
    if is_postgres:
        return postgresql.ENUM(*values, name=name, create_type=False)
    return sa.Enum(*values, name=name)


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    json_type = postgresql.JSONB() if is_postgres else sa.JSON()

    if is_postgres:
        for value in P5_AUDIT_EVENT_TYPES:
            op.execute(f"ALTER TYPE audit_event_type_enum ADD VALUE IF NOT EXISTS '{value}'")
        for value in P5_FINDING_TYPES:
            op.execute(f"ALTER TYPE quality_finding_type_enum ADD VALUE IF NOT EXISTS '{value}'")
        op.execute("ALTER TYPE finding_detector_enum ADD VALUE IF NOT EXISTS 'RULE'")

    # --- requirement_version: (id, project_id) is referenceable --------------
    if is_postgres:
        op.create_unique_constraint(
            "uq_requirement_version_id", "requirement_version", ["id", "project_id"]
        )
    else:
        # SQLite accepts a unique index as a foreign-key target; no table rebuild.
        op.create_index(
            "uq_requirement_version_id", "requirement_version", ["id", "project_id"], unique=True
        )

    # --- quality_finding: detection provenance and human resolution ----------
    empty_list = sa.text("'[]'::jsonb") if is_postgres else sa.text("'[]'")
    with op.batch_alter_table("quality_finding") as batch:
        batch.add_column(sa.Column("rule_id", sa.String(100), nullable=True))
        batch.add_column(sa.Column("review_signal", sa.Float(), nullable=True))
        batch.add_column(
            sa.Column("evidence", json_type, nullable=False, server_default=empty_list)
        )
        batch.add_column(sa.Column("related_version_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("graph_run_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("agent_run_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("resolution_reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column("resolved_by", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key(
            "fk_quality_finding_related_same_project",
            "requirement_version",
            ["related_version_id", "project_id"],
            ["id", "project_id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_quality_finding_graph_run_id_graph_run",
            "graph_run",
            ["graph_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_quality_finding_agent_run_id_agent_run",
            "agent_run",
            ["agent_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_check_constraint(
            "ck_quality_finding_closed_has_reason",
            "status = 'OPEN' OR length(coalesce(resolution_reason, '')) >= 1",
        )
    op.create_index(
        "ix_quality_finding_project_status", "quality_finding", ["project_id", "status"]
    )

    # --- conflict (G.4; D12) ----------------------------------------------------
    conflict_class = sa.Enum("DEFINITE", "POTENTIAL", name="conflict_class_enum")
    conflict_kind = sa.Enum(
        "NUMERIC",
        "TIMING",
        "ACTOR_SCOPE",
        "LOGICAL",
        "SECURITY",
        "BEHAVIOURAL",
        "OTHER",
        name="conflict_kind_enum",
    )
    conflict_status = sa.Enum(
        "OPEN", "UNDER_REVIEW", "RESOLVED", "DISMISSED", name="conflict_status_enum"
    )
    resolution = sa.Enum(
        "CHOOSE_A", "CHOOSE_B", "SYNTHESISE_NEW", "RECONCILED", name="conflict_resolution_enum"
    )
    severity = _existing_enum(
        "finding_severity_enum", "LOW", "MEDIUM", "HIGH", is_postgres=is_postgres
    )
    detector = _existing_enum(
        "finding_detector_enum", "HUMAN", "AGENT", "RULE", is_postgres=is_postgres
    )
    op.create_table(
        "conflict",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("version_a_id", sa.Uuid(), nullable=False),
        sa.Column("version_b_id", sa.Uuid(), nullable=False),
        sa.Column("conflict_class", conflict_class, nullable=False),
        sa.Column("kind", conflict_kind, nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence_a", sa.Text(), nullable=False),
        sa.Column("evidence_b", sa.Text(), nullable=False),
        sa.Column("severity", severity, nullable=False),
        sa.Column("review_signal", sa.Float(), nullable=True),
        sa.Column("involves_stakeholder_disagreement", sa.Boolean(), nullable=False),
        sa.Column("stakeholder_a", sa.String(300), nullable=True),
        sa.Column("stakeholder_b", sa.String(300), nullable=True),
        sa.Column("detected_by", detector, nullable=False),
        sa.Column("rule_id", sa.String(100), nullable=True),
        sa.Column("graph_run_id", sa.Uuid(), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("recorded_by", sa.Uuid(), nullable=False),
        sa.Column("status", conflict_status, nullable=False),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", resolution, nullable=True),
        sa.Column("resolution_reason", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_conflict"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_conflict_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["version_a_id", "project_id"],
            ["requirement_version.id", "requirement_version.project_id"],
            name="fk_conflict_version_a_same_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["version_b_id", "project_id"],
            ["requirement_version.id", "requirement_version.project_id"],
            name="fk_conflict_version_b_same_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["graph_run_id"],
            ["graph_run.id"],
            name="fk_conflict_graph_run_id_graph_run",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_run.id"],
            name="fk_conflict_agent_run_id_agent_run",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("version_a_id <> version_b_id", name="ck_conflict_two_versions"),
        sa.CheckConstraint("length(rationale) >= 1", name="ck_conflict_rationale_not_empty"),
        sa.CheckConstraint(
            "status <> 'RESOLVED' OR (resolution IS NOT NULL "
            "AND length(coalesce(resolution_reason, '')) >= 1)",
            name="ck_conflict_resolved_has_decision",
        ),
        sa.CheckConstraint(
            "status <> 'DISMISSED' OR length(coalesce(resolution_reason, '')) >= 1",
            name="ck_conflict_dismissed_has_reason",
        ),
    )
    op.create_index("ix_conflict_version_a_id", "conflict", ["version_a_id"])
    op.create_index("ix_conflict_version_b_id", "conflict", ["version_b_id"])
    op.create_index("ix_conflict_project_status", "conflict", ["project_id", "status"])
    op.create_index(
        "uq_conflict_active_pair",
        "conflict",
        ["version_a_id", "version_b_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('OPEN', 'UNDER_REVIEW')"),
        sqlite_where=sa.text("status IN ('OPEN', 'UNDER_REVIEW')"),
    )

    # --- glossary_term (G.5) ------------------------------------------------------
    op.create_table(
        "glossary_term",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("term", sa.String(200), nullable=False),
        sa.Column("term_key", sa.String(200), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_glossary_term"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_glossary_term_project_id_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("project_id", "term_key", name="uq_glossary_term_project_id"),
        sa.CheckConstraint("length(term) >= 1", name="ck_glossary_term_term_not_empty"),
        sa.CheckConstraint("length(definition) >= 1", name="ck_glossary_term_definition_not_empty"),
    )
    op.create_index("ix_glossary_term_project_id", "glossary_term", ["project_id"])

    if is_postgres:
        op.execute(GUARDS_SQL)


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        op.execute(GUARDS_DOWN_SQL)
        op.execute(P4_FINDING_GUARD_SQL)

    op.drop_table("glossary_term")
    op.drop_table("conflict")
    for enum_name in ENUM_NAMES:
        sa.Enum(name=enum_name).drop(bind, checkfirst=True)

    op.drop_index("ix_quality_finding_project_status", table_name="quality_finding")
    with op.batch_alter_table("quality_finding") as batch:
        batch.drop_constraint("ck_quality_finding_closed_has_reason", type_="check")
        batch.drop_constraint("fk_quality_finding_agent_run_id_agent_run", type_="foreignkey")
        batch.drop_constraint("fk_quality_finding_graph_run_id_graph_run", type_="foreignkey")
        batch.drop_constraint("fk_quality_finding_related_same_project", type_="foreignkey")
        for column in reversed(FINDING_COLUMNS):
            batch.drop_column(column)

    if is_postgres:
        op.drop_constraint("uq_requirement_version_id", "requirement_version", type_="unique")
    else:
        op.drop_index("uq_requirement_version_id", table_name="requirement_version")
    # The P5 audit event, finding type and detector values stay on their enums
    # (PostgreSQL cannot remove enum values). Rows using them are gone with the
    # tables above, except findings of the new types, which P5 alone wrote.
