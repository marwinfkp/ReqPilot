"""Requirement and requirement-version tables (architecture G.4).

The shape that matters:

* ``requirement`` holds **identity and pointers only** - no lifecycle state.
* ``requirement_version`` holds the content and the lifecycle state, and is
  **immutable once created**.

That split is `[DESIGN] D13` and it is load-bearing for G7: an approved version
stays approved and baselined while a successor is being worked on.

Immutability is enforced in three places, following the audit-log precedent:
the service layer never mutates content, the ORM raises on a content attribute
being reassigned after flush, and the state column moves only through validated
lifecycle transitions.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from reqpilot.domain.enums import RequirementCategory, RequirementPriority
from reqpilot.domain.lifecycle.states import RequirementState
from reqpilot.domain.models.audit import JsonType
from reqpilot.domain.models.base import Base, created_at_column, uuid_pk


class Requirement(Base):
    """Identity and version pointers for one requirement.

    Deliberately carries no ``state`` column. Asking "what state is this
    requirement in?" is ambiguous once a successor exists, which is exactly the
    ambiguity `[DESIGN] D13` removes.
    """

    __tablename__ = "requirement"
    __table_args__ = (
        # A human id is unique within its project, not globally: two projects
        # may each legitimately have an FR-LOAN-001.
        UniqueConstraint("project_id", "human_id", name="project_human_id"),
        Index("ix_requirement_project_human", "project_id", "human_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The approved convention, e.g. ``FR-LOAN-014`` (``FR-EXT-004``).
    human_id: Mapped[str] = mapped_column(String(64), nullable=False)

    #: The newest version, whatever its state.
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    #: The version that is in the project's baseline, if any. Unchanged by an
    #: edit: that is the point of D13.
    baselined_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()

    versions: Mapped[list[RequirementVersion]] = relationship(
        back_populates="requirement",
        cascade="all, delete-orphan",
        order_by="RequirementVersion.version_no",
        foreign_keys="RequirementVersion.requirement_id",
    )


class RequirementVersion(Base):
    """One immutable version of a requirement.

    Every field except ``state``, ``superseded_by_id`` and the pointer columns
    on the parent is fixed at creation. An edit creates a new row; it never
    rewrites this one.
    """

    __tablename__ = "requirement_version"
    __table_args__ = (
        UniqueConstraint("requirement_id", "version_no", name="requirement_version_no"),
        # P5: lets conflicts and findings reference a version *in their project*
        # with a composite foreign key (cross-project references are impossible).
        UniqueConstraint("id", "project_id", name="id_project"),
        CheckConstraint("version_no >= 1", name="version_no_positive"),
        Index("ix_requirement_version_state", "project_id", "state"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("requirement.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Denormalised from the parent so that every scoped query and every
    #: database-level invariant can reach the project without a join.
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )

    version_no: Mapped[int] = mapped_column(Integer, nullable=False)

    state: Mapped[RequirementState] = mapped_column(
        SAEnum(RequirementState, name="requirement_state_enum"),
        nullable=False,
        default=RequirementState.CANDIDATE,
    )

    # --- governed content: immutable once created ------------------------
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    #: The stakeholder's words before normalisation. Retained because
    #: traceability is to what was *said*, not only to what was written down.
    original_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[RequirementCategory | None] = mapped_column(
        SAEnum(RequirementCategory, name="requirement_category_enum"), nullable=True
    )
    priority: Mapped[RequirementPriority | None] = mapped_column(
        SAEnum(RequirementPriority, name="requirement_priority_enum"), nullable=True
    )
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    dependencies: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    assumptions: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    #: Provenance. At least one is required to leave CANDIDATE (``FR-EXT-007``).
    source_refs: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)

    #: The exact-version binding an approval decision must match (architecture
    #: G.7). Computed from the governed content at creation.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    #: Heuristic review-prioritisation signal, **not** a calibrated probability
    #: (approved Phase 0 H.1). Null in P1: nothing produces one yet.
    review_signal: Mapped[float | None] = mapped_column(nullable=True)

    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()

    #: Set when a successor is approved. The only pointer a version gains after
    #: creation, and it records history rather than changing content.
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("requirement_version.id", ondelete="SET NULL"), nullable=True
    )

    requirement: Mapped[Requirement] = relationship(
        back_populates="versions", foreign_keys=[requirement_id]
    )

    #: Attributes that must never change after the row exists. Enforced by the
    #: service layer and asserted by tests; the list is here so there is one
    #: place to look.
    IMMUTABLE_FIELDS: frozenset[str] = frozenset(
        {
            "id",
            "requirement_id",
            "project_id",
            "version_no",
            "statement",
            "original_text",
            "category",
            "priority",
            "justification",
            "dependencies",
            "assumptions",
            "source_refs",
            "content_hash",
            "created_by",
            "created_at",
        }
    )

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"RequirementVersion(id={self.id}, v{self.version_no}, "
            f"state={self.state}, requirement={self.requirement_id})"
        )
