"""P7 risk analysis and the risk register: the matrix, risks, evidence, mitigations.

Additive only. No earlier migration is touched; no existing column changes.

What carries architectural weight:

* ``risk_matrix`` (I.3, ``DQ-03``): the approved 3x3 severity matrix **as
  versioned data**, seeded here from ``rules/data/risk_rules.yaml``. Nine rows.
* ``risk`` (G.6): one risk item, requirement-scoped (naming an exact requirement
  version) or project-scoped (naming none). Its severity is pinned to the matrix
  by a composite foreign key on ``(matrix_version, likelihood, impact,
  severity)``: a row whose severity is not the matrix's value for its own cell
  cannot exist, whatever wrote it. Architecture G.6 asks for "a DB CHECK against
  ``risk_matrix``"; a SQL ``CHECK`` cannot reference another table, so the
  constraint is expressed as the foreign key that can - strictly stronger than a
  check against a copied value.
* ``FR-RSK-007`` at the database: a ``HIGH`` risk can never sit in ``PROPOSED``,
  so a high-severity risk is under review - and counted by the baseline guard -
  from the moment it exists.
* ``FR-RSK-001`` at the database: requirement-scoped implies exactly one
  version, project-scoped implies none. A project risk is never forced into a
  fake requirement link.
* ``FR-RSK-006`` at the database: ``evidence_count >= 1``, plus a deferred
  constraint trigger requiring at least one evidence link at commit.
* Content is immutable - including both ratings and the computed severity. A
  matrix version is immutable too: re-rating history by editing the matrix in
  place is exactly what versioning prevents. Triggers repeat what the ORM guards
  enforce. Rows may still disappear through the project-deletion cascade
  (``FR-ADM-006``), which runs at ``pg_trigger_depth() > 1``.

Revision ID: 0009_p7_risk_register
Revises: 0008_p6_compliance_security
Create Date: P7
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_p7_risk_register"
down_revision: str | None = "0008_p6_compliance_security"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

P7_AUDIT_EVENT_TYPES = (
    "RISK_ANALYSIS_STARTED",
    "RISK_PROPOSED",
    "RISK_DROPPED",
    "RISK_SEVERITY_COMPUTED",
    "RISK_RECORDED",
    "RISK_ESCALATED",
    "RISK_DECISION_RECORDED",
    "RISK_MITIGATION_DECIDED",
)
P7_REVIEW_REASONS = ("RISK_DROPPED", "RISK_OUT_OF_SCOPE")

#: Enum types this migration creates (and its downgrade drops).
ENUM_NAMES = (
    "risk_category_enum",
    "risk_likelihood_enum",
    "risk_impact_enum",
    "risk_severity_enum",
    "risk_scope_enum",
    "risk_status_enum",
    "mitigation_status_enum",
)

#: ``FR-RSK-007``: a HIGH risk is never merely PROPOSED.
HIGH_RISK_GATED_SQL = "severity <> 'HIGH' OR status <> 'PROPOSED'"
#: ``FR-RSK-001``: requirement-scoped means a version; project-scoped means none.
SCOPE_VERSION_SQL = (
    "(scope = 'REQUIREMENT' AND requirement_version_id IS NOT NULL) OR "
    "(scope = 'PROJECT' AND requirement_version_id IS NULL)"
)

GUARDS_SQL = """
-- A risk's content is immutable - above all its two ratings and the severity the
-- matrix computed from them. Its gate task is linked once. Its status moves only
-- along the approved path (architecture I.4), and only forward.
CREATE OR REPLACE FUNCTION reqpilot_risk_guard()
RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'risks are never deleted; a risk that should not stand is rejected';
    END IF;
    IF NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.scope IS DISTINCT FROM OLD.scope
       OR NEW.requirement_version_id IS DISTINCT FROM OLD.requirement_version_id
       OR NEW.graph_run_id IS DISTINCT FROM OLD.graph_run_id
       OR NEW.agent_run_id IS DISTINCT FROM OLD.agent_run_id
       OR NEW.category IS DISTINCT FROM OLD.category
       OR NEW.title IS DISTINCT FROM OLD.title
       OR NEW.title_key IS DISTINCT FROM OLD.title_key
       OR NEW.description IS DISTINCT FROM OLD.description
       OR NEW.likelihood IS DISTINCT FROM OLD.likelihood
       OR NEW.impact IS DISTINCT FROM OLD.impact
       OR NEW.severity IS DISTINCT FROM OLD.severity
       OR NEW.matrix_version IS DISTINCT FROM OLD.matrix_version
       OR NEW.likelihood_rationale IS DISTINCT FROM OLD.likelihood_rationale
       OR NEW.impact_rationale IS DISTINCT FROM OLD.impact_rationale
       OR NEW.citations IS DISTINCT FROM OLD.citations
       OR NEW.evidence_count IS DISTINCT FROM OLD.evidence_count
       OR NEW.detected_by IS DISTINCT FROM OLD.detected_by
       OR NEW.source_signal_kind IS DISTINCT FROM OLD.source_signal_kind
       OR NEW.source_signal_id IS DISTINCT FROM OLD.source_signal_id
       OR NEW.scope_rules_version IS DISTINCT FROM OLD.scope_rules_version
       OR NEW.rules_version IS DISTINCT FROM OLD.rules_version
       OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
       OR NEW.recorded_by IS DISTINCT FROM OLD.recorded_by
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'a risk''s content, ratings and computed severity are immutable';
    END IF;
    IF OLD.approval_task_id IS NOT NULL
       AND NEW.approval_task_id IS DISTINCT FROM OLD.approval_task_id THEN
        RAISE EXCEPTION 'a gate task is linked once and never replaced';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
           (OLD.status = 'PROPOSED' AND NEW.status = 'UNDER_REVIEW')
        OR (OLD.status = 'UNDER_REVIEW'
            AND NEW.status IN ('ACCEPTED', 'MITIGATED', 'REJECTED'))
        OR (OLD.status = 'ACCEPTED' AND NEW.status IN ('MITIGATED', 'CLOSED'))
        OR (OLD.status = 'MITIGATED' AND NEW.status = 'CLOSED')
        OR (OLD.status = 'REJECTED' AND NEW.status = 'CLOSED')) THEN
        RAISE EXCEPTION 'a risk cannot move from % to %', OLD.status, NEW.status;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER risk_guard
    BEFORE UPDATE OR DELETE ON risk
    FOR EACH ROW EXECUTE FUNCTION reqpilot_risk_guard();

