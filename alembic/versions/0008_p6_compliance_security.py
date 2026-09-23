"""P6 compliance and security analysis: mappings, gaps, security/privacy findings.

Additive only. No earlier migration is touched; no existing column changes.

What carries architectural weight:

* ``evidence`` and ``approval_task`` gain a unique ``(id, project_id)`` so that
  P6 rows can reference evidence and gate tasks *of their own project* through
  composite foreign keys. Cross-project evidence or a cross-project G2/G3 task
  cannot be linked, whatever wrote the row.
* ``compliance_mapping`` (G.6): a validated **candidate** mapping of one exact
  requirement version to one checklist control. It must cite evidence (a check,
  plus a deferred constraint trigger requiring at least one evidence link at
  commit), must carry a jurisdiction and a source type, and a high-impact
  interpretation can never be stored as a plain ``CANDIDATE`` (G2, ``FR-CMP-004``).
* ``compliance_gap`` (K.2): a rule-engine output, append-only.
* ``security_privacy_finding`` (G.6, I.7): the model's ``proposed_risk_level`` kept
  for audit beside the authoritative ``risk_level``. Checks enforce INV-G3 at the
  database: ``risk_level = max(normalised proposal, floor)`` exactly, a HIGH floor
  for every high-impact family, at least MEDIUM for privacy, and a HIGH finding is
  never outside G3 review.
* Content is immutable; the review status moves once, forward, and the gate task
  is linked once. Triggers repeat what the ORM guards enforce. Rows may still
  disappear through the project-deletion cascade (``FR-ADM-006``), which runs at
  ``pg_trigger_depth() > 1``.

Revision ID: 0008_p6_compliance_security
Revises: 0007_p5_quality_conflict
Create Date: P6
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_p6_compliance_security"
down_revision: str | None = "0007_p5_quality_conflict"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

P6_AUDIT_EVENT_TYPES = (
    "COMPLIANCE_RETRIEVED",
    "COMPLIANCE_PROPOSED",
    "COMPLIANCE_MAPPING_ACCEPTED",
    "COMPLIANCE_CLAIM_DROPPED",
    "COMPLIANCE_GAP_FOUND",
    "COMPLIANCE_MAPPING_REVIEWED",
    "SECURITY_REQUIREMENT_DERIVED",
    "PRIVACY_REQUIREMENT_DERIVED",
    "SECURITY_FINDING_DROPPED",
    "SECURITY_RISK_EVALUATED",
    "SECURITY_FINDING_REVIEWED",
)
P6_REVIEW_REASONS = ("EVIDENCE_UNAVAILABLE", "CLAIM_DROPPED")

#: Enum types this migration creates (and its downgrade drops).
ENUM_NAMES = (
    "obligation_kind_enum",
    "compliance_relationship_enum",
    "compliance_mapping_status_enum",
    "compliance_gap_origin_enum",
    "security_privacy_category_enum",
    "security_control_family_enum",
    "security_risk_level_enum",
    "security_finding_status_enum",
    "evidence_status_enum",
)

HIGH_IMPACT_FAMILIES = (
    "AUTHENTICATION",
    "AUTHORISATION",
    "CRYPTOGRAPHY",
    "AUDIT_LOGGING",
    "TRANSACTION_INTEGRITY",
    "CONSENT",
    "RETENTION",
    "SUBJECT_RIGHTS",
)


def _rank(column: str) -> str:
    return f"(CASE {column} WHEN 'LOW' THEN 1 WHEN 'MEDIUM' THEN 2 WHEN 'HIGH' THEN 3 END)"


RISK_IS_MAX_SQL = (
    f"{_rank('risk_level')} = CASE WHEN {_rank('catalogue_floor')} >= "
    f"{_rank('normalised_proposed_level')} THEN {_rank('catalogue_floor')} "
    f"ELSE {_rank('normalised_proposed_level')} END"
)
HIGH_IMPACT_FLOOR_SQL = (
    "family NOT IN (" + ", ".join(f"'{f}'" for f in HIGH_IMPACT_FAMILIES) + ") "
    "OR catalogue_floor = 'HIGH'"
)

GUARDS_SQL = """
-- A mapping's content is immutable; its gate task is linked once; its status
-- moves only PENDING_REVIEW -> APPROVED | REJECTED (G2), once.
CREATE OR REPLACE FUNCTION reqpilot_compliance_mapping_guard()
RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'compliance mappings are never deleted';
    END IF;
    IF NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.requirement_version_id IS DISTINCT FROM OLD.requirement_version_id
       OR NEW.graph_run_id IS DISTINCT FROM OLD.graph_run_id
       OR NEW.agent_run_id IS DISTINCT FROM OLD.agent_run_id
       OR NEW.control_key IS DISTINCT FROM OLD.control_key
       OR NEW.control_title IS DISTINCT FROM OLD.control_title
       OR NEW.obligation_kind IS DISTINCT FROM OLD.obligation_kind
       OR NEW.checklist_ref IS DISTINCT FROM OLD.checklist_ref
       OR NEW.checklist_domain IS DISTINCT FROM OLD.checklist_domain
       OR NEW.checklist_jurisdiction IS DISTINCT FROM OLD.checklist_jurisdiction
       OR NEW.relationship IS DISTINCT FROM OLD.relationship
       OR NEW.rationale IS DISTINCT FROM OLD.rationale
       OR NEW.candidate_text IS DISTINCT FROM OLD.candidate_text
       OR NEW.implied_obligation IS DISTINCT FROM OLD.implied_obligation
       OR NEW.jurisdiction IS DISTINCT FROM OLD.jurisdiction
       OR NEW.source_type IS DISTINCT FROM OLD.source_type
       OR NEW.jurisdictions IS DISTINCT FROM OLD.jurisdictions
       OR NEW.source_types IS DISTINCT FROM OLD.source_types
       OR NEW.citations IS DISTINCT FROM OLD.citations
       OR NEW.evidence_count IS DISTINCT FROM OLD.evidence_count
       OR NEW.is_high_impact IS DISTINCT FROM OLD.is_high_impact
       OR NEW.high_impact_reasons IS DISTINCT FROM OLD.high_impact_reasons
       OR NEW.review_signal IS DISTINCT FROM OLD.review_signal
       OR NEW.language_rules_version IS DISTINCT FROM OLD.language_rules_version
       OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
       OR NEW.recorded_by IS DISTINCT FROM OLD.recorded_by
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'a compliance mapping''s content is immutable';
    END IF;
    IF OLD.approval_task_id IS NOT NULL
       AND NEW.approval_task_id IS DISTINCT FROM OLD.approval_task_id THEN
        RAISE EXCEPTION 'a gate task is linked once and never replaced';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
           OLD.status = 'PENDING_REVIEW' AND NEW.status IN ('APPROVED', 'REJECTED')) THEN
        RAISE EXCEPTION 'a compliance mapping cannot move from % to %', OLD.status, NEW.status;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER compliance_mapping_guard
    BEFORE UPDATE OR DELETE ON compliance_mapping
    FOR EACH ROW EXECUTE FUNCTION reqpilot_compliance_mapping_guard();

