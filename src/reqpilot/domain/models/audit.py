"""The append-only audit event table (architecture ADR-010, section O).

Immutability is enforced at three levels, per the architecture:

1. **Application** - no update or delete path exists. The ORM mapping below sets
   no mutable attributes and the service offers append and read only.
2. **Database** - the migration ``REVOKE``s ``UPDATE, DELETE`` on this table from
   the application role and installs a trigger that raises on either.
3. **Hash chain** - each row carries ``prev_hash``/``row_hash``, so tampering
   through any other path is detectable rather than merely disallowed.

The payload rule is deliberate and worth respecting when adding event types:
**references only**. Ids, hashes, enum values, counts, decisions - never
requirement text, chunk text, prompts, masked values or secrets. Audit records
must be safe to export to an auditor who is not cleared for project content.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import BigInteger, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, Uuid

from reqpilot.domain.enums import ActorKind, AuditEventType
from reqpilot.domain.models.base import Base, created_at_column, uuid_pk

#: JSONB on PostgreSQL, plain JSON elsewhere, so the model also loads under
#: SQLite for fast offline unit tests of the hashing logic.
JsonType = JSON().with_variant(JSONB(), "postgresql")


class AuditEvent(Base):
    """One immutable record of an action taken by a human, an agent role, or the system."""

    __tablename__ = "audit_event"
    __table_args__ = (
        # Chain order is by `seq`, not by timestamp: wall-clock resolution is
        # coarse enough that several appends can share a timestamp, which would
        # make retrieval order - and therefore chain verification - ambiguous.
        UniqueConstraint("project_id", "seq", name="project_seq"),
        Index("ix_audit_event_project_seq", "project_id", "seq"),
        Index("ix_audit_event_subject", "project_id", "subject_type", "subject_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()

    #: Monotonic position within this project's chain, starting at 1.
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Project scoping. Nullable only for genuinely project-less system events;
    # every project-scoped event must set it, and the hash chain is per-project.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="SET NULL"), nullable=True, index=True
    )

    occurred_at: Mapped[dt.datetime] = created_at_column()

    actor_kind: Mapped[ActorKind] = mapped_column(
        SAEnum(ActorKind, name="actor_kind_enum"), nullable=False
    )
    actor_ref: Mapped[str] = mapped_column(String(200), nullable=False)

    event_type: Mapped[AuditEventType] = mapped_column(
        SAEnum(AuditEventType, name="audit_event_type_enum"), nullable=False
    )

    subject_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    subject_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    graph_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    #: References only - see the module docstring.
    payload: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)

    # Hash chain (architecture ADR-010). prev_hash is None for the first event
    # in a project's chain.
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"AuditEvent(id={self.id}, seq={self.seq}, type={self.event_type}, "
            f"project={self.project_id}, occurred_at={self.occurred_at})"
        )
