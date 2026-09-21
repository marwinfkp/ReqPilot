"""P2 knowledge base and retrieval: corpus, allowlist, evidence (architecture G.5, G.6, J).

Additive only. No earlier migration is touched.

What carries architectural weight, all PostgreSQL-specific:

* **Retrieval indexes.** An HNSW index for cosine distance on the 384-dimension
  embedding (ADR-004), and a GIN index on the English ``tsvector`` of chunk text
  for the keyword half of hybrid search. The GIN index is on the *expression*
  ``to_tsvector('english'::regconfig, text)`` - the retrieval query uses the
  identical expression, which is what lets PostgreSQL use it.
* **Immutability triggers**, the database-level layer behind the ORM guard:
  ``normative_source``, ``control`` and ``knowledge_chunk`` refuse UPDATE and
  DELETE; ``evidence`` refuses them too, except when a project deletion cascades
  (``FR-ADM-006``); ``knowledge_item`` refuses DELETE and allows UPDATE only of
  the supersession columns, only from ``ACTIVE`` to ``SUPERSEDED``.

On both dialects, check constraints keep supersession coherent: a superseded item
records the KB version and the kind of its supersession, and the kind agrees with
the successor - a retirement has none, a new version or a replacement always has
one - so retiring and replacing stay distinguishable in the data (``FR-RAG-006``).

The allowlist itself needs no trigger: it is enforced by the retrieval query's
join (architecture J.4), which is where the tests prove it.

Revision ID: 0004_p2_knowledge_base
Revises: 0003_p1_postgres_enum_repair
Create Date: P2
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0004_p2_knowledge_base"
down_revision: str | None = "0003_p1_postgres_enum_repair"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMENSION = 384

P2_AUDIT_EVENT_TYPES = (
    "KB_ITEM_ADDED",
    "KB_ITEM_SUPERSEDED",
    "SOURCE_INGESTED",
    "KB_SOURCE_ADDED",
    "KB_CONTROL_ADDED",
    "KB_SCOPE_CHANGED",
)

IMMUTABILITY_SQL = """
CREATE OR REPLACE FUNCTION reqpilot_forbid_kb_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only; % is not permitted (architecture G.5, J.6)',
        TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER normative_source_append_only
    BEFORE UPDATE OR DELETE ON normative_source
    FOR EACH ROW EXECUTE FUNCTION reqpilot_forbid_kb_mutation();
CREATE TRIGGER control_append_only
    BEFORE UPDATE OR DELETE ON control
    FOR EACH ROW EXECUTE FUNCTION reqpilot_forbid_kb_mutation();
CREATE TRIGGER knowledge_chunk_append_only
    BEFORE UPDATE OR DELETE ON knowledge_chunk
    FOR EACH ROW EXECUTE FUNCTION reqpilot_forbid_kb_mutation();

-- Evidence is append-only. The one exception is a referential action fired by
-- deleting its project (FR-ADM-006): those run nested inside the foreign-key
-- trigger, so pg_trigger_depth() is greater than one. A direct UPDATE or DELETE
-- runs at depth one and is refused.
CREATE OR REPLACE FUNCTION reqpilot_evidence_append_only()
RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'evidence is append-only; % is not permitted (architecture J.5)', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER evidence_append_only
    BEFORE UPDATE OR DELETE ON evidence
    FOR EACH ROW EXECUTE FUNCTION reqpilot_evidence_append_only();

