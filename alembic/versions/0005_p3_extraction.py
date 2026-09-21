"""P3 extraction and classification: project corpus, proposals, labels, review queue.

Additive only. No earlier migration is touched, and no existing table changes
shape except ``agent_run``, which gains five nullable columns (G.7).

What carries architectural weight:

* **The project corpus** (``source_document``, ``source_chunk``; G.3, J.1):
  append-only traceability roots. ``source_chunk.embedding`` exists but is left
  empty in P3 - no unmasked project text may reach the vector store (J.2).
* **Extraction proposals** (``extraction_candidate``): the proposal is immutable;
  the deterministic decision is written once, from ``PROPOSED``.
* **Classification** (``requirement_classification``; G.4) and acceptance
  criteria (``acceptance_criterion``; G.4): append-only rows bound to a version,
  so labelling never rewrites a requirement version.
* **The review queue** (``review_item``; M.5): the resolution is written once.
* **Provenance** (``prompt_template``, ``model_version``; G.7): append-only.

Triggers repeat, at the database, what the ORM guard already enforces. Rows
belonging to a project may still disappear through the project-deletion
cascade (``FR-ADM-006``): those deletes run nested inside the foreign-key
action, at ``pg_trigger_depth() > 1``, and are the only ones allowed.

Revision ID: 0005_p3_extraction
Revises: 0004_p2_knowledge_base
Create Date: P3
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0005_p3_extraction"
down_revision: str | None = "0004_p2_knowledge_base"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMENSION = 384

P3_AUDIT_EVENT_TYPES = (
    "EXTRACTION_PROPOSED",
    "EXTRACTION_VALIDATED",
    "CLASSIFICATION_PROPOSED",
    "HUMAN_OVERRIDE",
    "REVIEW_ITEM_RAISED",
    "REVIEW_ITEM_RESOLVED",
    "REQUIREMENTS_MERGED",
)

#: Enum types this migration creates (and its downgrade drops).
ENUM_NAMES = (
    "source_document_type_enum",
    "data_sensitivity_enum",
    "masking_status_enum",
    "candidate_status_enum",
    "requirement_kind_enum",
    "proposal_source_enum",
    "review_reason_enum",
    "review_status_enum",
    "review_resolution_enum",
)

IMMUTABILITY_SQL = """
-- Project content: append-only, except when a project deletion cascades.
CREATE OR REPLACE FUNCTION reqpilot_project_content_append_only()
RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION '% is append-only; % is not permitted (architecture G.3, G.4)',
        TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER source_document_append_only
    BEFORE UPDATE OR DELETE ON source_document
    FOR EACH ROW EXECUTE FUNCTION reqpilot_project_content_append_only();
CREATE TRIGGER source_chunk_append_only
    BEFORE UPDATE OR DELETE ON source_chunk
    FOR EACH ROW EXECUTE FUNCTION reqpilot_project_content_append_only();
CREATE TRIGGER requirement_classification_append_only
    BEFORE UPDATE OR DELETE ON requirement_classification
    FOR EACH ROW EXECUTE FUNCTION reqpilot_project_content_append_only();
CREATE TRIGGER acceptance_criterion_append_only
    BEFORE UPDATE OR DELETE ON acceptance_criterion
    FOR EACH ROW EXECUTE FUNCTION reqpilot_project_content_append_only();

-- Provenance records are not project-scoped and are never removed.
CREATE OR REPLACE FUNCTION reqpilot_provenance_append_only()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only; % is not permitted (architecture G.7)',
        TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER prompt_template_append_only
    BEFORE UPDATE OR DELETE ON prompt_template
    FOR EACH ROW EXECUTE FUNCTION reqpilot_provenance_append_only();
CREATE TRIGGER model_version_append_only
    BEFORE UPDATE OR DELETE ON model_version
    FOR EACH ROW EXECUTE FUNCTION reqpilot_provenance_append_only();

