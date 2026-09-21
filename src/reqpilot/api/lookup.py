"""Locating a resource by id without leaking cross-project existence.

The architecture's paths address some resources by bare id
(``GET /requirements/{id}``) while every query must stay project-scoped. The
resolution used here is to search **only the projects the actor belongs to**,
so no unscoped query is ever issued and a resource in someone else's project is
indistinguishable from one that does not exist.

Authorization is checked *before* each attempt rather than by catching the
refusal. Exception-driven control flow would work, but it makes an ordinary
"not my project" indistinguishable from a real bug in the repository layer.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException, status

from reqpilot.domain.enums import Action, ResourceType
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.policy import Actor, ResourceRef, can


def scan_actor_projects[T](
    actor: Actor,
    action: Action,
    resource_type: ResourceType,
    loader: Callable[[ProjectId], T | None],
) -> tuple[ProjectId, T] | None:
    """Return the first ``(project_id, resource)`` the actor may legitimately see.

    ``loader`` performs a project-scoped read. It is called only for projects
    where the policy already permits ``action``, so a refusal from the
    repository layer would be a genuine defect rather than an expected outcome.
    """
    for project_id in actor.roles_by_project:
        decision = can(
            actor, action, ResourceRef(resource_type=resource_type, project_id=project_id)
        )
        if not decision.allowed:
            continue
        found = loader(project_id)
        if found is not None:
            return project_id, found
    return None


def require_found[T](
    actor: Actor,
    action: Action,
    resource_type: ResourceType,
    loader: Callable[[ProjectId], T | None],
) -> tuple[ProjectId, T]:
    """Like :func:`scan_actor_projects`, but 404s instead of returning ``None``.

    404 rather than 403 is deliberate: answering "forbidden" would confirm the
    resource exists.
    """
    result = scan_actor_projects(actor, action, resource_type, loader)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return result
