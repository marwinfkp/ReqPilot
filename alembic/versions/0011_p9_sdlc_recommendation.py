"""P9 SDLC recommendation: runs, factors, candidates, rule applications.

Additive only. No earlier migration is touched; no existing column changes.

What carries architectural weight:

* ``sdlc_run`` / ``sdlc_factor`` / ``sdlc_candidate`` / ``sdlc_rule_application``
  (architecture G.8): a recommendation names the exact approved baseline it was
  computed from (a composite foreign key, so only a baseline *of its own
  project*), pins the ruleset and weights versions and the ruleset's content
  hash, and keeps the whole factor profile, every rule effect and every
  candidate's score and rank. Factors, candidates and rule applications are
  **append-only**; a run's inputs and ranking never change after insertion, its
  explanation and selection are written once, and only its lifecycle status
  moves (PostgreSQL triggers below; the ORM guard in ``domain/models/sdlc.py``).
* ``sdlc_factor`` checks the 1-5 scale (``[DESIGN] D11``) and that an override
  records its reason, actor, role, time and previous score (``FR-SDL-003``).
* PostgreSQL: the P9 audit event types, and the ``ARCHITECT`` role (a G6
  approver, ``FR-SDL-008``). Neither is removed on downgrade: PostgreSQL cannot
  drop an enum value, and audit history is kept.
* ``traceability_link``'s allowlist ``CHECK`` is widened to the P9 triples
  (architecture N.2 #20-#23 and the P9 additions). Downgrade restores the P8
  allowlist, deleting any P9 edges first.

Revision ID: 0011_p9_sdlc_recommendation
Revises: 0010_p8_trace_documents
Create Date: P9
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_p9_sdlc_recommendation"
down_revision: str | None = "0010_p8_trace_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

P9_AUDIT_EVENT_TYPES = (
    "FACTOR_PROPOSED",
    "FACTOR_OVERRIDDEN",
    "RULES_APPLIED",
    "MCDA_COMPUTED",
    "EXPLANATION_GENERATED",
    "EXPLANATION_DISCREPANCY",
    "SDLC_RUN_SUPERSEDED",
    "SDLC_SELECTION_RECORDED",
    "SDLC_G6_SETTLED",
)
RUN_STATUSES = (
    "RANKED",
    "AWAITING_G6",
    "REVISION_REQUESTED",
    "REJECTED",
    "SELECTED",
    "SUPERSEDED",
)
EXPLANATION_STATUSES = ("NOT_GENERATED", "GENERATED", "DISCREPANCY", "FAILED")
TRACE_CHECK = "ck_traceability_link_allowed_triple"

GUARDS_SQL = """
-- Factors, candidates and rule applications are append-only (the P3 function;
-- the project-deletion cascade still passes at depth > 1).
CREATE TRIGGER sdlc_factor_append_only
    BEFORE UPDATE OR DELETE ON sdlc_factor
    FOR EACH ROW EXECUTE FUNCTION reqpilot_project_content_append_only();
CREATE TRIGGER sdlc_candidate_append_only
    BEFORE UPDATE OR DELETE ON sdlc_candidate
    FOR EACH ROW EXECUTE FUNCTION reqpilot_project_content_append_only();
CREATE TRIGGER sdlc_rule_application_append_only
    BEFORE UPDATE OR DELETE ON sdlc_rule_application
    FOR EACH ROW EXECUTE FUNCTION reqpilot_project_content_append_only();