-- An extraction proposal is fixed; its decision is written once.
CREATE OR REPLACE FUNCTION reqpilot_extraction_candidate_guard()
RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'extraction_candidate rows are never deleted';
    END IF;
    IF (NEW.id, NEW.project_id, NEW.graph_run_id, NEW.agent_run_id, NEW.candidate_key,
        NEW.ordinal, NEW.statement, NEW.requirement_kind, NEW.proposal, NEW.review_signal,
        NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.project_id, OLD.graph_run_id, OLD.agent_run_id, OLD.candidate_key,
        OLD.ordinal, OLD.statement, OLD.requirement_kind, OLD.proposal, OLD.review_signal,
        OLD.created_at) THEN
        RAISE EXCEPTION 'an extraction proposal is immutable';
    END IF;
    IF OLD.status <> 'PROPOSED' THEN
        RAISE EXCEPTION 'an extraction candidate is decided once';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER extraction_candidate_guard
    BEFORE UPDATE OR DELETE ON extraction_candidate
    FOR EACH ROW EXECUTE FUNCTION reqpilot_extraction_candidate_guard();

-- A review item's subject never changes; its resolution is written once.
CREATE OR REPLACE FUNCTION reqpilot_review_item_guard()
RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'review_item rows are never deleted';
    END IF;
    IF (NEW.id, NEW.project_id, NEW.reason, NEW.subject_type, NEW.subject_id,
        NEW.related_subject_id, NEW.requirement_version_id, NEW.category, NEW.review_signal,
        NEW.graph_run_id, NEW.agent_run_id, NEW.detail, NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.project_id, OLD.reason, OLD.subject_type, OLD.subject_id,
        OLD.related_subject_id, OLD.requirement_version_id, OLD.category, OLD.review_signal,
        OLD.graph_run_id, OLD.agent_run_id, OLD.detail, OLD.created_at) THEN
        RAISE EXCEPTION 'a review item''s subject and detail are immutable';
    END IF;
    IF OLD.status <> 'OPEN' OR NEW.status <> 'RESOLVED' THEN
        RAISE EXCEPTION 'a review item is resolved once, from OPEN to RESOLVED';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER review_item_guard
    BEFORE UPDATE OR DELETE ON review_item
    FOR EACH ROW EXECUTE FUNCTION reqpilot_review_item_guard();
