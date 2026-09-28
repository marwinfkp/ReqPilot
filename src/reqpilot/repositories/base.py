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
from reqpilot.domain.errors import ProjectDeletedError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.identity import Project
from reqpilot.domain.policy import Actor, ResourceRef, require


def refuse_if_deleted(session: Session, action: Action, project_id: ProjectId | None) -> None:
    """Raise :class:`ProjectDeletedError` for any write to a deleted project.

    P11 (``FR-ADM-006``, architecture P.2): a deleted project's content is gone,
    so a read finds nothing (and a lookup that scans the actor's projects moves
    on); what survives - the tombstone and the redacted audit trail - is read
    and verified through their own paths. Nothing can be written to it again.
    """
    if project_id is None or action.value.endswith(".read") or action is Action.AUDIT_VERIFY:
        return
    project = session.get(Project, project_id)
    if project is not None and project.deleted_at is not None:
        raise ProjectDeletedError(
            "this project was deleted; only its redacted audit trail remains (FR-ADM-006)"
        )


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

    def authorize(
        self, action: Action, project_id: ProjectId, resource_id: object | None = None
    ) -> None:
        """Raise unless the current actor may perform ``action`` in ``project_id``.

        Called by every public repository method before touching the session.
        Raises :class:`~reqpilot.domain.errors.ProjectIsolationError` for a
        cross-project attempt, which callers audit as a security event.
        ``resource_id`` (P11) names the one row an access is about, where the
        policy needs it - an agent's token is bound to one run.
        """
        require(
            self._actor,
            action,
            ResourceRef(
                resource_type=self.resource_type,
                project_id=project_id,
                resource_id=str(resource_id) if resource_id is not None else None,
            ),
        )
        refuse_if_deleted(self._session, action, project_id)

    @staticmethod
    def scoped(stmt: Select, column: object, project_id: ProjectId) -> Select:
        """Apply the mandatory project predicate to a statement.

        Trivial by design. Its value is that an unscoped query becomes visibly
        unusual in review rather than indistinguishable from a scoped one.
        """
        return stmt.where(column == project_id)  # type: ignore[arg-type]