-- A finding's content - above all both risk levels - is immutable; its gate task
-- is linked once; its status moves only PENDING_REVIEW -> APPROVED | REJECTED (G3).
CREATE OR REPLACE FUNCTION reqpilot_security_privacy_finding_guard()
RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'security/privacy findings are never deleted';
    END IF;
    IF NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.requirement_version_id IS DISTINCT FROM OLD.requirement_version_id
       OR NEW.graph_run_id IS DISTINCT FROM OLD.graph_run_id
       OR NEW.agent_run_id IS DISTINCT FROM OLD.agent_run_id
       OR NEW.category IS DISTINCT FROM OLD.category
       OR NEW.family IS DISTINCT FROM OLD.family
       OR NEW.derived_requirement IS DISTINCT FROM OLD.derived_requirement
       OR NEW.rationale IS DISTINCT FROM OLD.rationale
       OR NEW.risk_rationale IS DISTINCT FROM OLD.risk_rationale
       OR NEW.evidence_status IS DISTINCT FROM OLD.evidence_status
       OR NEW.evidence_count IS DISTINCT FROM OLD.evidence_count
       OR NEW.citations IS DISTINCT FROM OLD.citations
       OR NEW.proposed_risk_level IS DISTINCT FROM OLD.proposed_risk_level
       OR NEW.normalised_proposed_level IS DISTINCT FROM OLD.normalised_proposed_level
       OR NEW.catalogue_floor IS DISTINCT FROM OLD.catalogue_floor
       OR NEW.risk_level IS DISTINCT FROM OLD.risk_level
       OR NEW.risk_rules_version IS DISTINCT FROM OLD.risk_rules_version
       OR NEW.escalation_reason IS DISTINCT FROM OLD.escalation_reason
       OR NEW.detected_by IS DISTINCT FROM OLD.detected_by
       OR NEW.source_signal_finding_id IS DISTINCT FROM OLD.source_signal_finding_id
       OR NEW.review_signal IS DISTINCT FROM OLD.review_signal
       OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
       OR NEW.recorded_by IS DISTINCT FROM OLD.recorded_by
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'a security/privacy finding''s content and risk levels are immutable';
    END IF;
    IF OLD.approval_task_id IS NOT NULL
       AND NEW.approval_task_id IS DISTINCT FROM OLD.approval_task_id THEN
        RAISE EXCEPTION 'a gate task is linked once and never replaced';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
           OLD.status = 'PENDING_REVIEW' AND NEW.status IN ('APPROVED', 'REJECTED')) THEN
        RAISE EXCEPTION 'a security/privacy finding cannot move from % to %',
            OLD.status, NEW.status;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER security_privacy_finding_guard
    BEFORE UPDATE OR DELETE ON security_privacy_finding
    FOR EACH ROW EXECUTE FUNCTION reqpilot_security_privacy_finding_guard();

