"""Request-scoped dependencies: the database session and the acting actor.

**Actor resolution here is development-only.** Real authentication - sessions,
password verification, MFA - is explicitly out of scope for this phase and was
never part of the foundation either. What exists is the smallest mechanism that
lets the API and the demonstration UI act *as* a real, project-scoped actor so
that authorization can be enforced for real:

    X-ReqPilot-Actor: <user id>

The header names a ``app_user`` row; the actor's roles are then read from
``project_member``, which is the same source the policy consults everywhere
else. **Nothing is trusted from the header except the identity claim**, and the
whole mechanism refuses to operate outside development.

The security property that matters: this is a weak *authentication* stand-in,
not a weak *authorization* path. Roles still come from the database, project
isolation still applies, and no header value can grant a role the user does not
hold.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from reqpilot.config import AppEnv, Settings, get_settings
from reqpilot.domain.enums import ActorKind, Role
from reqpilot.domain.ids import ActorId, ProjectId
from reqpilot.domain.models.identity import ProjectMember, User
from reqpilot.domain.policy import Actor
from reqpilot.repositories.database import get_session_factory


def get_db() -> Iterator[Session]:
    """Yield a request-scoped session, committing on success."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def load_actor(session: Session, user_id: uuid.UUID) -> Actor:
    """Build a policy actor from persisted membership rows.

    Roles are never taken from the request. They are read from
    ``project_member``, so an actor can only ever exercise a role that a
    project manager actually granted them.
    """
    roles: dict[ProjectId, frozenset[Role]] = {}
    stmt = select(ProjectMember).where(ProjectMember.user_id == user_id)
    for membership in session.scalars(stmt):
        key = ProjectId(membership.project_id)
        roles[key] = roles.get(key, frozenset()) | {membership.role}

    return Actor(
        actor_id=ActorId(user_id),
        kind=ActorKind.HUMAN,
        roles_by_project=roles,
    )


def get_actor(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_reqpilot_actor: Annotated[str | None, Header()] = None,
) -> Actor:
    """Resolve the acting user for this request. Development only."""
    if settings.app_env is AppEnv.PRODUCTION:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "header-based actor resolution is a development mechanism and is "
                "disabled outside development; real authentication is not implemented"
            ),
        )

    if not x_reqpilot_actor:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-ReqPilot-Actor header is required (development actor mechanism)",
        )

    try:
        user_id = uuid.UUID(x_reqpilot_actor)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-ReqPilot-Actor must be a user id",
        ) from None

    if session.get(User, user_id) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown actor")

    return load_actor(session, user_id)


DbSession = Annotated[Session, Depends(get_db)]
CurrentActor = Annotated[Actor, Depends(get_actor)]
