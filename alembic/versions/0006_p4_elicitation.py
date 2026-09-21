"""P4 elicitation and clarification: stakeholders, sessions, utterances, findings, clarifications.

Additive only. No earlier migration is touched and no existing table changes.

What carries architectural weight:

* ``stakeholder`` and ``interview_session`` (G.3): mutable, as G.3 says. A
  session's stakeholder must be in the session's project - a composite foreign
  key, not a convention.
* ``utterance`` (G.3; ``FR-ELI-004``): **append-only**, a traceability root. Its
  project must be its session's project (composite foreign key).
* ``quality_finding`` (G.4): content immutable. P4 records findings only by hand;
  detecting them is P5.
* ``clarification`` (G.4; ``FR-CLR-001``..``004``): content immutable, resolved
  once; at most one open clarification per finding (a partial unique index);
  its finding must belong to its version (composite foreign key).

Triggers repeat, at the database, what the ORM guard already enforces. As in
P3, rows may still disappear through the project-deletion cascade
(``FR-ADM-006``): those deletes run nested in the foreign-key action, at
``pg_trigger_depth() > 1``, and are the only ones allowed.

Revision ID: 0006_p4_elicitation
Revises: 0005_p3_extraction
Create Date: P4
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_p4_elicitation"
down_revision: str | None = "0005_p3_extraction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

P4_AUDIT_EVENT_TYPES = (
    "STAKEHOLDER_CREATED",
    "INTERVIEW_SESSION_CREATED",
    "INTERVIEW_SESSION_PAUSED",
    "INTERVIEW_SESSION_RESUMED",
    "INTERVIEW_SESSION_COMPLETED",
    "INTERVIEW_SESSION_STALLED",
    "QUESTION_GENERATED",
    "UTTERANCE_RECORDED",
    "ANSWER_ASSESSED",
    "QUALITY_FINDING_RAISED",
    "CLARIFICATION_RAISED",
    "CLARIFICATION_ANSWERED",
    "CLARIFICATION_DISMISSED",
    "CLARIFICATION_REANALYSED",
)

#: Enum types this migration creates (and its downgrade drops).
ENUM_NAMES = (
    "stakeholder_authority_enum",
    "interview_session_kind_enum",
    "interview_session_status_enum",
    "speaker_kind_enum",
    "quality_finding_type_enum",
    "finding_severity_enum",
    "quality_finding_status_enum",
    "finding_detector_enum",
    "clarification_status_enum",
    "reanalysis_status_enum",
)

IMMUTABILITY_SQL = """
-- Utterances: append-only traceability roots (G.3), except a project cascade.
CREATE TRIGGER utterance_append_only
    BEFORE UPDATE OR DELETE ON utterance
    FOR EACH ROW EXECUTE FUNCTION reqpilot_project_content_append_only();

-- Sessions and stakeholders are mutable, but never deleted except by a cascade.
CREATE OR REPLACE FUNCTION reqpilot_no_direct_delete()
RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION '% rows are never deleted directly (architecture G.3)', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER interview_session_no_delete
    BEFORE DELETE ON interview_session
    FOR EACH ROW EXECUTE FUNCTION reqpilot_no_direct_delete();
CREATE TRIGGER stakeholder_no_delete
    BEFORE DELETE ON stakeholder
    FOR EACH ROW EXECUTE FUNCTION reqpilot_no_direct_delete();

-- A session's identity (project, stakeholder, kind, template) is fixed.
CREATE OR REPLACE FUNCTION reqpilot_interview_session_guard()
RETURNS trigger AS $$
BEGIN
    IF NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.stakeholder_id IS DISTINCT FROM OLD.stakeholder_id
       OR NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.template_id IS DISTINCT FROM OLD.template_id
       OR NEW.template_version IS DISTINCT FROM OLD.template_version
       OR NEW.sensitivity IS DISTINCT FROM OLD.sensitivity THEN
        RAISE EXCEPTION 'a session''s project, stakeholder and template are fixed';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER interview_session_guard
    BEFORE UPDATE ON interview_session
    FOR EACH ROW EXECUTE FUNCTION reqpilot_interview_session_guard();

-- A quality finding's content is immutable; only its status may move.
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

CREATE TRIGGER quality_finding_guard
    BEFORE UPDATE OR DELETE ON quality_finding
    FOR EACH ROW EXECUTE FUNCTION reqpilot_quality_finding_guard();

