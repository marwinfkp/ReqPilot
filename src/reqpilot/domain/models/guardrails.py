"""Guardrails tables (roadmap phase P11; architecture ADR-009, J.2, P).

* ``auth_session`` - the opaque server-side session ADR-009 selected. The token
  itself is never stored: only its SHA-256, so a copy of the table cannot be
  replayed as a login. Expiry and revocation are columns, which is what makes
  revocation immediate (the reason ADR-009 chose server-side sessions over JWT).
  Not project-scoped: a session identifies a user; the user's roles, read from
  ``project_member`` on every request, decide what the session may reach.
* ``masking_map_entry`` - the unmasking map of ``FR-ING-003`` / J.2: which
  replacement token stands for which original value, per masked source. Stored
  separately and project-scoped; nothing reads it into a prompt, a log, an
  audit payload or an API response; deleted with the project.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from reqpilot.domain.models.base import Base, created_at_column, uuid_pk


class AuthSession(Base):
    """One server-side login session (ADR-009). Identified by the hash of its token."""

    __tablename__ = "auth_session"
    __table_args__ = (Index("ix_auth_session_user", "user_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    #: SHA-256 of the opaque token. The token is shown once, to its holder.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[dt.datetime] = created_at_column()
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MaskingMapEntry(Base):
    """One entry of a masked source's unmasking map (``FR-ING-003``; J.2)."""

    __tablename__ = "masking_map_entry"
    __table_args__ = (
        UniqueConstraint("project_id", "source_type", "source_id", "token", name="masking_token"),
        Index("ix_masking_map_entry_source", "project_id", "source_type", "source_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    #: ``source_document`` or ``utterance``.
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    token: Mapped[str] = mapped_column(String(64), nullable=False)
    #: A :class:`~reqpilot.domain.enums.MaskCategory` value.
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The original value. Never selected into a prompt, a log or a response.
    value: Mapped[str] = mapped_column(Text, nullable=False)
    masker_id: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()

    def __repr__(self) -> str:  # never print the value
        return f"MaskingMapEntry(token={self.token!r}, category={self.category!r})"


class ProjectPurge(Base):
    """The record of one project deletion (``FR-ADM-006``; architecture P.2).

    Append-only, at most one per project. On PostgreSQL inserting it is what
    purges the project's content (the ``project_purge_cascade`` trigger of
    migration 0013); elsewhere the deletion service performs the same deletes.
    """

    __tablename__ = "project_purge"
    __table_args__ = (UniqueConstraint("project_id", name="project_purge_once"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("project.id"), nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    requested_at: Mapped[dt.datetime] = created_at_column()
