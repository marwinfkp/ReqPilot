"""Stakeholders of a project, and who may act for them (P4; architecture G.3).

Also the one place that answers "may this actor see or answer as this
stakeholder?", used by the session and clarification services:

* a user holding a staff role in the project (Analyst, Project Manager,
  Compliance Officer, Security Reviewer, Auditor) sees every session the policy
  lets them read; an Analyst may answer on a stakeholder's behalf
  (``FR-ELI-005``);
* a user holding **only** the Stakeholder role sees and answers the sessions and
  clarifications of the stakeholder record linked to them, and nothing else.

A refusal is raised as :class:`ProjectIsolationError`, so the API answers 404 and
never confirms that someone else's session exists.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from reqpilot.domain.enums import AuditEventType, Role, StakeholderAuthority
from reqpilot.domain.errors import ElicitationError, ProjectIsolationError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.elicitation import Stakeholder
from reqpilot.domain.models.identity import ProjectMember
from reqpilot.domain.policy import Actor
from reqpilot.repositories.elicitation import StakeholderRepository
from reqpilot.rules.elicitation import ElicitationRules
from reqpilot.services.audit import AuditService

#: Project roles that see sessions as staff rather than as the stakeholder.
STAFF_ROLES = frozenset(
    {
        Role.ANALYST,
        Role.PROJECT_MANAGER,
        Role.COMPLIANCE_OFFICER,
        Role.SECURITY_REVIEWER,
        Role.AUDITOR,
    }
)


def is_stakeholder_only(actor: Actor, project_id: ProjectId) -> bool:
    roles = actor.roles_in(project_id)
    return Role.STAKEHOLDER in roles and not roles & STAFF_ROLES


def is_linked_user(actor: Actor, stakeholder: Stakeholder) -> bool:
    return stakeholder.user_id is not None and stakeholder.user_id == actor.actor_id


def require_may_view(actor: Actor, project_id: ProjectId, stakeholder: Stakeholder) -> None:
    """A Stakeholder-only user sees only their own stakeholder's records."""
    if is_stakeholder_only(actor, project_id) and not is_linked_user(actor, stakeholder):
        raise ProjectIsolationError("not found")


def answering_on_behalf(actor: Actor, project_id: ProjectId, stakeholder: Stakeholder) -> bool:
    """Whether ``actor`` answers for ``stakeholder`` (Analyst) or as them.

    Raises unless one of the two is legitimate: the linked user answers as the
    stakeholder; an Analyst may record an answer on anyone's behalf
    (``FR-ELI-005``). The caller has already checked the action's policy grant.
    """
    if is_linked_user(actor, stakeholder):
        return False
    if Role.ANALYST in actor.roles_in(project_id):
        return True
    raise ProjectIsolationError("not found")


class StakeholderService:
    def __init__(self, session: Session, actor: Actor, rules: ElicitationRules) -> None:
        self._session = session
        self._actor = actor
        self._rules = rules
        self._repo = StakeholderRepository(session, actor)
        self._audit = AuditService(session)

    def create(
        self,
        *,
        project_id: ProjectId,
        name: str,
        stakeholder_role: str,
        authority_level: StakeholderAuthority,
        user_id: uuid.UUID | None = None,
    ) -> Stakeholder:
        name = " ".join(name.split())
        if not name:
            raise ElicitationError("a stakeholder needs a name")
        if stakeholder_role not in self._rules.stakeholder_roles:
            raise ElicitationError(
                f"unknown stakeholder role {stakeholder_role!r}; the interview templates "
                f"define {list(self._rules.stakeholder_roles)}"
            )
        if user_id is not None:
            member = self._session.scalars(
                select(ProjectMember).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.user_id == user_id,
                    ProjectMember.role == Role.STAKEHOLDER,
                )
            ).first()
            if member is None:
                raise ElicitationError(
                    "a linked user must hold the Stakeholder role in this project"
                )
        stakeholder = self._repo.add(
            Stakeholder(
                project_id=project_id,
                name=name,
                stakeholder_role=stakeholder_role,
                authority_level=authority_level,
                user_id=user_id,
                created_by=self._actor.actor_id,
            )
        )
        self._audit.append(
            event_type=AuditEventType.STAKEHOLDER_CREATED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="stakeholder",
            subject_id=str(stakeholder.id),
            payload={
                "stakeholder_role": stakeholder_role,
                "authority_level": str(authority_level),
                "linked_user": user_id is not None,
            },
        )
        return stakeholder

    def get(self, project_id: ProjectId, stakeholder_id: uuid.UUID) -> Stakeholder | None:
        stakeholder = self._repo.get(project_id, stakeholder_id)
        if stakeholder is None:
            return None
        if is_stakeholder_only(self._actor, project_id) and not is_linked_user(
            self._actor, stakeholder
        ):
            return None
        return stakeholder

    def list_stakeholders(self, project_id: ProjectId) -> list[Stakeholder]:
        items = self._repo.list_for_project(project_id)
        if is_stakeholder_only(self._actor, project_id):
            return [s for s in items if is_linked_user(self._actor, s)]
        return items
