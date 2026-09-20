"""Repository base classes enforcing project scoping and authorization.

The architecture asks for authorization at two layers - API *and* repository -
so that a missed endpoint decorator cannot leak data. This module is the second
layer, and it makes the safe call shape the default one: every read and write
goes through a method that already takes an actor and a project.
"""

from __future__ import annotations

from sqlalchemy import Select
from sqlalchemy.orm import Session

from reqpilot.domain.enums import Action, ResourceType
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.policy import Actor, ResourceRef, require


class ProjectScopedRepository[T]:
    """Base for repositories whose entities belong to exactly one project.

    Subclasses provide the model and its ``project_id`` column; this class
    supplies the authorization check and the mandatory scoping predicate so
    neither can be forgotten at a call site.
    """

    #: The resource type used when authorizing access to this repository's rows.
    resource_type: ResourceType

    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor

    def authorize(self, action: Action, project_id: ProjectId) -> None:
        """Raise unless the current actor may perform ``action`` in ``project_id``.

        Called by every public repository method before touching the session.
        Raises :class:`~reqpilot.domain.errors.ProjectIsolationError` for a
        cross-project attempt, which callers audit as a security event.
        """
        require(
            self._actor,
            action,
            ResourceRef(resource_type=self.resource_type, project_id=project_id),
        )

    @staticmethod
    def scoped(stmt: Select, column: object, project_id: ProjectId) -> Select:
        """Apply the mandatory project predicate to a statement.

        Trivial by design. Its value is that an unscoped query becomes visibly
        unusual in review rather than indistinguishable from a scoped one.
        """
        return stmt.where(column == project_id)  # type: ignore[arg-type]