-- A knowledge item's content never changes. Only the supersession columns may
-- be written, once, moving the item from ACTIVE to SUPERSEDED.
CREATE OR REPLACE FUNCTION reqpilot_knowledge_item_guard()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'knowledge_item rows are never deleted; supersede the item instead';
    END IF;
    IF (NEW.id, NEW.item_key, NEW.version_no, NEW.normative_source_id, NEW.control_id,
        NEW.title, NEW.clause_ref, NEW.text, NEW.text_origin, NEW.content_hash,
        NEW.applicability, NEW.kb_version, NEW.created_by, NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.item_key, OLD.version_no, OLD.normative_source_id, OLD.control_id,
        OLD.title, OLD.clause_ref, OLD.text, OLD.text_origin, OLD.content_hash,
        OLD.applicability, OLD.kb_version, OLD.created_by, OLD.created_at) THEN
        RAISE EXCEPTION 'knowledge item content is immutable; create a new version instead';
    END IF;
    IF OLD.status <> 'ACTIVE' OR NEW.status <> 'SUPERSEDED' THEN
        RAISE EXCEPTION 'a knowledge item may only move once, from ACTIVE to SUPERSEDED';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER knowledge_item_guard
    BEFORE UPDATE OR DELETE ON knowledge_item
    FOR EACH ROW EXECUTE FUNCTION reqpilot_knowledge_item_guard();