-- A clarification's question and binding are immutable; it is resolved once.
CREATE OR REPLACE FUNCTION reqpilot_clarification_guard()
RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'clarifications are never deleted';
    END IF;
    IF NEW.requirement_version_id IS DISTINCT FROM OLD.requirement_version_id
       OR NEW.quality_finding_id IS DISTINCT FROM OLD.quality_finding_id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.question IS DISTINCT FROM OLD.question
       OR NEW.expected_answer_shape IS DISTINCT FROM OLD.expected_answer_shape
       OR NEW.asked_of_stakeholder_id IS DISTINCT FROM OLD.asked_of_stakeholder_id
       OR NEW.question_utterance_id IS DISTINCT FROM OLD.question_utterance_id THEN
        RAISE EXCEPTION 'a clarification''s question and binding are immutable';
    END IF;
    IF OLD.status <> 'OPEN' AND (
           NEW.status IS DISTINCT FROM OLD.status
           OR NEW.answer_utterance_id IS DISTINCT FROM OLD.answer_utterance_id
           OR NEW.dismissed_reason IS DISTINCT FROM OLD.dismissed_reason) THEN
        RAISE EXCEPTION 'a clarification is answered or dismissed once';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER clarification_guard
    BEFORE UPDATE OR DELETE ON clarification
    FOR EACH ROW EXECUTE FUNCTION reqpilot_clarification_guard();
