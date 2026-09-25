"""The typed trace graph: one edge table (architecture N.1; ``FR-TRC-001``, ``-004``).

``traceability_link`` is **append-only**. An edge records a relationship that a
persisted fact established - a requirement version cites an utterance, a risk
was identified for a version, a decision approved it, an artefact section cites
it. History is never rewritten: a successor version gets its *own* edges, and
its predecessor's stay exactly where they were (architecture N.4), so "what did
we know when we approved v1?" remains answerable after v2 exists.

Three layers keep the graph well formed, following the P1 precedent:

1. the trace service refuses an edge outside the closed allowlist
   (:mod:`reqpilot.domain.traceability`) or with an end it cannot resolve in the
   project;
2. a database ``CHECK`` built from the same allowlist refuses any other triple,
   whatever wrote it, and a unique key refuses a duplicate edge;
3. rows are never updated or deleted - the ORM guard below, and a PostgreSQL
   trigger installed by the migration.

Ids are stored as strings because the graph is polymorphic: most ends are UUID
rows, and a checklist control is identified by its checklist key.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.types import Uuid

from reqpilot.domain.errors import ImmutableRecordError
from reqpilot.domain.models.base import Base, created_at_column, uuid_pk
from reqpilot.domain.traceability import allowed_triple_check_sql


class TraceabilityLink(Base):
    """One typed edge of the trace graph. Append-only."""

    __tablename__ = "traceability_link"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "from_type", "from_id", "link_type", "to_type", "to_id", name="edge"
        ),
        # The closed allowlist, at the database (architecture N.1).
        CheckConstraint(allowed_triple_check_sql(), name="allowed_triple"),
        CheckConstraint("length(from_id) >= 1 AND length(to_id) >= 1", name="ends_present"),
        # The anchor version is pinned to the edge's own project.
        ForeignKeyConstraint(
            ["anchor_version_id", "project_id"],
            ["requirement_version.id", "requirement_version.project_id"],
            name="fk_traceability_link_anchor_version",
            ondelete="CASCADE",
        ),
        Index("ix_traceability_link_from", "project_id", "from_type", "from_id"),
        Index("ix_traceability_link_to", "project_id", "to_type", "to_id"),
        Index("ix_traceability_link_anchor", "project_id", "anchor_version_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    from_type: Mapped[str] = mapped_column(String(40), nullable=False)
    from_id: Mapped[str] = mapped_column(String(100), nullable=False)
    link_type: Mapped[str] = mapped_column(String(40), nullable=False)
    to_type: Mapped[str] = mapped_column(String(40), nullable=False)
    to_id: Mapped[str] = mapped_column(String(100), nullable=False)
    #: The exact requirement version this edge belongs to, when it belongs to
    #: one (``FR-TRC-004``). Lets "the graph of v1" be queried as it was.
    anchor_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    #: Which persisted fact the edge was derived from, e.g.
    #: ``requirement_version.source_refs`` or ``approval_decision``. Provenance
    #: of the link itself; never content.
    origin: Mapped[str] = mapped_column(String(80), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"TraceabilityLink({self.from_type}:{self.from_id} -{self.link_type}-> "
            f"{self.to_type}:{self.to_id})"
        )


@event.listens_for(Session, "before_flush")
def _guard_trace_links(session: Session, _context: object, _instances: object) -> None:
    """Refuse any flush that edits or deletes a trace link (``FR-TRC-004``)."""
    for instance in session.deleted:
        if isinstance(instance, TraceabilityLink):
            raise ImmutableRecordError("trace links are never deleted; history is preserved")
    for instance in session.dirty:
        if isinstance(instance, TraceabilityLink):
            state: Any = inspect(instance)
            if any(state.attrs[a.key].history.deleted for a in state.mapper.column_attrs):
                raise ImmutableRecordError("trace links are append-only")