-- A run's inputs and ranking are fixed at insertion; its explanation, its G6
-- binding and its selection are written once; only its status moves.
CREATE OR REPLACE FUNCTION reqpilot_sdlc_run_guard()
RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'SDLC runs are never deleted; a recommendation''s history is preserved';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.baseline_id IS DISTINCT FROM OLD.baseline_id
       OR NEW.graph_run_id IS DISTINCT FROM OLD.graph_run_id
       OR NEW.supersedes_run_id IS DISTINCT FROM OLD.supersedes_run_id
       OR NEW.ruleset_version IS DISTINCT FROM OLD.ruleset_version
       OR NEW.ruleset_ref IS DISTINCT FROM OLD.ruleset_ref
       OR NEW.rules_sha256 IS DISTINCT FROM OLD.rules_sha256
       OR NEW.weights_version IS DISTINCT FROM OLD.weights_version
       OR NEW.input_fingerprint IS DISTINCT FROM OLD.input_fingerprint
       OR NEW.profile_hash IS DISTINCT FROM OLD.profile_hash
       OR NEW.ranking_hash IS DISTINCT FROM OLD.ranking_hash
       OR NEW.top_candidate IS DISTINCT FROM OLD.top_candidate
       OR NEW.runner_up_candidate IS DISTINCT FROM OLD.runner_up_candidate
       OR NEW.reversal_conditions::text IS DISTINCT FROM OLD.reversal_conditions::text
       OR NEW.facts_summary::text IS DISTINCT FROM OLD.facts_summary::text
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'an SDLC run''s inputs, versions and ranking are immutable';
    END IF;
    IF (OLD.explanation_narrative IS NOT NULL
            AND NEW.explanation_narrative IS DISTINCT FROM OLD.explanation_narrative)
       OR (OLD.recommendation_hash IS NOT NULL
            AND NEW.recommendation_hash IS DISTINCT FROM OLD.recommendation_hash)
       OR (OLD.g6_task_group_id IS NOT NULL
            AND NEW.g6_task_group_id IS DISTINCT FROM OLD.g6_task_group_id)
       OR (OLD.selected_candidate IS NOT NULL
            AND NEW.selected_candidate IS DISTINCT FROM OLD.selected_candidate)
       OR (OLD.selected_at IS NOT NULL AND NEW.selected_at IS DISTINCT FROM OLD.selected_at) THEN
        RAISE EXCEPTION 'an SDLC run''s explanation, G6 binding and selection are written once';
    END IF;
    IF NEW.selected_candidate IS NOT NULL AND NEW.selected_candidate <> OLD.top_candidate THEN
        RAISE EXCEPTION 'only the computed first candidate can be selected; the ranking is not '
            'overridable (architecture L.6)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER sdlc_run_guard
    BEFORE UPDATE OR DELETE ON sdlc_run
    FOR EACH ROW EXECUTE FUNCTION reqpilot_sdlc_run_guard();
