"""P11 guardrails hardening: sessions, the unmasking map, injection tags, project deletion.

Additive only. No earlier migration is touched and no earlier trigger is
redefined.

What carries architectural weight:

* ``auth_session`` (ADR-009): opaque server-side sessions, stored as the SHA-256
  of the token, with expiry and revocation columns.
* ``masking_map_entry`` (``FR-ING-003``; J.2): the unmasking map, stored
  separately and project-scoped.
* ``utterance.masking_status`` / ``masker_id`` and ``injection_signals`` on
  ``utterance`` and ``source_chunk``: whether stored interview text passed the
  protective masker, and the Q.4 heuristic tags. Existing rows keep
  ``not_masked`` and no tags - which is what they are.
* **Project deletion** (``FR-ADM-006``; architecture P.2). ``project`` gains a
  tombstone (``deleted_at``, ``deleted_by``): the row stays because its audit
  trail stays (``audit_event`` is append-only and hash-chained per project,
  ADR-010, and its ``project_id`` is part of every row hash). A tombstoned project
  can never be changed again (``project_tombstone_guard``).

  Content is purged through ``project_purge``, an append-only record of each
  deletion whose ``AFTER INSERT`` trigger deletes the project's content rows.
  Every earlier guard already lets a delete through when it runs nested inside
  another trigger (``pg_trigger_depth() > 1`` - the "project deletion cascade"
  those phases anticipated), so no earlier trigger is redefined. The purge
  refuses unless the project is already tombstoned, touches rows of that one
  project only, and never touches ``audit_event``, ``project_member`` or the
  shared knowledge base. The application verifies afterwards that nothing of
  the project's content remains, in the same transaction.
* **The approval path's records** (architecture J.1, M.2): ``approval_decision``
  becomes append-only in the database, as it already was by convention; an
  ``approval_task``'s identity (project, gate, group, subject, role, blocking)
  never changes, a decided task never changes again, and a task becomes
  ``APPROVED`` only if an ``APPROVE`` decision for it exists (``REJECTED`` only
  with a ``REJECT``/``MODIFY`` one) - so a status written by any other path
  cannot make a gate pass. A decision is recorded only against an ``OPEN`` task of
  its own project, in the task's role, by a member holding that role. All of
  these still give way to the project purge (nested).

Revision ID: 0013_p11_guardrails_hardening
Revises: 0012_p10_workflow_generation
Create Date: P11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_p11_guardrails_hardening"
down_revision: str | None = "0012_p10_workflow_generation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

P11_AUDIT_EVENT_TYPES = ("INJECTION_SUSPECTED", "AUTH_SESSION_ISSUED", "AUTH_SESSION_REVOKED")

#: The project's content tables, children before parents (the reverse of the
#: schema's dependency order at this revision). ``audit_event``,
#: ``project_member`` and ``project`` are deliberately absent. ``agent_run`` has
#: no ``project_id`` and goes with ``graph_run`` (``ON DELETE CASCADE``).
PURGE_ORDER = (
    "workflow_gate",
    "workflow_activity",
    "workflow_source",
    "workflow_phase",
    "workflow_change",
    "workflow",
    "security_privacy_finding_evidence",
    "sdlc_rule_application",
    "sdlc_factor",
    "sdlc_candidate",
    "risk_evidence",
    "compliance_mapping_evidence",
    "artifact_section",
    "security_privacy_finding",
    "sdlc_run",
    "risk_mitigation",
    "evidence",
    "compliance_gap",
    "clarification",
    "baseline_member",
    "artifact_version",
    "utterance",
    "traceability_link",
    "risk",
    "review_item",
    "requirement_classification",
    "quality_finding",
    "extraction_candidate",
    "conflict",
    "compliance_mapping",
    "baseline",
    "acceptance_criterion",
    "source_chunk",
    "requirement_version",
    "interview_session",
    "approval_decision",
    "stakeholder",
    "source_document",
    "source_allowlist",
    "requirement",
    "masking_map_entry",
    "graph_run",
    "glossary_term",
    "artifact",
    "approval_task",
)


def _purge_sql() -> str:
    # Table names come only from the fixed PURGE_ORDER constant above - no input.
    deletes = "\n".join(
        f"    DELETE FROM {table} WHERE project_id = NEW.project_id;"  # noqa: S608
        for table in PURGE_ORDER
    )
    return _PURGE_TEMPLATE.replace("{deletes}", deletes)


_PURGE_TEMPLATE = """
CREATE OR REPLACE FUNCTION reqpilot_purge_project()
RETURNS trigger AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM project WHERE id = NEW.project_id AND deleted_at IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            'project % is not marked deleted; nothing was purged (FR-ADM-006)', NEW.project_id;
    END IF;
{deletes}
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER project_purge_cascade
    AFTER INSERT ON project_purge
    FOR EACH ROW EXECUTE FUNCTION reqpilot_purge_project();