-- A published matrix version is immutable. Changing a cell in place would
-- silently re-rate every historical risk that cites that version, which is
-- precisely what recording matrix_version on every risk exists to prevent.
CREATE OR REPLACE FUNCTION reqpilot_risk_matrix_guard()
RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION
        'the risk matrix is versioned data: publish a new matrix_version, never edit one';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER risk_matrix_guard
    BEFORE UPDATE OR DELETE ON risk_matrix
    FOR EACH ROW EXECUTE FUNCTION reqpilot_risk_matrix_guard();

-- A mitigation's text and provenance are immutable; only the human decision moves.
CREATE OR REPLACE FUNCTION reqpilot_risk_mitigation_guard()
RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'mitigation suggestions are never deleted';
    END IF;
    IF NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.risk_id IS DISTINCT FROM OLD.risk_id
       OR NEW.suggestion IS DISTINCT FROM OLD.suggestion
       OR NEW.is_ai_generated IS DISTINCT FROM OLD.is_ai_generated
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'a mitigation suggestion''s text and provenance are immutable';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
           OLD.status = 'SUGGESTED' AND NEW.status IN ('ACCEPTED', 'REJECTED')) THEN
        RAISE EXCEPTION 'a mitigation cannot move from % to %', OLD.status, NEW.status;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER risk_mitigation_guard
    BEFORE UPDATE OR DELETE ON risk_mitigation
    FOR EACH ROW EXECUTE FUNCTION reqpilot_risk_mitigation_guard();

