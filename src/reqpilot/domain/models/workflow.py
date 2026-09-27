"""Generated project workflows (architecture G.8, M.4, N.2 #24-#26; roadmap phase P10).

Six tables, all project-scoped and pinned to their project by composite foreign keys:

* ``workflow`` - one generated workflow for one G6-selected SDLC run: the run, the
  selected candidate row and the baseline it realises, the template version and
  content hash that shaped it, the input fingerprint that makes generation
  idempotent, the **generated structure** (kept unchanged forever, so the original
  is never lost to an edit), the current revision and content hash, the
  validation's open items, and the status;
* ``workflow_phase`` / ``workflow_activity`` / ``workflow_gate`` - the ordered
  children (architecture G.8: "ordered children with role, deliverables, entry/exit
  criteria, testing requirements, traceability requirements"). Gates here are gates
  of the *generated project's* process, never ReqPilot gates;
* ``workflow_source`` - the provenance of every derived element: which compliance
  mapping made a checkpoint mandatory, which risk and mitigation an activity treats,
  which derived security requirement a security activity comes from, which
  candidate the workflow realises. **Append-only.** The trace edges are built from
  it;
* ``workflow_change`` - the change log of ``FR-WFL-007``: one row per accepted
  edit, with the revision it produced, the actor and the role exercised, the
  element, each changed field's previous and new value, and the reason.
  **Append-only.**

**What can move.** On ``workflow`` only the status (one way, to ``superseded``),
the revision, the current content hash, the open items and ``updated_at``. The
children's wording can change through the service's edit path, which writes a
change-log row for each edit; their keys, kinds, ``mandatory`` flags and parent
phase never change, and a mandatory activity is never deleted. The ORM guard below
and PostgreSQL triggers installed by migration 0012 both enforce this.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.types import Uuid

from reqpilot.domain.enums import WorkflowStatus
from reqpilot.domain.errors import ImmutableRecordError
from reqpilot.domain.models.audit import JsonType
from reqpilot.domain.models.base import Base, created_at_column, utc_now, uuid_pk

ACTIVITY_KINDS_SQL = (
    "kind IN ('template', 'security', 'risk_treatment', 'risk_verification', 'manual')"
)
GATE_KINDS_SQL = "kind IN ('phase_approval', 'compliance_checkpoint', 'production_readiness')"
ORIGINS_SQL = "origin IN ('generated', 'edited', 'manual')"
SOURCE_TYPES_SQL = (
    "source_type IN ('sdlc_candidate', 'compliance_mapping', 'risk', 'risk_mitigation', "
    "'security_privacy_finding')"
)
ELEMENT_TYPES_SQL = "element_type IN ('workflow', 'phase', 'activity', 'gate')"
OPERATIONS_SQL = "operation IN ('update', 'add', 'remove')"
#: The partial-index predicate: at most one live workflow per project.
LIVE_WORKFLOW_SQL = "status <> 'SUPERSEDED'"


def _updated_at_column() -> Any:
    return mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class Workflow(Base):
    """One generated project workflow for one G6-selected SDLC run."""

    __tablename__ = "workflow"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_workflow_id_project"),
        # Idempotency: the same run and the same approved inputs give one workflow.
        UniqueConstraint(
            "project_id", "sdlc_run_id", "input_fingerprint", name="uq_workflow_run_inputs"
        ),
        ForeignKeyConstraint(
            ["sdlc_run_id", "project_id"],
            ["sdlc_run.id", "sdlc_run.project_id"],
            name="fk_workflow_sdlc_run",
        ),
        ForeignKeyConstraint(
            ["selected_candidate_id", "project_id"],
            ["sdlc_candidate.id", "sdlc_candidate.project_id"],
            name="fk_workflow_selected_candidate",
        ),
        ForeignKeyConstraint(
            ["baseline_id", "project_id"],
            ["baseline.id", "baseline.project_id"],
            name="fk_workflow_baseline",
        ),
        ForeignKeyConstraint(
            ["supersedes_workflow_id", "project_id"],
            ["workflow.id", "workflow.project_id"],
            name="fk_workflow_supersedes",
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("length(generated_hash) = 64", name="generated_hash_present"),
        CheckConstraint("length(content_hash) = 64", name="content_hash_present"),
        CheckConstraint("length(input_fingerprint) = 64", name="input_fingerprint_present"),
        Index("ix_workflow_project", "project_id", "created_at"),
        # At most one live (non-superseded) workflow per project.
        Index(
            "uq_workflow_one_live_per_project",
            "project_id",
            unique=True,
            postgresql_where=text(LIVE_WORKFLOW_SQL),
            sqlite_where=text(LIVE_WORKFLOW_SQL),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    sdlc_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    selected_candidate_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    candidate_key: Mapped[str] = mapped_column(String(60), nullable=False)
    baseline_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    graph_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("graph_run.id", ondelete="SET NULL"), nullable=True
    )
    supersedes_workflow_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[WorkflowStatus] = mapped_column(
        SAEnum(WorkflowStatus, name="workflow_status_enum"), nullable=False
    )
    #: ``workflow_templates@<version>#<sha12>`` and the file's full canonical sha256.
    template_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    templates_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    #: sha256 over the run, the candidate, the template and every eligible source row.
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The workflow exactly as generated. Never changes (the original survives edits).
    generated_structure: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    generated_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The current content (after edits): its revision and hash.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The open items the source records leave (never validation errors).
    open_items: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, nullable=False, default=list)
    generated_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[dt.datetime] = _updated_at_column()


class WorkflowPhase(Base):
    __tablename__ = "workflow_phase"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_workflow_phase_id_project"),
        UniqueConstraint("id", "workflow_id", name="uq_workflow_phase_id_workflow"),
        UniqueConstraint("workflow_id", "position", name="uq_workflow_phase_position"),
        UniqueConstraint("workflow_id", "key", name="uq_workflow_phase_key"),
        ForeignKeyConstraint(
            ["workflow_id", "project_id"],
            ["workflow.id", "workflow.project_id"],
            name="fk_workflow_phase_workflow",
            ondelete="CASCADE",
        ),
        CheckConstraint("position >= 1", name="position_positive"),
        CheckConstraint(ORIGINS_SQL, name="origin_known"),
        Index("ix_workflow_phase_workflow", "workflow_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    stages: Mapped[list[str]] = mapped_column(JsonType, nullable=False)
    cycle: Mapped[str | None] = mapped_column(String(200), nullable=True)
    verifies_phase_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    responsible_roles: Mapped[list[str]] = mapped_column(JsonType, nullable=False)
    deliverables: Mapped[list[str]] = mapped_column(JsonType, nullable=False)
    entry_criteria: Mapped[list[str]] = mapped_column(JsonType, nullable=False)
    exit_criteria: Mapped[list[str]] = mapped_column(JsonType, nullable=False)
    testing_requirements: Mapped[list[str]] = mapped_column(JsonType, nullable=False)
    traceability_requirements: Mapped[list[str]] = mapped_column(JsonType, nullable=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[dt.datetime] = _updated_at_column()


class WorkflowActivity(Base):
    __tablename__ = "workflow_activity"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_workflow_activity_id_project"),
        UniqueConstraint("workflow_id", "key", name="uq_workflow_activity_key"),
        UniqueConstraint("phase_id", "position", name="uq_workflow_activity_position"),
        ForeignKeyConstraint(
            ["workflow_id", "project_id"],
            ["workflow.id", "workflow.project_id"],
            name="fk_workflow_activity_workflow",
            ondelete="CASCADE",
        ),
        # The phase belongs to the same workflow.
        ForeignKeyConstraint(
            ["phase_id", "workflow_id"],
            ["workflow_phase.id", "workflow_phase.workflow_id"],
            name="fk_workflow_activity_phase",
            ondelete="CASCADE",
        ),
        CheckConstraint("position >= 1", name="position_positive"),
        CheckConstraint(ACTIVITY_KINDS_SQL, name="kind_known"),
        CheckConstraint(ORIGINS_SQL, name="origin_known"),
        # A project-derived activity is always mandatory.
        CheckConstraint("kind IN ('template', 'manual') OR mandatory", name="derived_mandatory"),
        Index("ix_workflow_activity_workflow", "workflow_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    phase_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    responsible_roles: Mapped[list[str]] = mapped_column(JsonType, nullable=False)
    deliverables: Mapped[list[str]] = mapped_column(JsonType, nullable=False)
    mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[dt.datetime] = _updated_at_column()


class WorkflowGate(Base):
    """A gate of the generated project's process (architecture M.1, M.4). Not a ReqPilot gate."""

    __tablename__ = "workflow_gate"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_workflow_gate_id_project"),
        UniqueConstraint("workflow_id", "key", name="uq_workflow_gate_key"),
        UniqueConstraint("phase_id", "position", name="uq_workflow_gate_position"),
        ForeignKeyConstraint(
            ["workflow_id", "project_id"],
            ["workflow.id", "workflow.project_id"],
            name="fk_workflow_gate_workflow",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["phase_id", "workflow_id"],
            ["workflow_phase.id", "workflow_phase.workflow_id"],
            name="fk_workflow_gate_phase",
            ondelete="CASCADE",
        ),
        CheckConstraint("position >= 1", name="position_positive"),
        CheckConstraint(GATE_KINDS_SQL, name="kind_known"),
        CheckConstraint(ORIGINS_SQL, name="origin_known"),
        CheckConstraint("kind = 'phase_approval' OR mandatory", name="derived_mandatory"),
        Index("ix_workflow_gate_workflow", "workflow_id"),
        # Exactly one production-readiness gate per workflow ([PS §16] category 8);
        # "at most one" here, "at least one" by validation.
        Index(
            "uq_workflow_gate_one_production_readiness",
            "workflow_id",
            unique=True,
            postgresql_where=text("kind = 'production_readiness'"),
            sqlite_where=text("kind = 'production_readiness'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    phase_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    approver_roles: Mapped[list[str]] = mapped_column(JsonType, nullable=False)
    required_evidence: Mapped[list[str]] = mapped_column(JsonType, nullable=False)
    entry_criteria: Mapped[list[str]] = mapped_column(JsonType, nullable=False)
    exit_criteria: Mapped[list[str]] = mapped_column(JsonType, nullable=False)
    mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[dt.datetime] = _updated_at_column()


class WorkflowSource(Base):
    """Provenance of a workflow element (N.2 #24-#26). Append-only."""

    __tablename__ = "workflow_source"
    __table_args__ = (
        UniqueConstraint(
            "workflow_id",
            "element_type",
            "element_id",
            "source_type",
            "source_id",
            "relation",
            name="uq_workflow_source_link",
        ),
        ForeignKeyConstraint(
            ["workflow_id", "project_id"],
            ["workflow.id", "workflow.project_id"],
            name="fk_workflow_source_workflow",
            ondelete="CASCADE",
        ),
        CheckConstraint(ELEMENT_TYPES_SQL, name="element_type_known"),
        CheckConstraint(SOURCE_TYPES_SQL, name="source_type_known"),
        Index("ix_workflow_source_workflow", "workflow_id"),
        Index("ix_workflow_source_source", "project_id", "source_type", "source_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    element_type: Mapped[str] = mapped_column(String(20), nullable=False)
    element_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    relation: Mapped[str] = mapped_column(String(30), nullable=False)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()


class WorkflowChange(Base):
    """One accepted edit of a workflow (``FR-WFL-007``). Append-only."""

    __tablename__ = "workflow_change"
    __table_args__ = (
        UniqueConstraint("workflow_id", "revision", name="uq_workflow_change_revision"),
        ForeignKeyConstraint(
            ["workflow_id", "project_id"],
            ["workflow.id", "workflow.project_id"],
            name="fk_workflow_change_workflow",
            ondelete="CASCADE",
        ),
        CheckConstraint("revision >= 2", name="revision_after_generation"),
        CheckConstraint(OPERATIONS_SQL, name="operation_known"),
        CheckConstraint(ELEMENT_TYPES_SQL, name="element_type_known"),
        CheckConstraint("length(trim(reason)) >= 1", name="reason_recorded"),
        Index("ix_workflow_change_workflow", "workflow_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: The revision this edit produced (the generated workflow is revision 1).
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    operation: Mapped[str] = mapped_column(String(20), nullable=False)
    element_type: Mapped[str] = mapped_column(String(20), nullable=False)
    element_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    element_key: Mapped[str] = mapped_column(String(200), nullable=False)
    #: ``{field: {"before": ..., "after": ...}}``
    changes: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    role_exercised: Mapped[str] = mapped_column(String(40), nullable=False)
    content_hash_before: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash_after: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


#: ``workflow`` columns that may move after insertion, and nothing else.
WORKFLOW_MUTABLE_COLUMNS: frozenset[str] = frozenset(
    {"status", "revision", "content_hash", "open_items", "updated_at"}
)
#: Child columns an edit may change (the service writes a change-log row for each).
PHASE_MUTABLE_COLUMNS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "responsible_roles",
        "deliverables",
        "entry_criteria",
        "exit_criteria",
        "testing_requirements",
        "traceability_requirements",
        "origin",
        "updated_at",
    }
)
ACTIVITY_MUTABLE_COLUMNS: frozenset[str] = frozenset(
    {"name", "description", "responsible_roles", "deliverables", "origin", "updated_at"}
)
GATE_MUTABLE_COLUMNS: frozenset[str] = frozenset(
    {
        "name",
        "purpose",
        "approver_roles",
        "required_evidence",
        "entry_criteria",
        "exit_criteria",
        "origin",
        "updated_at",
    }
)


def _changed(instance: object) -> set[str]:
    """Every column this flush writes.

    ``has_changes()`` rather than only a removed previous value: an attribute
    assigned after the instance was expired (a savepoint rollback, a commit) has
    no loaded previous value, and must still count as a write to a fixed column.
    """
    state: Any = inspect(instance)
    return {a.key for a in state.mapper.column_attrs if state.attrs[a.key].history.has_changes()}


@event.listens_for(Session, "before_flush")
def _guard_workflow(session: Session, _context: object, _instances: object) -> None:
    """Provenance and the change log are append-only; the generated workflow never changes."""
    for instance in session.deleted:
        if isinstance(instance, (WorkflowSource, WorkflowChange, Workflow, WorkflowPhase)):
            raise ImmutableRecordError(
                f"{type(instance).__name__} rows are never deleted; a workflow's history and "
                "provenance are preserved"
            )
        if isinstance(instance, (WorkflowActivity, WorkflowGate)) and (
            instance.mandatory or not isinstance(instance, WorkflowActivity)
        ):
            raise ImmutableRecordError(
                "a mandatory workflow element, and any workflow gate, is never deleted"
            )
    for instance in session.dirty:
        if isinstance(instance, (WorkflowSource, WorkflowChange)):
            if _changed(instance):
                raise ImmutableRecordError(f"{type(instance).__name__} is append-only")
            continue
        allowed = {
            Workflow: WORKFLOW_MUTABLE_COLUMNS,
            WorkflowPhase: PHASE_MUTABLE_COLUMNS,
            WorkflowActivity: ACTIVITY_MUTABLE_COLUMNS,
            WorkflowGate: GATE_MUTABLE_COLUMNS,
        }.get(type(instance))
        if allowed is None:
            continue
        illegal = _changed(instance) - allowed
        if illegal:
            raise ImmutableRecordError(
                f"{type(instance).__name__} columns {sorted(illegal)} are fixed at generation "
                "(keys, kinds, mandatory flags, ordering, provenance and the generated workflow)"
            )
        if isinstance(instance, Workflow):
            history: Any = inspect(instance).attrs["status"].history
            committed = [*history.deleted, *history.unchanged]
            if WorkflowStatus.SUPERSEDED in committed and _changed(instance):
                raise ImmutableRecordError("a superseded workflow never changes again")