-- The purge record is itself append-only (the P3 provenance function raises always).
CREATE TRIGGER project_purge_append_only
    BEFORE UPDATE OR DELETE ON project_purge
    FOR EACH ROW EXECUTE FUNCTION reqpilot_provenance_append_only();

-- A tombstone is final: once deleted_at is set the project row never changes again.
CREATE OR REPLACE FUNCTION reqpilot_project_tombstone_guard()
RETURNS trigger AS $$
BEGIN
    IF OLD.deleted_at IS NOT NULL THEN
        RAISE EXCEPTION 'project % was deleted; its tombstone is final (FR-ADM-006)', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER project_tombstone_guard
    BEFORE UPDATE ON project
    FOR EACH ROW EXECUTE FUNCTION reqpilot_project_tombstone_guard();

-- The approval path's records (architecture J.1, M.2).
CREATE TRIGGER approval_decision_append_only
    BEFORE UPDATE OR DELETE ON approval_decision
    FOR EACH ROW EXECUTE FUNCTION reqpilot_project_content_append_only();

CREATE OR REPLACE FUNCTION reqpilot_approval_task_guard()
RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'approval_task rows are never deleted directly (architecture M.2)';
    END IF;
    IF NEW.project_id <> OLD.project_id OR NEW.gate <> OLD.gate
       OR NEW.task_group_id IS DISTINCT FROM OLD.task_group_id
       OR NEW.subject_type <> OLD.subject_type OR NEW.subject_id <> OLD.subject_id
       OR NEW.required_role <> OLD.required_role OR NEW.blocking <> OLD.blocking THEN
        RAISE EXCEPTION 'an approval task''s identity never changes (architecture M.2)';
    END IF;
    IF NEW.status <> OLD.status THEN
        IF OLD.status <> 'OPEN' THEN
            RAISE EXCEPTION 'a % approval task cannot change again', OLD.status;
        END IF;
        IF NEW.status = 'APPROVED' AND NOT EXISTS (
            SELECT 1 FROM approval_decision d WHERE d.task_id = NEW.id AND d.decision = 'APPROVE'
        ) THEN
            RAISE EXCEPTION 'an approval task becomes APPROVED only with an APPROVE decision';
        END IF;
        IF NEW.status = 'REJECTED' AND NOT EXISTS (
            SELECT 1 FROM approval_decision d
            WHERE d.task_id = NEW.id AND d.decision IN ('REJECT', 'MODIFY')
        ) THEN
            RAISE EXCEPTION 'an approval task becomes REJECTED only with a rejecting decision';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER approval_task_guard
    BEFORE UPDATE OR DELETE ON approval_task
    FOR EACH ROW EXECUTE FUNCTION reqpilot_approval_task_guard();

-- A decision is recorded only against an OPEN task of its own project, in the task's
-- own role, by a member of the project who holds that role.
CREATE OR REPLACE FUNCTION reqpilot_approval_decision_guard()
RETURNS trigger AS $$
DECLARE
    t approval_task%ROWTYPE;
BEGIN
    SELECT * INTO t FROM approval_task WHERE id = NEW.task_id;
    IF NOT FOUND OR t.project_id <> NEW.project_id THEN
        RAISE EXCEPTION 'a decision names an approval task of its own project';
    END IF;
    IF t.status <> 'OPEN' THEN
        RAISE EXCEPTION 'a % approval task takes no further decision', t.status;
    END IF;
    IF NEW.role_exercised <> t.required_role THEN
        RAISE EXCEPTION 'a decision is recorded only in the task''s own role';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM project_member m
        WHERE m.project_id = NEW.project_id AND m.user_id = NEW.decided_by
          AND m.role = NEW.role_exercised
    ) THEN
        RAISE EXCEPTION 'a decision is recorded only for a member holding the role exercised';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER approval_decision_guard
    BEFORE INSERT ON approval_decision
    FOR EACH ROW EXECUTE FUNCTION reqpilot_approval_decision_guard();