"""

GUARDS_DOWN_SQL = """
DROP TRIGGER IF EXISTS sdlc_run_guard ON sdlc_run;
DROP FUNCTION IF EXISTS reqpilot_sdlc_run_guard();
DROP TRIGGER IF EXISTS sdlc_rule_application_append_only ON sdlc_rule_application;
DROP TRIGGER IF EXISTS sdlc_candidate_append_only ON sdlc_candidate;
DROP TRIGGER IF EXISTS sdlc_factor_append_only ON sdlc_factor;
"""


def _trace_check(*, before_p9: bool) -> str:
    """The allowlist, from the one place it is defined (architecture N.1)."""
    from reqpilot.domain.traceability import allowed_triple_check_sql

    return allowed_triple_check_sql(before_p9=before_p9)


def _replace_trace_check(*, before_p9: bool) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint(TRACE_CHECK, "traceability_link", type_="check")
        op.create_check_constraint(
            TRACE_CHECK, "traceability_link", sa.text(_trace_check(before_p9=before_p9))
        )
        return
    # SQLite cannot alter a CHECK in place: rebuild the table (batch mode).
    with op.batch_alter_table("traceability_link", recreate="always") as batch:
        batch.drop_constraint(TRACE_CHECK, type_="check")
        batch.create_check_constraint(TRACE_CHECK, sa.text(_trace_check(before_p9=before_p9)))


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    json_type = postgresql.JSONB() if is_postgres else sa.JSON()

    if is_postgres:
        for value in P9_AUDIT_EVENT_TYPES:
            op.execute(f"ALTER TYPE audit_event_type_enum ADD VALUE IF NOT EXISTS '{value}'")
        op.execute("ALTER TYPE role_enum ADD VALUE IF NOT EXISTS 'ARCHITECT'")
        postgresql.ENUM(*RUN_STATUSES, name="sdlc_run_status_enum").create(bind, checkfirst=True)
        postgresql.ENUM(*EXPLANATION_STATUSES, name="sdlc_explanation_status_enum").create(
            bind, checkfirst=True
        )
        run_status = postgresql.ENUM(*RUN_STATUSES, name="sdlc_run_status_enum", create_type=False)
        explanation_status = postgresql.ENUM(
            *EXPLANATION_STATUSES, name="sdlc_explanation_status_enum", create_type=False
        )
    else:
        run_status = sa.Enum(*RUN_STATUSES, name="sdlc_run_status_enum")
        explanation_status = sa.Enum(*EXPLANATION_STATUSES, name="sdlc_explanation_status_enum")

    op.create_table(
        "sdlc_run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("baseline_id", sa.Uuid(), nullable=False),
        sa.Column("graph_run_id", sa.Uuid(), nullable=True),
        sa.Column("supersedes_run_id", sa.Uuid(), nullable=True),
        sa.Column("status", run_status, nullable=False),
        sa.Column("ruleset_version", sa.String(40), nullable=False),
        sa.Column("ruleset_ref", sa.String(100), nullable=False),
        sa.Column("rules_sha256", sa.String(64), nullable=False),
        sa.Column("weights_version", sa.String(40), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("profile_hash", sa.String(64), nullable=False),
        sa.Column("ranking_hash", sa.String(64), nullable=False),
        sa.Column("top_candidate", sa.String(60), nullable=False),
        sa.Column("runner_up_candidate", sa.String(60), nullable=True),
        sa.Column("reversal_conditions", json_type, nullable=False),
        sa.Column("facts_summary", json_type, nullable=False),
        sa.Column("explanation_status", explanation_status, nullable=False),
        sa.Column("explanation_narrative", sa.Text(), nullable=True),
        sa.Column("explanation_counter_arguments", json_type, nullable=False),
        sa.Column("asserted_top_candidate", sa.String(60), nullable=True),
        sa.Column("asserted_scores", json_type, nullable=False),
        sa.Column("explanation_discrepancies", json_type, nullable=False),
        sa.Column("explanation_attempts", sa.Integer(), nullable=False),
        sa.Column("explanation_model", sa.String(200), nullable=True),
        sa.Column("explanation_prompt", sa.String(100), nullable=True),
        sa.Column("explanation_agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("explained_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recommendation_hash", sa.String(64), nullable=True),
        sa.Column("g6_task_group_id", sa.Uuid(), nullable=True),
        sa.Column("selected_candidate", sa.String(60), nullable=True),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["graph_run_id"], ["graph_run.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("id", "project_id", name="uq_sdlc_run_id"),
        sa.ForeignKeyConstraint(
            ["baseline_id", "project_id"],
            ["baseline.id", "baseline.project_id"],
            name="fk_sdlc_run_baseline",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_run_id", "project_id"],
            ["sdlc_run.id", "sdlc_run.project_id"],
            name="fk_sdlc_run_supersedes",
        ),
        sa.CheckConstraint("length(profile_hash) = 64", name="ck_sdlc_run_profile_hash_present"),
        sa.CheckConstraint("length(ranking_hash) = 64", name="ck_sdlc_run_ranking_hash_present"),
        sa.CheckConstraint(
            "(status <> 'SELECTED') OR "
            "(selected_candidate IS NOT NULL AND selected_at IS NOT NULL)",
            name="ck_sdlc_run_selection_recorded",
        ),
    )
    op.create_index("ix_sdlc_run_project", "sdlc_run", ["project_id", "created_at"])

    op.create_table(
        "sdlc_factor",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sdlc_run_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("factor_id", sa.String(60), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("derived_score", sa.Integer(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence_refs", json_type, nullable=False),
        sa.Column("evidence_state", sa.String(30), nullable=False),
        sa.Column("basis", json_type, nullable=False),
        sa.Column("proposal_status", sa.String(20), nullable=False),
        sa.Column("proposed_score", sa.Integer(), nullable=True),
        sa.Column("proposal_rationale", sa.Text(), nullable=True),
        sa.Column("proposal_evidence_refs", json_type, nullable=False),
        sa.Column("proposal_rejection_reason", sa.String(300), nullable=True),
        sa.Column("is_overridden", sa.Boolean(), nullable=False),
        sa.Column("previous_score", sa.Integer(), nullable=True),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("overridden_by", sa.Uuid(), nullable=True),
        sa.Column("override_role", sa.String(40), nullable=True),
        sa.Column("overridden_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["sdlc_run_id", "project_id"],
            ["sdlc_run.id", "sdlc_run.project_id"],
            name="fk_sdlc_factor_run",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("sdlc_run_id", "factor_id", name="uq_sdlc_factor_sdlc_run_id"),
        sa.UniqueConstraint("id", "project_id", name="uq_sdlc_factor_id"),
        sa.CheckConstraint("score BETWEEN 1 AND 5", name="ck_sdlc_factor_score_on_scale"),
        sa.CheckConstraint(
            "derived_score BETWEEN 1 AND 5", name="ck_sdlc_factor_derived_score_on_scale"
        ),
        sa.CheckConstraint(
            "proposed_score IS NULL OR proposed_score BETWEEN 1 AND 5",
            name="ck_sdlc_factor_proposal_on_scale",
        ),
        sa.CheckConstraint(
            "(NOT is_overridden) OR (override_reason IS NOT NULL AND overridden_by IS NOT NULL "
            "AND override_role IS NOT NULL AND overridden_at IS NOT NULL "
            "AND previous_score IS NOT NULL)",
            name="ck_sdlc_factor_override_recorded",
        ),
    )
    op.create_index("ix_sdlc_factor_run", "sdlc_factor", ["sdlc_run_id"])
    op.create_index("ix_sdlc_factor_project_id", "sdlc_factor", ["project_id"])

    op.create_table(
        "sdlc_candidate",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sdlc_run_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_key", sa.String(60), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("raw_score", sa.Float(), nullable=False),
        sa.Column("max_raw", sa.Float(), nullable=False),
        sa.Column("mcda_score", sa.Float(), nullable=False),
        sa.Column("normalised_score", sa.Float(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("vetoed_by", json_type, nullable=False),
        sa.Column("boosted_by", json_type, nullable=False),
        sa.Column("required_by", json_type, nullable=False),
        sa.Column("contributions", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["sdlc_run_id", "project_id"],
            ["sdlc_run.id", "sdlc_run.project_id"],
            name="fk_sdlc_candidate_run",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("sdlc_run_id", "candidate_key", name="uq_sdlc_candidate_sdlc_run_id"),
        sa.UniqueConstraint("sdlc_run_id", "rank", name="uq_sdlc_candidate_run_rank"),
        sa.UniqueConstraint("id", "project_id", name="uq_sdlc_candidate_id"),
        sa.CheckConstraint("rank >= 1", name="ck_sdlc_candidate_rank_positive"),
        sa.CheckConstraint(
            "normalised_score BETWEEN 0 AND 100", name="ck_sdlc_candidate_score_bounded"
        ),
    )
    op.create_index("ix_sdlc_candidate_run", "sdlc_candidate", ["sdlc_run_id"])
    op.create_index("ix_sdlc_candidate_project_id", "sdlc_candidate", ["project_id"])

    op.create_table(
        "sdlc_rule_application",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sdlc_run_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.String(80), nullable=False),
        sa.Column("effect", sa.String(20), nullable=False),
        sa.Column("affected_candidate", sa.String(60), nullable=True),
        sa.Column("changed_ranking", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("trigger_values", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["sdlc_run_id", "project_id"],
            ["sdlc_run.id", "sdlc_run.project_id"],
            name="fk_sdlc_rule_application_run",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_sdlc_rule_application_run", "sdlc_rule_application", ["sdlc_run_id"])
    op.create_index("ix_sdlc_rule_application_project_id", "sdlc_rule_application", ["project_id"])

    _replace_trace_check(before_p9=False)

    if is_postgres:
        op.execute(GUARDS_SQL)


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        op.execute(GUARDS_DOWN_SQL)
        # P9 trace edges cannot survive the P8 allowlist; they go with the tables
        # they point at. The append-only trigger is lifted for this one statement.
        op.execute("ALTER TABLE traceability_link DISABLE TRIGGER traceability_link_append_only")
    op.execute(
        "DELETE FROM traceability_link WHERE from_type IN ('sdlc_run', 'sdlc_factor', "
        "'sdlc_candidate') OR to_type IN ('sdlc_run', 'sdlc_factor', 'sdlc_candidate')"
    )
    if is_postgres:
        op.execute("ALTER TABLE traceability_link ENABLE TRIGGER traceability_link_append_only")
    _replace_trace_check(before_p9=True)

    op.drop_table("sdlc_rule_application")
    op.drop_table("sdlc_candidate")
    op.drop_table("sdlc_factor")
    op.drop_table("sdlc_run")
    sa.Enum(name="sdlc_explanation_status_enum").drop(bind, checkfirst=True)
    sa.Enum(name="sdlc_run_status_enum").drop(bind, checkfirst=True)
    # The P9 audit event values and the ARCHITECT role value stay on their enums
    # (PostgreSQL cannot drop enum values); audit history is kept.