-- Links and gaps are append-only (the P3 function; cascades still pass).
CREATE TRIGGER compliance_mapping_evidence_append_only
    BEFORE UPDATE OR DELETE ON compliance_mapping_evidence
    FOR EACH ROW EXECUTE FUNCTION reqpilot_project_content_append_only();
CREATE TRIGGER security_privacy_finding_evidence_append_only
    BEFORE UPDATE OR DELETE ON security_privacy_finding_evidence
    FOR EACH ROW EXECUTE FUNCTION reqpilot_project_content_append_only();
CREATE TRIGGER compliance_gap_append_only
    BEFORE UPDATE OR DELETE ON compliance_gap
    FOR EACH ROW EXECUTE FUNCTION reqpilot_project_content_append_only();

-- FR-CMP-001 / J.5: a mapping with no evidence link cannot be committed. Deferred
-- to commit, because the links are written after the mapping in one transaction.
CREATE OR REPLACE FUNCTION reqpilot_compliance_mapping_has_evidence()
RETURNS trigger AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM compliance_mapping_evidence e
        WHERE e.mapping_id = NEW.id AND e.project_id = NEW.project_id
    ) THEN
        RAISE EXCEPTION 'compliance mapping % cites no evidence of its project', NEW.id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER compliance_mapping_has_evidence
    AFTER INSERT ON compliance_mapping
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION reqpilot_compliance_mapping_has_evidence();
"""

GUARDS_DOWN_SQL = """
DROP TRIGGER IF EXISTS compliance_mapping_has_evidence ON compliance_mapping;
DROP TRIGGER IF EXISTS compliance_gap_append_only ON compliance_gap;
DROP TRIGGER IF EXISTS security_privacy_finding_evidence_append_only
    ON security_privacy_finding_evidence;