"""


PURGE_DOWN_SQL = """
DROP TRIGGER IF EXISTS approval_decision_guard ON approval_decision;
DROP FUNCTION IF EXISTS reqpilot_approval_decision_guard();
DROP TRIGGER IF EXISTS approval_task_guard ON approval_task;
DROP FUNCTION IF EXISTS reqpilot_approval_task_guard();
DROP TRIGGER IF EXISTS approval_decision_append_only ON approval_decision;
DROP TRIGGER IF EXISTS project_tombstone_guard ON project;
DROP FUNCTION IF EXISTS reqpilot_project_tombstone_guard();
DROP TRIGGER IF EXISTS project_purge_append_only ON project_purge;
DROP TRIGGER IF EXISTS project_purge_cascade ON project_purge;
DROP FUNCTION IF EXISTS reqpilot_purge_project();
"""


def _json(is_postgres: bool) -> sa.types.TypeEngine:  # type: ignore[type-arg]
    return postgresql.JSONB() if is_postgres else sa.JSON()


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        with op.get_context().autocommit_block():
            for value in P11_AUDIT_EVENT_TYPES:
                op.execute(f"ALTER TYPE audit_event_type_enum ADD VALUE IF NOT EXISTS '{value}'")

    # -- sessions (ADR-009) ----------------------------------------------------------
    op.create_table(
        "auth_session",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_auth_session_user", "auth_session", ["user_id"])

    # -- the unmasking map (FR-ING-003; J.2) ------------------------------------------
    op.create_table(
        "masking_map_entry",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("project.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("masker_id", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id", "source_type", "source_id", "token", name="masking_token"
        ),
    )
    op.create_index(
        "ix_masking_map_entry_source",
        "masking_map_entry",
        ["project_id", "source_type", "source_id"],
    )

    # -- masking and injection tags on stored project text ------------------------------
    masking_enum: sa.types.TypeEngine = (  # type: ignore[type-arg]
        postgresql.ENUM("NOT_MASKED", "MASKED", name="masking_status_enum", create_type=False)
        if is_postgres
        else sa.Enum("NOT_MASKED", "MASKED", name="masking_status_enum")
    )
    with op.batch_alter_table("utterance") as batch:
        batch.add_column(
            sa.Column("masking_status", masking_enum, nullable=False, server_default="NOT_MASKED")
        )
        batch.add_column(
            sa.Column("masker_id", sa.String(100), nullable=False, server_default="none")
        )
        batch.add_column(
            sa.Column("injection_signals", _json(is_postgres), nullable=False, server_default="[]")
        )
    with op.batch_alter_table("source_chunk") as batch:
        batch.add_column(
            sa.Column("injection_signals", _json(is_postgres), nullable=False, server_default="[]")
        )

    # -- project deletion (FR-ADM-006; P.2) --------------------------------------------
    with op.batch_alter_table("project") as batch:
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("deleted_by", sa.Uuid(), nullable=True))
    op.create_table(
        "project_purge",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("project.id"), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", name="project_purge_once"),
    )

    if is_postgres:
        op.execute(_purge_sql())


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    # Refuse rather than resurrect: dropping the tombstone would turn a deleted
    # project back into an apparently live, empty one.
    if bind.execute(sa.text("SELECT count(*) FROM project WHERE deleted_at IS NOT NULL")).scalar():
        raise RuntimeError(
            "cannot downgrade below P11 while deleted projects exist: their tombstones would "
            "be lost and they would reappear as live projects"
        )
    if is_postgres:
        op.execute(PURGE_DOWN_SQL)
    op.drop_table("project_purge")
    with op.batch_alter_table("project") as batch:
        batch.drop_column("deleted_by")
        batch.drop_column("deleted_at")
    with op.batch_alter_table("source_chunk") as batch:
        batch.drop_column("injection_signals")
    with op.batch_alter_table("utterance") as batch:
        batch.drop_column("injection_signals")
        batch.drop_column("masker_id")
        batch.drop_column("masking_status")
    op.drop_index("ix_masking_map_entry_source", table_name="masking_map_entry")
    op.drop_table("masking_map_entry")
    op.drop_index("ix_auth_session_user", table_name="auth_session")
    op.drop_table("auth_session")
    # The P11 audit event values stay on their enum (PostgreSQL cannot drop enum
    # values); audit history is kept.
