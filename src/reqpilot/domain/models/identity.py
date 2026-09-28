"""Identity, project and membership tables (architecture G.2, G.3).

The foundational subset only. ``project_member`` is the physical basis of
project isolation: a role is held *within* a project, never globally.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, event, inspect
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship
from sqlalchemy.types import Uuid

from reqpilot.domain.enums import Role
from reqpilot.domain.errors import ImmutableRecordError
from reqpilot.domain.models.base import Base, created_at_column, uuid_pk


class User(Base):
    """An authenticated person.

    Deliberately minimal: email plus a password hash column. Authentication
    itself is not implemented in P0 - the column exists so that the membership
    and audit foreign keys have a real target.
    """

    __tablename__ = "app_user"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Never a plaintext password. Nullable because P0 creates users without
    # credentials; authentication arrives with the phase that needs logins.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()

    memberships: Mapped[list[ProjectMember]] = relationship(back_populates="user")


class Project(Base):
    """A requirements-engineering engagement. The unit of isolation."""

    __tablename__ = "project"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Lifecycle is a plain string in P0. The project state machine belongs to a
    # later roadmap phase; constraining it now would be speculative.
    lifecycle_state: Mapped[str] = mapped_column(String(50), nullable=False, default="elicitation")
    created_at: Mapped[dt.datetime] = created_at_column()

    # --- knowledge scope (architecture G.3, J.4, J.6) --------------------
    #: Jurisdiction codes this project's retrieval may draw on. Empty means none:
    #: retrieval fails closed rather than searching every jurisdiction.
    jurisdiction_scope: Mapped[list[str]] = mapped_column(
        JSON().with_variant(postgresql.ARRAY(String(100)), "postgresql"),
        nullable=False,
        default=list,
    )
    #: The KB version this project's analysis is pinned to, so a later KB update
    #: does not silently change past analyses (J.6). ``None`` follows the current
    #: KB version.
    kb_version_pin: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- deletion (P11; FR-ADM-006, architecture P.2) --------------------
    #: Set when the project was deleted. The row remains as a tombstone because
    #: its audit trail - append-only and hash-chained per project - remains.
    deleted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, active_history=True
    )
    #: The human who deleted it.
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    members: Mapped[list[ProjectMember]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ProjectMember(Base):
    """A user's role within one project (architecture G.2).

    A user may hold several roles in one project - realistic for a small team -
    which is why the uniqueness constraint covers the role as well. Which role
    was actually exercised is recorded on each decision, so role-appropriate
    approval stays meaningful and auditable.
    """

    __tablename__ = "project_member"
    __table_args__ = (UniqueConstraint("project_id", "user_id", "role", name="project_user_role"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[Role] = mapped_column(SAEnum(Role, name="role_enum"), nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()

    project: Mapped[Project] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


@event.listens_for(Session, "before_flush")
def _guard_project_tombstone(session: Session, _context: object, _instances: object) -> None:
    """A deleted project's row is final (P11; the ``project_tombstone_guard`` trigger).

    Checked here as well as in PostgreSQL so the rule holds on every engine: once
    ``deleted_at`` is set - by the one flush that tombstones the project - no
    later flush may change any column of that row, or delete it.
    """
    for obj in list(session.dirty) + list(session.deleted):
        if not isinstance(obj, Project):
            continue
        with session.no_autoflush:
            obj.deleted_at  # noqa: B018 - loads an expired value before reading its history
        history = inspect(obj).attrs.deleted_at.history
        committed = [*history.deleted, *history.unchanged]
        previous = committed[0] if committed else None
        if previous is not None and (obj in session.deleted or session.is_modified(obj)):
            raise ImmutableRecordError(
                "a deleted project's tombstone is final (FR-ADM-006); it cannot change"
            )
