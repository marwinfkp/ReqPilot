"""Project-scoped persistence for requirements and their versions.

Following the foundation pattern: every public method authorises first and
scopes second, so an unauthorised or cross-project read is refused before the
session is touched. Business rules - lifecycle, approval, baselines - live in
the service layer, not here.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from reqpilot.domain.enums import Action, ResourceType
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle.states import RequirementState
from reqpilot.domain.models.requirements import Requirement, RequirementVersion
from reqpilot.repositories.base import ProjectScopedRepository


class RequirementRepository(ProjectScopedRepository[Requirement]):
    """Requirements, always within one project."""

    resource_type = ResourceType.REQUIREMENT

    def add(self, requirement: Requirement) -> Requirement:
        self.authorize(Action.REQUIREMENT_CREATE, ProjectId(requirement.project_id))
        self._session.add(requirement)
        self._session.flush()
        return requirement

    def get(self, project_id: ProjectId, requirement_id: uuid.UUID) -> Requirement | None:
        """Return one requirement, or ``None`` if it is not in this project.

        Returning ``None`` rather than raising for a wrong-project id is
        deliberate: the caller turns it into the same 404 it would return for a
        nonexistent id, so cross-project *existence* does not leak.
        """
        self.authorize(Action.REQUIREMENT_READ, project_id)
        stmt = select(Requirement).where(Requirement.id == requirement_id)
        stmt = self.scoped(stmt, Requirement.project_id, project_id)
        return self._session.scalars(stmt).first()

    def get_by_human_id(self, project_id: ProjectId, human_id: str) -> Requirement | None:
        self.authorize(Action.REQUIREMENT_READ, project_id)
        stmt = select(Requirement).where(Requirement.human_id == human_id)
        stmt = self.scoped(stmt, Requirement.project_id, project_id)
        return self._session.scalars(stmt).first()

    def list_for_project(self, project_id: ProjectId) -> list[Requirement]:
        self.authorize(Action.REQUIREMENT_READ, project_id)
        stmt = select(Requirement).order_by(Requirement.human_id)
        stmt = self.scoped(stmt, Requirement.project_id, project_id)
        return list(self._session.scalars(stmt))

    def existing_human_ids(self, project_id: ProjectId) -> list[str]:
        """Every human id already used in this project, for id allocation."""
        self.authorize(Action.REQUIREMENT_READ, project_id)
        stmt = select(Requirement.human_id)
        stmt = self.scoped(stmt, Requirement.project_id, project_id)
        return list(self._session.scalars(stmt))


class RequirementVersionRepository(ProjectScopedRepository[RequirementVersion]):
    """Requirement versions, always within one project."""

    resource_type = ResourceType.REQUIREMENT_VERSION

    def add(self, version: RequirementVersion) -> RequirementVersion:
        self.authorize(Action.REQUIREMENT_CREATE, ProjectId(version.project_id))
        self._session.add(version)
        self._session.flush()
        return version

    def get(self, project_id: ProjectId, version_id: uuid.UUID) -> RequirementVersion | None:
        self.authorize(Action.REQUIREMENT_READ, project_id)
        stmt = select(RequirementVersion).where(RequirementVersion.id == version_id)
        stmt = self.scoped(stmt, RequirementVersion.project_id, project_id)
        return self._session.scalars(stmt).first()

    def list_for_requirement(
        self, project_id: ProjectId, requirement_id: uuid.UUID
    ) -> list[RequirementVersion]:
        """Full version history, oldest first."""
        self.authorize(Action.REQUIREMENT_READ, project_id)
        stmt = (
            select(RequirementVersion)
            .where(RequirementVersion.requirement_id == requirement_id)
            .order_by(RequirementVersion.version_no)
        )
        stmt = self.scoped(stmt, RequirementVersion.project_id, project_id)
        return list(self._session.scalars(stmt))

    def highest_version_no(self, project_id: ProjectId, requirement_id: uuid.UUID) -> int:
        """The newest version number, or 0 when the requirement has none."""
        self.authorize(Action.REQUIREMENT_READ, project_id)
        stmt = (
            select(RequirementVersion.version_no)
            .where(RequirementVersion.requirement_id == requirement_id)
            .order_by(RequirementVersion.version_no.desc())
            .limit(1)
        )
        stmt = self.scoped(stmt, RequirementVersion.project_id, project_id)
        return self._session.scalars(stmt).first() or 0

    def list_by_state(
        self, project_id: ProjectId, state: RequirementState
    ) -> list[RequirementVersion]:
        self.authorize(Action.REQUIREMENT_READ, project_id)
        stmt = select(RequirementVersion).where(RequirementVersion.state == state)
        stmt = self.scoped(stmt, RequirementVersion.project_id, project_id)
        return list(self._session.scalars(stmt))
