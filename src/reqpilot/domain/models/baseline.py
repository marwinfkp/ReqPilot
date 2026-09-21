"""Baseline tables (architecture G.7, H.4).

Both tables are append-only: a baseline is frozen at creation and never edited.
Correcting one means creating a new baseline, which is what keeps "what did we
approve, and when" answerable.

The invariant these tables exist to protect:

    **No unapproved requirement version may enter a baseline** (``FR-HIL-004``).

The architecture asks for it at three levels. Two of them live here - the
foreign key to ``requirement_version`` and, on PostgreSQL, a trigger asserting
the member's state at insert time. The third is the baseline service, which
refuses to assemble the rows in the first place.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from reqpilot.domain.models.base import Base, created_at_column, uuid_pk


class Baseline(Base):
    """A frozen, approved set of requirement versions."""

    __tablename__ = "baseline"
    __table_args__ = (
        UniqueConstraint("project_id", "label", name="project_label"),
        Index("ix_baseline_project", "project_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(200), nullable=False)

    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    #: The decision that authorised this baseline. Not nullable: a baseline that
    #: cannot name the approval behind it is exactly what the invariant forbids.
    approval_decision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("approval_decision.id", ondelete="RESTRICT"), nullable=False
    )

    frozen_at: Mapped[dt.datetime] = created_at_column()

    members: Mapped[list[BaselineMember]] = relationship(
        back_populates="baseline", order_by="BaselineMember.created_at"
    )

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"Baseline(id={self.id}, label={self.label!r}, project={self.project_id})"


class BaselineMember(Base):
    """One requirement version inside one baseline. Append-only."""

    __tablename__ = "baseline_member"
    __table_args__ = (
        UniqueConstraint("baseline_id", "requirement_version_id", name="baseline_version"),
        Index("ix_baseline_member_baseline", "baseline_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    baseline_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("baseline.id", ondelete="CASCADE"), nullable=False
    )
    requirement_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("requirement_version.id", ondelete="RESTRICT"), nullable=False
    )
    #: Denormalised so a cross-project member is refusable without a join, and
    #: so the database-level check has everything it needs locally.
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The hash the version carried when it entered. Lets an auditor confirm the
    #: baselined content later without trusting the version row to be unchanged.
    version_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[dt.datetime] = created_at_column()

    baseline: Mapped[Baseline] = relationship(back_populates="members")
