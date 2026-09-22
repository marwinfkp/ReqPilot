"""Conflicts and the project glossary (roadmap phase P5; architecture G.4, G.5, H.2).

* ``conflict`` - two requirement versions of one project that cannot, or may not,
  both hold (``FR-CNF-001``..``003``). **A guard, not a state** (``[DESIGN]
  D12``): an open conflict blocks ``ANALYZED -> VALIDATED`` and ``VALIDATED ->
  PENDING_APPROVAL`` for both versions; no requirement ever enters a
  "conflicted" state. Its content - which versions, what kind, the rationale
  and the evidence from each side - is immutable. Only a human moves its status:
  under review, then resolved (the G4 decision) or dismissed, once.
* ``glossary_term`` - a term the project has defined (G.5). Undefined-term
  detection (``FR-QAL-005``) checks requirement text against it.

Both versions of a conflict must be in the conflict's project: composite foreign
keys make a cross-project conflict impossible to store, whatever wrote it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, ClassVar

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
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

from reqpilot.domain.enums import (
    ConflictClass,
    ConflictKind,
    ConflictResolution,
    ConflictStatus,
    FindingDetector,
    FindingSeverity,
)
from reqpilot.domain.errors import ImmutableRecordError
from reqpilot.domain.models.base import Base, created_at_column, utc_now, uuid_pk


def _updated_at_column() -> Any:
    return mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class Conflict(Base):
    """A detected contradiction between two requirement versions (G.4, E #6)."""

    __tablename__ = "conflict"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_a_id", "project_id"],
            ["requirement_version.id", "requirement_version.project_id"],
            name="fk_conflict_version_a_same_project",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["version_b_id", "project_id"],
            ["requirement_version.id", "requirement_version.project_id"],
            name="fk_conflict_version_b_same_project",
            ondelete="CASCADE",
        ),
        CheckConstraint("version_a_id <> version_b_id", name="two_versions"),
        CheckConstraint("length(rationale) >= 1", name="rationale_not_empty"),
        CheckConstraint(
            "status <> 'RESOLVED' OR (resolution IS NOT NULL "
            "AND length(coalesce(resolution_reason, '')) >= 1)",
            name="resolved_has_decision",
        ),
        CheckConstraint(
            "status <> 'DISMISSED' OR length(coalesce(resolution_reason, '')) >= 1",
            name="dismissed_has_reason",
        ),
        # At most one active conflict per version pair; the pair is stored in a
        # canonical order by the service.
        Index(
            "uq_conflict_active_pair",
            "version_a_id",
            "version_b_id",
            unique=True,
            postgresql_where=text("status IN ('OPEN', 'UNDER_REVIEW')"),
            sqlite_where=text("status IN ('OPEN', 'UNDER_REVIEW')"),
        ),
        Index("ix_conflict_project_status", "project_id", "status"),
    )

    #: Written once, when the conflict is detected.
    CONTENT_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "id",
            "project_id",
            "version_a_id",
            "version_b_id",
            "conflict_class",
            "kind",
            "rationale",
            "evidence_a",
            "evidence_b",
            "severity",
            "review_signal",
            "involves_stakeholder_disagreement",
            "stakeholder_a",
            "stakeholder_b",
            "detected_by",
            "rule_id",
            "graph_run_id",
            "agent_run_id",
            "recorded_by",
            "created_at",
        }
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    version_a_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    version_b_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    conflict_class: Mapped[ConflictClass] = mapped_column(
        SAEnum(ConflictClass, name="conflict_class_enum"), nullable=False
    )
    kind: Mapped[ConflictKind] = mapped_column(
        SAEnum(ConflictKind, name="conflict_kind_enum"), nullable=False
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    #: Words of version A's statement, and of version B's, that disagree.
    evidence_a: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_b: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[FindingSeverity] = mapped_column(
        SAEnum(FindingSeverity, name="finding_severity_enum"), nullable=False
    )
    #: Heuristic review-prioritisation signal in [0, 1] - not a probability.
    review_signal: Mapped[float | None] = mapped_column(nullable=True)
    #: Both sides trace to different stakeholders (``FR-CNF-002``; G4 trigger).
    #: Derived deterministically from the versions' sources, never from a model.
    involves_stakeholder_disagreement: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    stakeholder_a: Mapped[str | None] = mapped_column(String(300), nullable=True)
    stakeholder_b: Mapped[str | None] = mapped_column(String(300), nullable=True)
    detected_by: Mapped[FindingDetector] = mapped_column(
        SAEnum(FindingDetector, name="finding_detector_enum"), nullable=False
    )
    #: The deterministic rule, or the prompt, that produced the conflict.
    rule_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    graph_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("graph_run.id", ondelete="SET NULL"), nullable=True
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_run.id", ondelete="SET NULL"), nullable=True
    )
    recorded_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # --- human review and resolution (G4) ---------------------------------------
    status: Mapped[ConflictStatus] = mapped_column(
        SAEnum(ConflictStatus, name="conflict_status_enum"), nullable=False
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution: Mapped[ConflictResolution | None] = mapped_column(
        SAEnum(ConflictResolution, name="conflict_resolution_enum"), nullable=True
    )
    resolution_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[dt.datetime] = _updated_at_column()


class GlossaryTerm(Base):
    """A term the project has defined (architecture G.5; ``FR-QAL-005``)."""

    __tablename__ = "glossary_term"
    __table_args__ = (
        UniqueConstraint("project_id", "term_key", name="project_term"),
        CheckConstraint("length(term) >= 1", name="term_not_empty"),
        CheckConstraint("length(definition) >= 1", name="definition_not_empty"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    term: Mapped[str] = mapped_column(String(200), nullable=False)
    #: The term, case-folded and space-normalised: what uniqueness and matching use.
    term_key: Mapped[str] = mapped_column(String(200), nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


# ---------------------------------------------------------------------------
# Immutability, enforced in the ORM (and again by the database's triggers)
# ---------------------------------------------------------------------------

_ALLOWED_MOVES: dict[ConflictStatus, frozenset[ConflictStatus]] = {
    ConflictStatus.OPEN: frozenset(
        {ConflictStatus.UNDER_REVIEW, ConflictStatus.RESOLVED, ConflictStatus.DISMISSED}
    ),
    ConflictStatus.UNDER_REVIEW: frozenset({ConflictStatus.RESOLVED, ConflictStatus.DISMISSED}),
    ConflictStatus.RESOLVED: frozenset(),
    ConflictStatus.DISMISSED: frozenset(),
}


def _changed(instance: Base) -> set[str]:
    state = inspect(instance)
    return {a.key for a in state.mapper.column_attrs if state.attrs[a.key].history.deleted}


@event.listens_for(Session, "before_flush")
def _guard_quality_immutability(session: Session, _context: object, _instances: object) -> None:
    """Refuse a flush that rewrites a conflict or the glossary.

    Registered on the ``Session`` class, so no session can skip it. A project
    deletion removes these rows through the database's cascade, not here.
    """
    for instance in session.deleted:
        if isinstance(instance, (Conflict, GlossaryTerm)):
            raise ImmutableRecordError(f"{type(instance).__name__} rows are never deleted")

    for instance in session.dirty:
        changed = _changed(instance)
        if not changed:
            continue
        if isinstance(instance, GlossaryTerm):
            raise ImmutableRecordError("a glossary term is added, never rewritten")
        if isinstance(instance, Conflict):
            if changed & Conflict.CONTENT_FIELDS:
                raise ImmutableRecordError("a conflict's versions and evidence are immutable")
            if "status" in changed:
                deleted = inspect(instance).attrs["status"].history.deleted
                previous = ConflictStatus(next(iter(deleted)))
                if ConflictStatus(instance.status) not in _ALLOWED_MOVES[previous]:
                    raise ImmutableRecordError(
                        f"a conflict cannot move from {previous} to {instance.status}"
                    )
            elif changed - {"updated_at"}:
                raise ImmutableRecordError("a conflict's review fields change with its status")