"""

IMMUTABILITY_DOWN_SQL = """
DROP TRIGGER IF EXISTS clarification_guard ON clarification;
DROP TRIGGER IF EXISTS quality_finding_guard ON quality_finding;
DROP TRIGGER IF EXISTS interview_session_guard ON interview_session;
DROP TRIGGER IF EXISTS stakeholder_no_delete ON stakeholder;
DROP TRIGGER IF EXISTS interview_session_no_delete ON interview_session;
DROP TRIGGER IF EXISTS utterance_append_only ON utterance;
DROP FUNCTION IF EXISTS reqpilot_clarification_guard();
DROP FUNCTION IF EXISTS reqpilot_quality_finding_guard();
DROP FUNCTION IF EXISTS reqpilot_interview_session_guard();
DROP FUNCTION IF EXISTS reqpilot_no_direct_delete();
"""


def _existing_enum(name: str, *values: str, is_postgres: bool) -> sa.types.TypeEngine:
    """An enum type an earlier migration created: reused, never re-created."""
    if is_postgres:
        return postgresql.ENUM(*values, name=name, create_type=False)
    return sa.Enum(*values, name=name)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    json_type = postgresql.JSONB() if is_postgres else sa.JSON()

    if is_postgres:
        for value in P4_AUDIT_EVENT_TYPES:
            op.execute(f"ALTER TYPE audit_event_type_enum ADD VALUE IF NOT EXISTS '{value}'")

    authority = sa.Enum(
        "DECISION_MAKER", "CONTRIBUTOR", "INFORMANT", name="stakeholder_authority_enum"
    )
    session_kind = sa.Enum("INTERVIEW", "CLARIFICATION", name="interview_session_kind_enum")
    session_status = sa.Enum(
        "ACTIVE", "PAUSED", "COMPLETED", "STALLED", name="interview_session_status_enum"
    )
    speaker_kind = sa.Enum("SYSTEM", "STAKEHOLDER", name="speaker_kind_enum")
    finding_type = sa.Enum(
        "AMBIGUITY",
        "INCOMPLETENESS",
        "UNTESTABILITY",
        "DUPLICATION",
        "UNDEFINED_TERM",
        "MISSING_SOURCE",
        "INCONSISTENCY",
        name="quality_finding_type_enum",
    )
    severity = sa.Enum("LOW", "MEDIUM", "HIGH", name="finding_severity_enum")
    finding_status = sa.Enum("OPEN", "RESOLVED", "DISMISSED", name="quality_finding_status_enum")
    detector = sa.Enum("HUMAN", "AGENT", name="finding_detector_enum")
    clarification_status = sa.Enum(
        "OPEN", "ANSWERED", "DISMISSED", name="clarification_status_enum"
    )
    reanalysis = sa.Enum(
        "PENDING", "NEW_VERSION", "NO_CHANGE", "FAILED", name="reanalysis_status_enum"
    )
    sensitivity = _existing_enum(
        "data_sensitivity_enum",
        "SYNTHETIC",
        "UNCLASSIFIED",
        "CONFIDENTIAL",
        is_postgres=is_postgres,
    )

    op.create_table(
        "stakeholder",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("stakeholder_role", sa.String(50), nullable=False),
        sa.Column("authority_level", authority, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_stakeholder"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_stakeholder_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name="fk_stakeholder_user_id_app_user",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("id", "project_id", name="uq_stakeholder_id"),
        sa.CheckConstraint("length(name) >= 1", name="ck_stakeholder_name_not_empty"),
    )
    op.create_index("ix_stakeholder_project_id", "stakeholder", ["project_id"])

    op.create_table(
        "interview_session",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("stakeholder_id", sa.Uuid(), nullable=False),
        sa.Column("kind", session_kind, nullable=False),
        sa.Column("template_id", sa.String(100), nullable=True),
        sa.Column("template_version", sa.String(20), nullable=True),
        sa.Column("sensitivity", sensitivity, nullable=False),
        sa.Column("status", session_status, nullable=False),
        sa.Column("topic_coverage", json_type, nullable=False),
        sa.Column("current_topic", sa.String(64), nullable=True),
        sa.Column("pending_question_id", sa.Uuid(), nullable=True),
        sa.Column("followups_this_topic", sa.Integer(), nullable=False),
        sa.Column("pending_issue", sa.Text(), nullable=True),
        sa.Column("unassessed_answer_id", sa.Uuid(), nullable=True),
        sa.Column("graph_run_id", sa.Uuid(), nullable=True),
        sa.Column("stall_reason", sa.String(300), nullable=True),
        sa.Column("questions_asked", sa.Integer(), nullable=False),
        sa.Column("followups_asked", sa.Integer(), nullable=False),
        sa.Column("started_by", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_interview_session"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_interview_session_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stakeholder_id", "project_id"],
            ["stakeholder.id", "stakeholder.project_id"],
            name="fk_interview_session_stakeholder_same_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["graph_run_id"],
            ["graph_run.id"],
            name="fk_interview_session_graph_run_id_graph_run",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("id", "project_id", name="uq_interview_session_id"),
        sa.CheckConstraint(
            "followups_this_topic >= 0", name="ck_interview_session_followups_not_negative"
        ),
        sa.CheckConstraint(
            "(kind = 'INTERVIEW' AND template_id IS NOT NULL) OR kind = 'CLARIFICATION'",
            name="ck_interview_session_interview_has_template",
        ),
    )
    op.create_index("ix_interview_session_project_id", "interview_session", ["project_id"])
    op.create_index("ix_interview_session_stakeholder_id", "interview_session", ["stakeholder_id"])
    op.create_index(
        "ix_interview_session_project_status", "interview_session", ["project_id", "status"]
    )

    op.create_table(
        "utterance",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("speaker_kind", speaker_kind, nullable=False),
        sa.Column("speaker_ref", sa.Uuid(), nullable=True),
        sa.Column("stakeholder_role", sa.String(50), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("topic_id", sa.String(64), nullable=True),
        sa.Column("is_followup", sa.Boolean(), nullable=False),
        sa.Column("replies_to_id", sa.Uuid(), nullable=True),
        sa.Column("recorded_by", sa.Uuid(), nullable=True),
        sa.Column("on_behalf", sa.Boolean(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_utterance"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_utterance_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "project_id"],
            ["interview_session.id", "interview_session.project_id"],
            name="fk_utterance_session_same_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["replies_to_id"],
            ["utterance.id"],
            name="fk_utterance_replies_to_id_utterance",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_run.id"],
            name="fk_utterance_agent_run_id_agent_run",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("session_id", "seq", name="uq_utterance_session_id"),
        sa.CheckConstraint("seq >= 1", name="ck_utterance_seq_positive"),
        sa.CheckConstraint("length(text) >= 1", name="ck_utterance_text_not_empty"),
        sa.CheckConstraint(
            "(speaker_kind = 'SYSTEM' AND speaker_ref IS NULL) OR "
            "(speaker_kind = 'STAKEHOLDER' AND speaker_ref IS NOT NULL "
            "AND recorded_by IS NOT NULL)",
            name="ck_utterance_speaker_consistent",
        ),
    )
    op.create_index(
        "ix_utterance_project_session_seq", "utterance", ["project_id", "session_id", "seq"]
    )

    op.create_table(
        "quality_finding",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("requirement_version_id", sa.Uuid(), nullable=False),
        sa.Column("finding_type", finding_type, nullable=False),
        sa.Column("severity", severity, nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("span_quote", sa.Text(), nullable=True),
        sa.Column("status", finding_status, nullable=False),
        sa.Column("detected_by", detector, nullable=False),
        sa.Column("recorded_by", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_quality_finding"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_quality_finding_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requirement_version_id"],
            ["requirement_version.id"],
            name="fk_quality_finding_requirement_version_id_requirement_version",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id", "requirement_version_id", name="uq_quality_finding_id"),
        sa.CheckConstraint("length(rationale) >= 1", name="ck_quality_finding_rationale_not_empty"),
    )
    op.create_index(
        "ix_quality_finding_project_version",
        "quality_finding",
        ["project_id", "requirement_version_id"],
    )

    op.create_table(
        "clarification",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("requirement_version_id", sa.Uuid(), nullable=False),
        sa.Column("quality_finding_id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("expected_answer_shape", sa.String(300), nullable=False),
        sa.Column("status", clarification_status, nullable=False),
        sa.Column("asked_of_stakeholder_id", sa.Uuid(), nullable=False),
        sa.Column("assignee_user_id", sa.Uuid(), nullable=True),
        sa.Column("raised_by", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("question_utterance_id", sa.Uuid(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("answer_utterance_id", sa.Uuid(), nullable=True),
        sa.Column("answered_by", sa.Uuid(), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_reason", sa.Text(), nullable=True),
        sa.Column("dismissed_by", sa.Uuid(), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reanalysis_status", reanalysis, nullable=True),
        sa.Column("reanalysis_run_id", sa.Uuid(), nullable=True),
        sa.Column("resulting_version_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_clarification"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_clarification_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requirement_version_id"],
            ["requirement_version.id"],
            name="fk_clarification_requirement_version_id_requirement_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["quality_finding_id", "requirement_version_id"],
            ["quality_finding.id", "quality_finding.requirement_version_id"],
            name="fk_clarification_finding_same_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["asked_of_stakeholder_id", "project_id"],
            ["stakeholder.id", "stakeholder.project_id"],
            name="fk_clarification_stakeholder_same_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["interview_session.id"],
            name="fk_clarification_session_id_interview_session",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["question_utterance_id"],
            ["utterance.id"],
            name="fk_clarification_question_utterance_id_utterance",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["answer_utterance_id"],
            ["utterance.id"],
            name="fk_clarification_answer_utterance_id_utterance",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_run.id"],
            name="fk_clarification_agent_run_id_agent_run",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reanalysis_run_id"],
            ["graph_run.id"],
            name="fk_clarification_reanalysis_run_id_graph_run",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["resulting_version_id"],
            ["requirement_version.id"],
            name="fk_clarification_resulting_version_id_requirement_version",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("length(question) >= 1", name="ck_clarification_question_not_empty"),
        sa.CheckConstraint(
            "status <> 'ANSWERED' OR answer_utterance_id IS NOT NULL",
            name="ck_clarification_answered_has_answer",
        ),
        sa.CheckConstraint(
            "status <> 'DISMISSED' OR length(coalesce(dismissed_reason, '')) >= 1",
            name="ck_clarification_dismissed_has_reason",
        ),
    )
    op.create_index(
        "uq_clarification_open_per_finding",
        "clarification",
        ["quality_finding_id"],
        unique=True,
        postgresql_where=sa.text("status = 'OPEN'"),
        sqlite_where=sa.text("status = 'OPEN'"),
    )
    op.create_index("ix_clarification_project_status", "clarification", ["project_id", "status"])

    if is_postgres:
        op.execute(IMMUTABILITY_SQL)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(IMMUTABILITY_DOWN_SQL)

    op.drop_table("clarification")
    op.drop_table("quality_finding")
    op.drop_table("utterance")
    op.drop_table("interview_session")
    op.drop_table("stakeholder")

    for enum_name in ENUM_NAMES:
        sa.Enum(name=enum_name).drop(bind, checkfirst=True)
    # The P4 audit event types stay on audit_event_type_enum (PostgreSQL cannot
    # remove enum values). data_sensitivity_enum belongs to P3 and is left alone.