DROP TRIGGER IF EXISTS compliance_mapping_evidence_append_only ON compliance_mapping_evidence;
DROP TRIGGER IF EXISTS security_privacy_finding_guard ON security_privacy_finding;
DROP TRIGGER IF EXISTS compliance_mapping_guard ON compliance_mapping;
DROP FUNCTION IF EXISTS reqpilot_compliance_mapping_has_evidence();
DROP FUNCTION IF EXISTS reqpilot_security_privacy_finding_guard();
DROP FUNCTION IF EXISTS reqpilot_compliance_mapping_guard();
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


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    json_type = postgresql.JSONB() if is_postgres else sa.JSON()
    created: set[str] = set()

    if is_postgres:
        for value in P6_AUDIT_EVENT_TYPES:
            op.execute(f"ALTER TYPE audit_event_type_enum ADD VALUE IF NOT EXISTS '{value}'")
        for value in P6_REVIEW_REASONS:
            op.execute(f"ALTER TYPE review_reason_enum ADD VALUE IF NOT EXISTS '{value}'")

    # --- (id, project_id) referenceable on evidence and approval_task --------------
    if is_postgres:
        op.create_unique_constraint("uq_evidence_id", "evidence", ["id", "project_id"])
        op.create_unique_constraint("uq_approval_task_id", "approval_task", ["id", "project_id"])
    else:
        op.create_index("uq_evidence_id", "evidence", ["id", "project_id"], unique=True)
        op.create_index("uq_approval_task_id", "approval_task", ["id", "project_id"], unique=True)

    obligation = _enum(
        "obligation_kind_enum",
        "CONTROL",
        "APPROVAL_CHECKPOINT",
        "AUDIT_CHECKPOINT",
        "RETENTION_OBLIGATION",
        "REPORTING_OBLIGATION",
        is_postgres=is_postgres,
        created=created,
    )
    relationship = _enum(
        "compliance_relationship_enum",
        "ADDRESSES",
        "PARTIALLY_ADDRESSES",
        "RELEVANT_CONTEXT",
        is_postgres=is_postgres,
        created=created,
    )
    mapping_status = _enum(
        "compliance_mapping_status_enum",
        "CANDIDATE",
        "PENDING_REVIEW",
        "APPROVED",
        "REJECTED",
        is_postgres=is_postgres,
        created=created,
    )
    gap_origin = _enum(
        "compliance_gap_origin_enum",
        "RULE_ENGINE",
        "G2_REJECTION",
        is_postgres=is_postgres,
        created=created,
    )
    category = _enum(
        "security_privacy_category_enum",
        "SECURITY",
        "PRIVACY",
        is_postgres=is_postgres,
        created=created,
    )
    family = _enum(
        "security_control_family_enum",
        "AUTHENTICATION",
        "AUTHORISATION",
        "CRYPTOGRAPHY",
        "AUDIT_LOGGING",
        "SESSION_MANAGEMENT",
        "TRANSACTION_INTEGRITY",
        "FRAUD_CONTROLS",
        "DATA_MINIMISATION",
        "CONSENT",
        "RETENTION",
        "SUBJECT_RIGHTS",
        is_postgres=is_postgres,
        created=created,
    )
    level = _enum(
        "security_risk_level_enum",
        "LOW",
        "MEDIUM",
        "HIGH",
        is_postgres=is_postgres,
        created=created,
    )
    finding_status = _enum(
        "security_finding_status_enum",
        "PROPOSED",
        "PENDING_REVIEW",
        "APPROVED",
        "REJECTED",
        is_postgres=is_postgres,
        created=created,
    )
    evidence_status = _enum(
        "evidence_status_enum",
        "SUPPORTED",
        "UNAVAILABLE",
        is_postgres=is_postgres,
        created=created,
    )
    source_type = _existing_enum(
        "normative_source_type_enum",
        "STATUTE",
        "REGULATORY_DIRECTION",
        "REGULATORY_GUIDANCE",
        "ORG_POLICY",
        "CONTRACTUAL_SCHEME",
        "INDUSTRY_STANDARD",
        "CONTROL_FRAMEWORK",
        "BEST_PRACTICE",
        is_postgres=is_postgres,
    )
    detector = _existing_enum(
        "finding_detector_enum", "HUMAN", "AGENT", "RULE", is_postgres=is_postgres
    )

    # --- compliance_mapping (G.6) ---------------------------------------------------
    op.create_table(
        "compliance_mapping",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("requirement_version_id", sa.Uuid(), nullable=False),
        sa.Column("graph_run_id", sa.Uuid(), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("control_key", sa.String(64), nullable=False),
        sa.Column("control_title", sa.String(300), nullable=False),
        sa.Column("obligation_kind", obligation, nullable=False),
        sa.Column("checklist_ref", sa.String(100), nullable=False),
        sa.Column("checklist_domain", sa.String(100), nullable=False),
        sa.Column("checklist_jurisdiction", sa.String(20), nullable=False),
        sa.Column("relationship", relationship, nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("candidate_text", sa.Text(), nullable=True),
        sa.Column("implied_obligation", sa.Text(), nullable=True),
        sa.Column("jurisdiction", sa.String(20), nullable=False),
        sa.Column("source_type", source_type, nullable=False),
        sa.Column("jurisdictions", json_type, nullable=False),
        sa.Column("source_types", json_type, nullable=False),
        sa.Column("citations", json_type, nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("is_high_impact", sa.Boolean(), nullable=False),
        sa.Column("high_impact_reasons", json_type, nullable=False),
        sa.Column("review_signal", sa.Float(), nullable=True),
        sa.Column("language_rules_version", sa.String(20), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("recorded_by", sa.Uuid(), nullable=False),
        sa.Column("status", mapping_status, nullable=False),
        sa.Column("approval_task_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_compliance_mapping"),
        sa.UniqueConstraint("id", "project_id", name="uq_compliance_mapping_id"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_compliance_mapping_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requirement_version_id", "project_id"],
            ["requirement_version.id", "requirement_version.project_id"],
            name="fk_compliance_mapping_version_same_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approval_task_id", "project_id"],
            ["approval_task.id", "approval_task.project_id"],
            name="fk_compliance_mapping_task_same_project",
        ),
        sa.ForeignKeyConstraint(
            ["graph_run_id"],
            ["graph_run.id"],
            name="fk_compliance_mapping_graph_run_id_graph_run",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_run.id"],
            name="fk_compliance_mapping_agent_run_id_agent_run",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("evidence_count >= 1", name="cites_evidence"),
        sa.CheckConstraint("length(rationale) >= 1", name="rationale_not_empty"),
        sa.CheckConstraint("length(jurisdiction) >= 2", name="has_jurisdiction"),
        sa.CheckConstraint(
            "NOT is_high_impact OR status <> 'CANDIDATE'",
            name="high_impact_is_gated",
        ),
    )
    op.create_index(
        "ix_compliance_mapping_requirement_version_id",
        "compliance_mapping",
        ["requirement_version_id"],
    )
    op.create_index("ix_compliance_mapping_graph_run_id", "compliance_mapping", ["graph_run_id"])
    op.create_index(
        "ix_compliance_mapping_project_status", "compliance_mapping", ["project_id", "status"]
    )
    op.create_index(
        "uq_compliance_mapping_active",
        "compliance_mapping",
        ["requirement_version_id", "control_key"],
        unique=True,
        postgresql_where=sa.text("status <> 'REJECTED'"),
        sqlite_where=sa.text("status <> 'REJECTED'"),
    )

    op.create_table(
        "compliance_mapping_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("mapping_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_compliance_mapping_evidence"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_compliance_mapping_evidence_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["mapping_id", "project_id"],
            ["compliance_mapping.id", "compliance_mapping.project_id"],
            name="fk_compliance_mapping_evidence_mapping",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "project_id"],
            ["evidence.id", "evidence.project_id"],
            name="fk_compliance_mapping_evidence_evidence",
        ),
        sa.UniqueConstraint(
            "mapping_id", "evidence_id", name="uq_compliance_mapping_evidence_mapping_id"
        ),
    )
    op.create_index(
        "ix_compliance_mapping_evidence_project_id", "compliance_mapping_evidence", ["project_id"]
    )
    op.create_index(
        "ix_compliance_mapping_evidence_mapping_id", "compliance_mapping_evidence", ["mapping_id"]
    )

    # --- compliance_gap (K.2) ------------------------------------------------------
    op.create_table(
        "compliance_gap",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("graph_run_id", sa.Uuid(), nullable=False),
        sa.Column("control_key", sa.String(64), nullable=False),
        sa.Column("control_title", sa.String(300), nullable=False),
        sa.Column("obligation_kind", obligation, nullable=False),
        sa.Column("is_high_impact", sa.Boolean(), nullable=False),
        sa.Column("checklist_ref", sa.String(100), nullable=False),
        sa.Column("checklist_domain", sa.String(100), nullable=False),
        sa.Column("checklist_jurisdiction", sa.String(20), nullable=False),
        sa.Column("origin", gap_origin, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("related_mapping_id", sa.Uuid(), nullable=True),
        sa.Column("recorded_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_compliance_gap"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_compliance_gap_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["graph_run_id"],
            ["graph_run.id"],
            name="fk_compliance_gap_graph_run_id_graph_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["related_mapping_id", "project_id"],
            ["compliance_mapping.id", "compliance_mapping.project_id"],
            name="fk_compliance_gap_mapping_same_project",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("length(reason) >= 1", name="reason_not_empty"),
        sa.CheckConstraint(
            "origin <> 'G2_REJECTION' OR related_mapping_id IS NOT NULL",
            name="rejection_names_mapping",
        ),
    )
    op.create_index(
        "ix_compliance_gap_project_run", "compliance_gap", ["project_id", "graph_run_id"]
    )
    op.create_index(
        "uq_compliance_gap_rule_run_control",
        "compliance_gap",
        ["graph_run_id", "control_key", "checklist_jurisdiction"],
        unique=True,
        postgresql_where=sa.text("origin = 'RULE_ENGINE'"),
        sqlite_where=sa.text("origin = 'RULE_ENGINE'"),
    )
    op.create_index(
        "uq_compliance_gap_rejected_mapping",
        "compliance_gap",
        ["related_mapping_id"],
        unique=True,
        postgresql_where=sa.text("origin = 'G2_REJECTION'"),
        sqlite_where=sa.text("origin = 'G2_REJECTION'"),
    )

    # --- security_privacy_finding (G.6, I.7) -----------------------------------------
    op.create_table(
        "security_privacy_finding",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("requirement_version_id", sa.Uuid(), nullable=False),
        sa.Column("graph_run_id", sa.Uuid(), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("category", category, nullable=False),
        sa.Column("family", family, nullable=False),
        sa.Column("derived_requirement", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("risk_rationale", sa.Text(), nullable=True),
        sa.Column("evidence_status", evidence_status, nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("citations", json_type, nullable=False),
        sa.Column("proposed_risk_level", sa.String(50), nullable=True),
        sa.Column("normalised_proposed_level", level, nullable=False),
        sa.Column("catalogue_floor", level, nullable=False),
        sa.Column("risk_level", level, nullable=False),
        sa.Column("risk_rules_version", sa.String(100), nullable=False),
        sa.Column("escalation_reason", sa.Text(), nullable=False),
        sa.Column("detected_by", detector, nullable=False),
        sa.Column("source_signal_finding_id", sa.Uuid(), nullable=True),
        sa.Column("review_signal", sa.Float(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("recorded_by", sa.Uuid(), nullable=False),
        sa.Column("status", finding_status, nullable=False),
        sa.Column("approval_task_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_security_privacy_finding"),
        sa.UniqueConstraint("id", "project_id", name="uq_security_privacy_finding_id"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_security_privacy_finding_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requirement_version_id", "project_id"],
            ["requirement_version.id", "requirement_version.project_id"],
            name="fk_security_privacy_finding_version_same_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approval_task_id", "project_id"],
            ["approval_task.id", "approval_task.project_id"],
            name="fk_security_privacy_finding_task_same_project",
        ),
        sa.ForeignKeyConstraint(
            ["graph_run_id"],
            ["graph_run.id"],
            name="fk_security_privacy_finding_graph_run_id_graph_run",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_run.id"],
            name="fk_security_privacy_finding_agent_run_id_agent_run",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_signal_finding_id"],
            ["quality_finding.id"],
            name="fk_security_privacy_finding_source_signal",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "length(derived_requirement) >= 1",
            name="requirement_not_empty",
        ),
        sa.CheckConstraint(
            "length(escalation_reason) >= 1",
            name="escalation_reason_not_empty",
        ),
        sa.CheckConstraint(RISK_IS_MAX_SQL, name="risk_is_max_of_proposal_and_floor"),
        sa.CheckConstraint(HIGH_IMPACT_FLOOR_SQL, name="high_impact_family_floor_high"),
        sa.CheckConstraint(
            "category <> 'PRIVACY' OR catalogue_floor IN ('MEDIUM', 'HIGH')",
            name="privacy_floor_medium",
        ),
        sa.CheckConstraint(
            "risk_level <> 'HIGH' OR status <> 'PROPOSED'",
            name="high_risk_is_gated",
        ),
        sa.CheckConstraint(
            "evidence_status <> 'SUPPORTED' OR evidence_count >= 1",
            name="supported_cites_evidence",
        ),
    )
    op.create_index(
        "ix_security_privacy_finding_requirement_version_id",
        "security_privacy_finding",
        ["requirement_version_id"],
    )
    op.create_index(
        "ix_security_privacy_finding_graph_run_id", "security_privacy_finding", ["graph_run_id"]
    )
    op.create_index(
        "ix_security_privacy_finding_project_status",
        "security_privacy_finding",
        ["project_id", "status"],
    )
    op.create_index(
        "uq_security_privacy_finding_active",
        "security_privacy_finding",
        ["requirement_version_id", "family"],
        unique=True,
        postgresql_where=sa.text("status <> 'REJECTED'"),
        sqlite_where=sa.text("status <> 'REJECTED'"),
    )

    op.create_table(
        "security_privacy_finding_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_security_privacy_finding_evidence"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_security_privacy_finding_evidence_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["finding_id", "project_id"],
            ["security_privacy_finding.id", "security_privacy_finding.project_id"],
            name="fk_security_privacy_finding_evidence_finding",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "project_id"],
            ["evidence.id", "evidence.project_id"],
            name="fk_security_privacy_finding_evidence_evidence",
        ),
        sa.UniqueConstraint(
            "finding_id", "evidence_id", name="uq_security_privacy_finding_evidence_finding_id"
        ),
    )
    op.create_index(
        "ix_security_privacy_finding_evidence_project_id",
        "security_privacy_finding_evidence",
        ["project_id"],
    )
    op.create_index(
        "ix_security_privacy_finding_evidence_finding_id",
        "security_privacy_finding_evidence",
        ["finding_id"],
    )

    if is_postgres:
        op.execute(GUARDS_SQL)


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        op.execute(GUARDS_DOWN_SQL)

    op.drop_table("security_privacy_finding_evidence")
    op.drop_table("security_privacy_finding")
    op.drop_table("compliance_gap")
    op.drop_table("compliance_mapping_evidence")
    op.drop_table("compliance_mapping")
    for enum_name in ENUM_NAMES:
        sa.Enum(name=enum_name).drop(bind, checkfirst=True)

    if is_postgres:
        op.drop_constraint("uq_approval_task_id", "approval_task", type_="unique")
        op.drop_constraint("uq_evidence_id", "evidence", type_="unique")
    else:
        op.drop_index("uq_approval_task_id", table_name="approval_task")
        op.drop_index("uq_evidence_id", table_name="evidence")
    # The P6 audit event and review reason values stay on their enums (PostgreSQL
    # cannot remove enum values). Rows using them are gone with the tables above,
    # except audit events and review items, which are history and are kept.