-- Evidence links are append-only (the P3 function; cascades still pass).
CREATE TRIGGER risk_evidence_append_only
    BEFORE UPDATE OR DELETE ON risk_evidence
    FOR EACH ROW EXECUTE FUNCTION reqpilot_project_content_append_only();

-- FR-RSK-006: a risk with no evidence link cannot be committed. Deferred to
-- commit, because the links are written after the risk in one transaction.
CREATE OR REPLACE FUNCTION reqpilot_risk_has_evidence()
RETURNS trigger AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM risk_evidence e
        WHERE e.risk_id = NEW.id AND e.project_id = NEW.project_id
    ) THEN
        RAISE EXCEPTION 'risk % links no evidence of its project', NEW.id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER risk_has_evidence
    AFTER INSERT ON risk
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION reqpilot_risk_has_evidence();
"""

GUARDS_DOWN_SQL = """
DROP TRIGGER IF EXISTS risk_has_evidence ON risk;
DROP TRIGGER IF EXISTS risk_evidence_append_only ON risk_evidence;
DROP TRIGGER IF EXISTS risk_mitigation_guard ON risk_mitigation;
DROP TRIGGER IF EXISTS risk_matrix_guard ON risk_matrix;
DROP TRIGGER IF EXISTS risk_guard ON risk;
DROP FUNCTION IF EXISTS reqpilot_risk_has_evidence();
DROP FUNCTION IF EXISTS reqpilot_risk_mitigation_guard();
DROP FUNCTION IF EXISTS reqpilot_risk_matrix_guard();
DROP FUNCTION IF EXISTS reqpilot_risk_guard();
"""


def _existing_enum(name: str, *values: str, is_postgres: bool) -> sa.types.TypeEngine:
    """An enum type an earlier migration created: reused, never re-created."""
    if is_postgres:
        return postgresql.ENUM(*values, name=name, create_type=False)
    return sa.Enum(*values, name=name)


def _enum(name: str, *values: str, is_postgres: bool, created: set[str]) -> sa.types.TypeEngine:
    """An enum this migration owns. On PostgreSQL it is created once, then reused."""
    if is_postgres:
        if name not in created:
            postgresql.ENUM(*values, name=name).create(op.get_bind(), checkfirst=True)
            created.add(name)
        return postgresql.ENUM(*values, name=name, create_type=False)
    return sa.Enum(*values, name=name)


def _matrix_rows() -> list[dict[str, str]]:
    """The approved matrix, from the versioned ruleset - one source of truth."""
    from reqpilot.rules.risk import packaged_risk_rules

    matrix = packaged_risk_rules().matrix
    return [
        {
            "matrix_version": matrix.version,
            "likelihood": likelihood.name,
            "impact": impact.name,
            "severity": severity.name,
        }
        for (likelihood, impact), severity in sorted(
            matrix.cells.items(), key=lambda item: (item[0][0].value, item[0][1].value)
        )
    ]


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    json_type = postgresql.JSONB() if is_postgres else sa.JSON()
    created: set[str] = set()

    if is_postgres:
        for value in P7_AUDIT_EVENT_TYPES:
            op.execute(f"ALTER TYPE audit_event_type_enum ADD VALUE IF NOT EXISTS '{value}'")
        for value in P7_REVIEW_REASONS:
            op.execute(f"ALTER TYPE review_reason_enum ADD VALUE IF NOT EXISTS '{value}'")

    likelihood = _enum(
        "risk_likelihood_enum", "L1", "L2", "L3", is_postgres=is_postgres, created=created
    )
    impact = _enum("risk_impact_enum", "I1", "I2", "I3", is_postgres=is_postgres, created=created)
    severity = _enum(
        "risk_severity_enum", "LOW", "MEDIUM", "HIGH", is_postgres=is_postgres, created=created
    )
    category = _enum(
        "risk_category_enum",
        "BUSINESS",
        "TECHNICAL",
        "SECURITY",
        "PRIVACY",
        "COMPLIANCE",
        "OPERATIONAL",
        is_postgres=is_postgres,
        created=created,
    )
    scope = _enum(
        "risk_scope_enum", "REQUIREMENT", "PROJECT", is_postgres=is_postgres, created=created
    )
    status = _enum(
        "risk_status_enum",
        "PROPOSED",
        "UNDER_REVIEW",
        "ACCEPTED",
        "MITIGATED",
        "REJECTED",
        "CLOSED",
        is_postgres=is_postgres,
        created=created,
    )
    mitigation_status = _enum(
        "mitigation_status_enum",
        "SUGGESTED",
        "ACCEPTED",
        "REJECTED",
        is_postgres=is_postgres,
        created=created,
    )
    role_enum = _existing_enum(
        "role_enum",
        "ANALYST",
        "STAKEHOLDER",
        "COMPLIANCE_OFFICER",
        "SECURITY_REVIEWER",
        "PROJECT_MANAGER",
        "AUDITOR",
        "KB_ADMIN",
        is_postgres=is_postgres,
    )
    detector = _existing_enum(
        "finding_detector_enum", "HUMAN", "AGENT", "RULE", is_postgres=is_postgres
    )

    # --- the matrix, as versioned data (I.3, DQ-03) -------------------------
    matrix_table = op.create_table(
        "risk_matrix",
        sa.Column("matrix_version", sa.String(32), nullable=False),
        sa.Column("likelihood", likelihood, nullable=False),
        sa.Column("impact", impact, nullable=False),
        sa.Column("severity", severity, nullable=False),
        sa.PrimaryKeyConstraint("matrix_version", "likelihood", "impact"),
        sa.UniqueConstraint(
            "matrix_version", "likelihood", "impact", "severity", name="matrix_cell_severity"
        ),
    )
    op.bulk_insert(matrix_table, _matrix_rows())

    # --- the register -------------------------------------------------------
    op.create_table(
        "risk",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("scope", scope, nullable=False),
        sa.Column("requirement_version_id", sa.Uuid(), nullable=True),
        sa.Column("graph_run_id", sa.Uuid(), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("category", category, nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("title_key", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("likelihood", likelihood, nullable=False),
        sa.Column("impact", impact, nullable=False),
        sa.Column("severity", severity, nullable=False),
        sa.Column("matrix_version", sa.String(32), nullable=False),
        sa.Column("likelihood_rationale", sa.Text(), nullable=False),
        sa.Column("impact_rationale", sa.Text(), nullable=False),
        sa.Column("citations", json_type, nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("detected_by", detector, nullable=False),
        sa.Column("source_signal_kind", sa.String(64), nullable=True),
        sa.Column("source_signal_id", sa.Uuid(), nullable=True),
        sa.Column("scope_rules_version", sa.String(20), nullable=False),
        sa.Column("rules_version", sa.String(100), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("recorded_by", sa.Uuid(), nullable=False),
        sa.Column("status", status, nullable=False),
        sa.Column("owner_role", role_enum, nullable=False),
        sa.Column("approval_task_id", sa.Uuid(), nullable=True),
        sa.Column("decision_rationale", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_signal", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "project_id", name="uq_risk_id_project"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        # The severity is the matrix's, or the row does not exist (G.6).
        sa.ForeignKeyConstraint(
            ["matrix_version", "likelihood", "impact", "severity"],
            [
                "risk_matrix.matrix_version",
                "risk_matrix.likelihood",
                "risk_matrix.impact",
                "risk_matrix.severity",
            ],
            name="fk_risk_severity_from_matrix",
        ),
        sa.ForeignKeyConstraint(
            ["requirement_version_id", "project_id"],
            ["requirement_version.id", "requirement_version.project_id"],
            name="fk_risk_version_same_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approval_task_id", "project_id"],
            ["approval_task.id", "approval_task.project_id"],
            name="fk_risk_task_same_project",
        ),
        sa.ForeignKeyConstraint(["graph_run_id"], ["graph_run.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_run.id"], ondelete="SET NULL"),
        sa.CheckConstraint("evidence_count >= 1", name="cites_evidence"),
        sa.CheckConstraint("length(title) >= 1", name="title_not_empty"),
        sa.CheckConstraint("length(description) >= 1", name="description_not_empty"),
        sa.CheckConstraint(
            "length(likelihood_rationale) >= 1", name="likelihood_rationale_present"
        ),
        sa.CheckConstraint("length(impact_rationale) >= 1", name="impact_rationale_present"),
        sa.CheckConstraint(HIGH_RISK_GATED_SQL, name="high_risk_is_gated"),
        sa.CheckConstraint(SCOPE_VERSION_SQL, name="scope_matches_version"),
    )
    op.create_index("ix_risk_project_status", "risk", ["project_id", "status"])
    op.create_index("ix_risk_project_severity", "risk", ["project_id", "severity"])
    op.create_index("ix_risk_requirement_version_id", "risk", ["requirement_version_id"])
    op.create_index("ix_risk_graph_run_id", "risk", ["graph_run_id"])
    op.create_index(
        "uq_risk_active_version_title",
        "risk",
        ["requirement_version_id", "title_key"],
        unique=True,
        postgresql_where=sa.text("status <> 'REJECTED' AND scope = 'REQUIREMENT'"),
        sqlite_where=sa.text("status <> 'REJECTED' AND scope = 'REQUIREMENT'"),
    )
    op.create_index(
        "uq_risk_active_project_title",
        "risk",
        ["project_id", "title_key"],
        unique=True,
        postgresql_where=sa.text("status <> 'REJECTED' AND scope = 'PROJECT'"),
        sqlite_where=sa.text("status <> 'REJECTED' AND scope = 'PROJECT'"),
    )

    op.create_table(
        "risk_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("risk_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["risk_id", "project_id"],
            ["risk.id", "risk.project_id"],
            name="fk_risk_evidence_risk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "project_id"],
            ["evidence.id", "evidence.project_id"],
            name="fk_risk_evidence_evidence",
        ),
        sa.UniqueConstraint("risk_id", "evidence_id", name="risk_evidence_link"),
    )
    op.create_index("ix_risk_evidence_project_id", "risk_evidence", ["project_id"])
    op.create_index("ix_risk_evidence_risk_id", "risk_evidence", ["risk_id"])

    op.create_table(
        "risk_mitigation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("risk_id", sa.Uuid(), nullable=False),
        sa.Column("suggestion", sa.Text(), nullable=False),
        sa.Column("is_ai_generated", sa.Boolean(), nullable=False),
        sa.Column("status", mitigation_status, nullable=False),
        sa.Column("accepted_by", sa.Uuid(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["risk_id", "project_id"],
            ["risk.id", "risk.project_id"],
            name="fk_risk_mitigation_risk",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("length(suggestion) >= 1", name="suggestion_not_empty"),
        sa.CheckConstraint(
            "status <> 'ACCEPTED' OR accepted_by IS NOT NULL", name="acceptance_names_a_human"
        ),
    )
    op.create_index("ix_risk_mitigation_project_id", "risk_mitigation", ["project_id"])
    op.create_index("ix_risk_mitigation_risk", "risk_mitigation", ["risk_id", "status"])

    if is_postgres:
        op.execute(GUARDS_SQL)


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        op.execute(GUARDS_DOWN_SQL)

    op.drop_table("risk_mitigation")
    op.drop_table("risk_evidence")
    op.drop_table("risk")
    op.drop_table("risk_matrix")
    for enum_name in ENUM_NAMES:
        sa.Enum(name=enum_name).drop(bind, checkfirst=True)
    # The P7 audit event and review reason values stay on their enums (PostgreSQL
    # cannot remove enum values). Rows using them are gone with the tables above,
    # except audit events and review items, which are history and are kept.
