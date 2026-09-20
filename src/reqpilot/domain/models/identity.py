"""Identity, project and membership tables (architecture G.2, G.3).

The foundational subset only. ``project_member`` is the physical basis of
project isolation: a role is held *within* a project, never globally.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from reqpilot.domain.enums import Role
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