"""

IMMUTABILITY_DOWN_SQL = """
DROP TRIGGER IF EXISTS knowledge_item_guard ON knowledge_item;
DROP TRIGGER IF EXISTS evidence_append_only ON evidence;
DROP TRIGGER IF EXISTS knowledge_chunk_append_only ON knowledge_chunk;
DROP TRIGGER IF EXISTS control_append_only ON control;
DROP TRIGGER IF EXISTS normative_source_append_only ON normative_source;
DROP FUNCTION IF EXISTS reqpilot_knowledge_item_guard();
DROP FUNCTION IF EXISTS reqpilot_evidence_append_only();
DROP FUNCTION IF EXISTS reqpilot_forbid_kb_mutation();
"""

ENUM_NAMES = (
    "normative_source_type_enum",
    "licence_class_enum",
    "text_origin_enum",
    "knowledge_item_status_enum",
    "supersession_kind_enum",
    "chunk_strategy_enum",
    "evidence_kind_enum",
)


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    string_list = postgresql.ARRAY(sa.String(length=100)) if is_postgres else sa.JSON()
    empty_list = sa.text("'{}'") if is_postgres else sa.text("'[]'")

    if is_postgres:
        for value in P2_AUDIT_EVENT_TYPES:
            op.execute(f"ALTER TYPE audit_event_type_enum ADD VALUE IF NOT EXISTS '{value}'")

    source_type = sa.Enum(
        "STATUTE",
        "REGULATORY_DIRECTION",
        "REGULATORY_GUIDANCE",
        "ORG_POLICY",
        "CONTRACTUAL_SCHEME",
        "INDUSTRY_STANDARD",
        "CONTROL_FRAMEWORK",
        "BEST_PRACTICE",
        name="normative_source_type_enum",
    )
    licence_class = sa.Enum(
        "EXTRACT_PERMITTED", "PARAPHRASE_ONLY", "SYNTHETIC", name="licence_class_enum"
    )
    text_origin = sa.Enum(
        "VERBATIM_EXTRACT", "TEAM_PARAPHRASE", "SYNTHETIC", name="text_origin_enum"
    )
    item_status = sa.Enum("ACTIVE", "SUPERSEDED", name="knowledge_item_status_enum")
    supersession_kind = sa.Enum("VERSIONED", "REPLACED", "RETIRED", name="supersession_kind_enum")
    chunk_strategy = sa.Enum("CLAUSE", "WINDOW", "SECTION", "UTTERANCE", name="chunk_strategy_enum")
    evidence_kind = sa.Enum(
        "UTTERANCE", "SOURCE_CHUNK", "KNOWLEDGE_ITEM", name="evidence_kind_enum"
    )

    # --- project knowledge scope (G.3) ----------------------------------
    op.add_column(
        "project",
        sa.Column("jurisdiction_scope", string_list, nullable=False, server_default=empty_list),
    )
    op.add_column("project", sa.Column("kb_version_pin", sa.Integer(), nullable=True))

    # --- normative_source ------------------------------------------------
    op.create_table(
        "normative_source",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_type", source_type, nullable=False),
        sa.Column("issuing_body", sa.String(length=300), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("jurisdiction", sa.String(length=20), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("retrieved_at", sa.Date(), nullable=False),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("licence_class", licence_class, nullable=False),
        sa.Column("licence_note", sa.String(length=2000), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_normative_source"),
        sa.UniqueConstraint(
            "issuing_body", "title", "version", "jurisdiction", name="uq_normative_source_identity"
        ),
    )
    op.create_index("ix_normative_source_jurisdiction", "normative_source", ["jurisdiction"])

    # --- control -----------------------------------------------------------
    op.create_table(
        "control",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("normative_source_id", sa.Uuid(), nullable=False),
        sa.Column("control_ref", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("paraphrase", sa.Text(), nullable=False),
        sa.Column("applicability", string_list, nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["normative_source_id"],
            ["normative_source.id"],
            name="fk_control_normative_source_id_normative_source",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_control"),
        sa.UniqueConstraint("normative_source_id", "control_ref", name="uq_control_source_ref"),
    )
    op.create_index("ix_control_normative_source_id", "control", ["normative_source_id"])

    # --- knowledge_item ------------------------------------------------------
    op.create_table(
        "knowledge_item",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("item_key", sa.String(length=100), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("normative_source_id", sa.Uuid(), nullable=False),
        sa.Column("control_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("clause_ref", sa.String(length=200), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_origin", text_origin, nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("applicability", string_list, nullable=False),
        sa.Column("kb_version", sa.Integer(), nullable=False),
        sa.Column("status", item_status, nullable=False),
        sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
        sa.Column("supersession_kind", supersession_kind, nullable=True),
        sa.Column("superseded_in_kb_version", sa.Integer(), nullable=True),
        sa.Column("supersession_reason", sa.String(length=1000), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["normative_source_id"],
            ["normative_source.id"],
            name="fk_knowledge_item_normative_source_id_normative_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["control_id"],
            ["control.id"],
            name="fk_knowledge_item_control_id_control",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["knowledge_item.id"],
            name="fk_knowledge_item_superseded_by_id_knowledge_item",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_item"),
        sa.UniqueConstraint("item_key", "version_no", name="uq_knowledge_item_key_version"),
        sa.CheckConstraint("version_no >= 1", name="ck_knowledge_item_version_no_positive"),
        sa.CheckConstraint("kb_version >= 1", name="ck_knowledge_item_kb_version_positive"),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND superseded_in_kb_version IS NULL "
            " AND supersession_kind IS NULL AND superseded_by_id IS NULL) OR "
            "(status = 'SUPERSEDED' AND superseded_in_kb_version IS NOT NULL "
            " AND supersession_kind IS NOT NULL)",
            name="ck_knowledge_item_supersession_coherent",
        ),
        sa.CheckConstraint(
            "supersession_kind IS NULL"
            " OR (supersession_kind = 'RETIRED' AND superseded_by_id IS NULL)"
            " OR (supersession_kind IN ('VERSIONED', 'REPLACED')"
            " AND superseded_by_id IS NOT NULL)",
            name="ck_knowledge_item_supersession_kind_matches_successor",
        ),
        sa.CheckConstraint(
            "superseded_in_kb_version IS NULL OR superseded_in_kb_version > kb_version",
            name="ck_knowledge_item_superseded_after_created",
        ),
    )
    op.create_index("ix_knowledge_item_item_key", "knowledge_item", ["item_key"])
    op.create_index(
        "ix_knowledge_item_normative_source_id", "knowledge_item", ["normative_source_id"]
    )
    op.create_index("ix_knowledge_item_kb_version", "knowledge_item", ["kb_version"])
    op.create_index(
        "uq_knowledge_item_active_content",
        "knowledge_item",
        ["normative_source_id", "content_hash"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
        sqlite_where=sa.text("status = 'ACTIVE'"),
    )

    # --- knowledge_chunk -----------------------------------------------------
    op.create_table(
        "knowledge_chunk",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_item_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("strategy", chunk_strategy, nullable=False),
        sa.Column("structure_label", sa.String(length=200), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSION), nullable=False),
        sa.Column("embedding_model", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["knowledge_item_id"],
            ["knowledge_item.id"],
            name="fk_knowledge_chunk_knowledge_item_id_knowledge_item",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_chunk"),
        sa.UniqueConstraint("knowledge_item_id", "ordinal", name="uq_knowledge_chunk_item_ordinal"),
        sa.CheckConstraint(
            "char_start >= 0 AND char_end > char_start", name="ck_knowledge_chunk_span_ordered"
        ),
    )
    op.create_index(
        "ix_knowledge_chunk_knowledge_item_id", "knowledge_chunk", ["knowledge_item_id"]
    )
    if is_postgres:
        op.execute(
            "CREATE INDEX ix_knowledge_chunk_embedding_hnsw ON knowledge_chunk "
            "USING hnsw (embedding vector_cosine_ops)"
        )
        op.execute(
            "CREATE INDEX ix_knowledge_chunk_text_fts ON knowledge_chunk "
            "USING gin (to_tsvector('english'::regconfig, text))"
        )

    # --- source_allowlist ----------------------------------------------------
    op.create_table(
        "source_allowlist",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("normative_source_id", sa.Uuid(), nullable=False),
        sa.Column("added_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_source_allowlist_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["normative_source_id"],
            ["normative_source.id"],
            name="fk_source_allowlist_normative_source_id_normative_source",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_allowlist"),
        sa.UniqueConstraint(
            "project_id", "normative_source_id", name="uq_source_allowlist_project_source"
        ),
    )
    op.create_index("ix_source_allowlist_project_id", "source_allowlist", ["project_id"])

    # --- evidence -----------------------------------------------------------
    op.create_table(
        "evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("kind", evidence_kind, nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_chunk_id", sa.Uuid(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("quote_hash", sa.String(length=64), nullable=False),
        sa.Column("retrieval_id", sa.Uuid(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("retrieval_score", sa.Float(), nullable=False),
        sa.Column("kb_version", sa.Integer(), nullable=False),
        sa.Column("embedding_model", sa.String(length=200), nullable=False),
        sa.Column("ruleset_version", sa.String(length=50), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("graph_run_id", sa.Uuid(), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_evidence_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_chunk_id"],
            ["knowledge_chunk.id"],
            name="fk_evidence_knowledge_chunk_id_knowledge_chunk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["graph_run_id"],
            ["graph_run.id"],
            name="fk_evidence_graph_run_id_graph_run",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_run.id"],
            name="fk_evidence_agent_run_id_agent_run",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evidence"),
        sa.UniqueConstraint(
            "retrieval_id", "knowledge_chunk_id", name="uq_evidence_retrieval_chunk"
        ),
        sa.CheckConstraint(
            "char_start >= 0 AND char_end > char_start", name="ck_evidence_span_ordered"
        ),
        sa.CheckConstraint(
            "kind <> 'KNOWLEDGE_ITEM' OR knowledge_chunk_id IS NOT NULL",
            name="ck_evidence_knowledge_evidence_names_chunk",
        ),
    )
    op.create_index("ix_evidence_project_id", "evidence", ["project_id"])
    op.create_index("ix_evidence_retrieval_id", "evidence", ["retrieval_id"])
    op.create_index("ix_evidence_graph_run_id", "evidence", ["graph_run_id"])

    if is_postgres:
        op.execute(IMMUTABILITY_SQL)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(IMMUTABILITY_DOWN_SQL)

    op.drop_table("evidence")
    op.drop_table("source_allowlist")
    op.drop_table("knowledge_chunk")
    op.drop_table("knowledge_item")
    op.drop_table("control")
    op.drop_table("normative_source")

    with op.batch_alter_table("project") as batch:
        batch.drop_column("kb_version_pin")
        batch.drop_column("jurisdiction_scope")

    for enum_name in ENUM_NAMES:
        sa.Enum(name=enum_name).drop(bind, checkfirst=True)
    # The P2 audit event types stay on audit_event_type_enum: PostgreSQL cannot
    # remove enum values, and the foundation downgrade drops the whole type.