"""

IMMUTABILITY_DOWN_SQL = """
DROP TRIGGER IF EXISTS review_item_guard ON review_item;
DROP TRIGGER IF EXISTS extraction_candidate_guard ON extraction_candidate;
DROP TRIGGER IF EXISTS model_version_append_only ON model_version;
DROP TRIGGER IF EXISTS prompt_template_append_only ON prompt_template;
DROP TRIGGER IF EXISTS acceptance_criterion_append_only ON acceptance_criterion;
DROP TRIGGER IF EXISTS requirement_classification_append_only ON requirement_classification;
DROP TRIGGER IF EXISTS source_chunk_append_only ON source_chunk;
DROP TRIGGER IF EXISTS source_document_append_only ON source_document;
DROP FUNCTION IF EXISTS reqpilot_review_item_guard();
DROP FUNCTION IF EXISTS reqpilot_extraction_candidate_guard();
DROP FUNCTION IF EXISTS reqpilot_provenance_append_only();
DROP FUNCTION IF EXISTS reqpilot_project_content_append_only();
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
        for value in P3_AUDIT_EVENT_TYPES:
            op.execute(f"ALTER TYPE audit_event_type_enum ADD VALUE IF NOT EXISTS '{value}'")

    doc_type = sa.Enum(
        "TRANSCRIPT",
        "MEETING_NOTES",
        "POLICY",
        "LEGACY_SPECIFICATION",
        "AUDIT_FINDING",
        name="source_document_type_enum",
    )
    sensitivity = sa.Enum("SYNTHETIC", "UNCLASSIFIED", "CONFIDENTIAL", name="data_sensitivity_enum")
    masking = sa.Enum("NOT_MASKED", "MASKED", name="masking_status_enum")
    candidate_status = sa.Enum(
        "PROPOSED", "ACCEPTED", "MERGED", "REJECTED", name="candidate_status_enum"
    )
    requirement_kind = sa.Enum("FUNCTIONAL", "NON_FUNCTIONAL", name="requirement_kind_enum")
    proposal_source = sa.Enum("AGENT", "HUMAN", name="proposal_source_enum")
    review_reason = sa.Enum(
        "MALFORMED_OUTPUT",
        "UNRESOLVED_SOURCE",
        "EXTRACTION_INVALID",
        "LOW_EXTRACTION_SIGNAL",
        "LOW_CLASSIFICATION_SIGNAL",
        "UNKNOWN_LABEL",
        "CLASSIFICATION_FAILED",
        "POSSIBLE_DUPLICATE",
        "ACCEPTANCE_CRITERIA_INVALID",
        name="review_reason_enum",
    )
    review_status = sa.Enum("OPEN", "RESOLVED", name="review_status_enum")
    review_resolution = sa.Enum(
        "ACCEPTED",
        "REJECTED",
        "OVERRIDDEN",
        "MERGED",
        "KEPT_DISTINCT",
        "ACKNOWLEDGED",
        name="review_resolution_enum",
    )
    chunk_strategy = _existing_enum(
        "chunk_strategy_enum", "CLAUSE", "WINDOW", "SECTION", "UTTERANCE", is_postgres=is_postgres
    )
    category = _existing_enum(
        "requirement_category_enum",
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
        is_postgres=is_postgres,
    )
    agent_role = _existing_enum(
        "agent_role_enum",
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
        is_postgres=is_postgres,
    )

    # --- agent_run: generation provenance and cost (G.7, ET-08) ---------------
    with op.batch_alter_table("agent_run") as batch:
        batch.add_column(sa.Column("attempts", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("error_code", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("review_signal", sa.Float(), nullable=True))
        batch.add_column(sa.Column("cost_estimate", sa.Float(), nullable=True))
        batch.add_column(sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))

    # --- source_document (G.3) ------------------------------------------------
    op.create_table(
        "source_document",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("doc_type", doc_type, nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("filename", sa.String(length=300), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("sensitivity", sensitivity, nullable=False),
        sa.Column("masking_status", masking, nullable=False),
        sa.Column("masker_id", sa.String(length=100), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_source_document_project_id_project",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_document"),
        sa.UniqueConstraint(
            "project_id", "content_hash", name="uq_source_document_project_content"
        ),
        sa.CheckConstraint("char_count >= 1", name="ck_source_document_not_empty"),
    )
    op.create_index("ix_source_document_project_id", "source_document", ["project_id"])

    # --- source_chunk (G.3, J.3) -------------------------------------------------
    op.create_table(
        "source_chunk",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("source_document_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("strategy", chunk_strategy, nullable=False),
        sa.Column("speaker", sa.String(length=200), nullable=True),
        sa.Column("structure_label", sa.String(length=200), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column(
            "embedding",
            Vector(EMBEDDING_DIMENSION) if is_postgres else sa.Text(),
            nullable=True,
        ),
        sa.Column("embedding_model", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_source_chunk_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["source_document.id"],
            name="fk_source_chunk_source_document_id_source_document",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_chunk"),
        sa.UniqueConstraint(
            "source_document_id", "ordinal", name="uq_source_chunk_document_ordinal"
        ),
        sa.CheckConstraint(
            "char_start >= 0 AND char_end > char_start", name="ck_source_chunk_span_ordered"
        ),
    )
    op.create_index("ix_source_chunk_project_id", "source_chunk", ["project_id"])
    op.create_index("ix_source_chunk_source_document_id", "source_chunk", ["source_document_id"])

    # --- prompt_template / model_version (G.7) ---------------------------------
    op.create_table(
        "prompt_template",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("role", agent_role, nullable=False),
        sa.Column("version", sa.String(length=20), nullable=False),
        sa.Column("contract_version", sa.String(length=20), nullable=False),
        sa.Column("template_sha256", sa.String(length=64), nullable=False),
        sa.Column("template_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_prompt_template"),
        sa.UniqueConstraint("name", "version", name="uq_prompt_template_name_version"),
    )
    op.create_table(
        "model_version",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model_id", sa.String(length=200), nullable=False),
        sa.Column("params_hash", sa.String(length=64), nullable=False),
        sa.Column("params", json_type, nullable=False),
        sa.Column("is_model", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_model_version"),
        sa.UniqueConstraint(
            "provider", "model_id", "params_hash", name="uq_model_version_provider_model_params"
        ),
    )

    # --- extraction_candidate ----------------------------------------------------
    op.create_table(
        "extraction_candidate",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("graph_run_id", sa.Uuid(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_key", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("requirement_kind", requirement_kind, nullable=False),
        sa.Column("proposal", json_type, nullable=False),
        sa.Column("review_signal", sa.Float(), nullable=False),
        sa.Column("status", candidate_status, nullable=False),
        sa.Column("spans", json_type, nullable=False),
        sa.Column("original_text", sa.Text(), nullable=True),
        sa.Column("findings", json_type, nullable=False),
        sa.Column("merged_into_id", sa.Uuid(), nullable=True),
        sa.Column("requirement_version_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_extraction_candidate_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["graph_run_id"],
            ["graph_run.id"],
            name="fk_extraction_candidate_graph_run_id_graph_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_run.id"],
            name="fk_extraction_candidate_agent_run_id_agent_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["merged_into_id"],
            ["extraction_candidate.id"],
            name="fk_extraction_candidate_merged_into_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requirement_version_id"],
            ["requirement_version.id"],
            name="fk_extraction_candidate_requirement_version_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_extraction_candidate"),
        sa.UniqueConstraint(
            "graph_run_id", "candidate_key", name="uq_extraction_candidate_run_candidate_key"
        ),
        sa.CheckConstraint(
            "(status = 'PROPOSED' AND decided_at IS NULL) OR "
            "(status <> 'PROPOSED' AND decided_at IS NOT NULL)",
            name="ck_extraction_candidate_decided_once",
        ),
        sa.CheckConstraint(
            "status <> 'ACCEPTED' OR requirement_version_id IS NOT NULL",
            name="ck_extraction_candidate_accepted_has_version",
        ),
        sa.CheckConstraint(
            "status <> 'MERGED' OR merged_into_id IS NOT NULL",
            name="ck_extraction_candidate_merged_has_target",
        ),
    )
    op.create_index("ix_extraction_candidate_project_id", "extraction_candidate", ["project_id"])
    op.create_index(
        "ix_extraction_candidate_graph_run_id", "extraction_candidate", ["graph_run_id"]
    )
    op.create_index(
        "ix_extraction_candidate_project_status", "extraction_candidate", ["project_id", "status"]
    )

    # --- requirement_classification (G.4) ----------------------------------------
    op.create_table(
        "requirement_classification",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("requirement_version_id", sa.Uuid(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("category", category, nullable=False),
        sa.Column("review_signal", sa.Float(), nullable=True),
        sa.Column("source", proposal_source, nullable=False),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("change_reason", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_requirement_classification_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requirement_version_id"],
            ["requirement_version.id"],
            name="fk_requirement_classification_version_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_run.id"],
            name="fk_requirement_classification_agent_run_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_requirement_classification"),
        sa.UniqueConstraint(
            "requirement_version_id",
            "revision_no",
            "category",
            name="uq_requirement_classification_version_revision_category",
        ),
        sa.CheckConstraint(
            "revision_no >= 1", name="ck_requirement_classification_revision_positive"
        ),
        sa.CheckConstraint(
            "review_signal IS NULL OR (review_signal >= 0 AND review_signal <= 1)",
            name="ck_requirement_classification_signal_in_range",
        ),
        sa.CheckConstraint(
            "(source = 'AGENT' AND review_signal IS NOT NULL) OR "
            "(source = 'HUMAN' AND review_signal IS NULL AND needs_review = false)",
            name="ck_requirement_classification_source_signal_coherent",
        ),
    )
    op.create_index(
        "ix_requirement_classification_project_id", "requirement_classification", ["project_id"]
    )
    op.create_index(
        "ix_requirement_classification_requirement_version_id",
        "requirement_classification",
        ["requirement_version_id"],
    )

    # --- acceptance_criterion (G.4) ----------------------------------------------
    op.create_table(
        "acceptance_criterion",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("requirement_version_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("given_text", sa.Text(), nullable=False),
        sa.Column("when_text", sa.Text(), nullable=False),
        sa.Column("then_text", sa.Text(), nullable=False),
        sa.Column("source", proposal_source, nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("copied_from_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_acceptance_criterion_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requirement_version_id"],
            ["requirement_version.id"],
            name="fk_acceptance_criterion_version_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_run.id"],
            name="fk_acceptance_criterion_agent_run_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["copied_from_id"],
            ["acceptance_criterion.id"],
            name="fk_acceptance_criterion_copied_from_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_acceptance_criterion"),
        sa.UniqueConstraint(
            "requirement_version_id", "ordinal", name="uq_acceptance_criterion_version_ordinal"
        ),
    )
    op.create_index("ix_acceptance_criterion_project_id", "acceptance_criterion", ["project_id"])
    op.create_index(
        "ix_acceptance_criterion_requirement_version_id",
        "acceptance_criterion",
        ["requirement_version_id"],
    )

    # --- review_item (M.5) ------------------------------------------------------
    op.create_table(
        "review_item",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("reason", review_reason, nullable=False),
        sa.Column("subject_type", sa.String(length=50), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("related_subject_id", sa.Uuid(), nullable=True),
        sa.Column("requirement_version_id", sa.Uuid(), nullable=True),
        sa.Column("category", category, nullable=True),
        sa.Column("review_signal", sa.Float(), nullable=True),
        sa.Column("graph_run_id", sa.Uuid(), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("detail", json_type, nullable=False),
        sa.Column("status", review_status, nullable=False),
        sa.Column("resolution", review_resolution, nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_review_item_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requirement_version_id"],
            ["requirement_version.id"],
            name="fk_review_item_requirement_version_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["graph_run_id"],
            ["graph_run.id"],
            name="fk_review_item_graph_run_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_run.id"],
            name="fk_review_item_agent_run_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_item"),
        sa.CheckConstraint(
            "(status = 'OPEN' AND resolution IS NULL AND resolved_at IS NULL "
            " AND resolved_by IS NULL) OR "
            "(status = 'RESOLVED' AND resolution IS NOT NULL AND resolved_at IS NOT NULL "
            " AND resolved_by IS NOT NULL)",
            name="ck_review_item_resolution_coherent",
        ),
    )
    op.create_index("ix_review_item_project_status", "review_item", ["project_id", "status"])
    op.create_index(
        "ix_review_item_requirement_version_id", "review_item", ["requirement_version_id"]
    )

    if is_postgres:
        op.execute(IMMUTABILITY_SQL)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(IMMUTABILITY_DOWN_SQL)

    op.drop_table("review_item")
    op.drop_table("acceptance_criterion")
    op.drop_table("requirement_classification")
    op.drop_table("extraction_candidate")
    op.drop_table("model_version")
    op.drop_table("prompt_template")
    op.drop_table("source_chunk")
    op.drop_table("source_document")

    with op.batch_alter_table("agent_run") as batch:
        batch.drop_column("finished_at")
        batch.drop_column("cost_estimate")
        batch.drop_column("review_signal")
        batch.drop_column("error_code")
        batch.drop_column("attempts")

    for enum_name in ENUM_NAMES:
        sa.Enum(name=enum_name).drop(bind, checkfirst=True)
    # The P3 audit event types stay on audit_event_type_enum: PostgreSQL cannot
    # remove enum values, and the foundation downgrade drops the whole type.
    # chunk_strategy_enum, requirement_category_enum and agent_role_enum belong
    # to earlier migrations and are left alone.
