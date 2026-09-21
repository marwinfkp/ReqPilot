"""Project-scoped persistence for baselines.

Append and read only. There is no update and no delete: a baseline is frozen
when it is created, and correcting one means creating another.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from reqpilot.domain.enums import Action, ResourceType
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.baseline import Baseline, BaselineMember
from reqpilot.repositories.base import ProjectScopedRepository


class BaselineRepository(ProjectScopedRepository[Baseline]):
    """Baselines and their membership, always within one project."""

    resource_type = ResourceType.BASELINE

    def add(self, baseline: Baseline) -> Baseline:
        self.authorize(Action.BASELINE_CREATE, ProjectId(baseline.project_id))
        self._session.add(baseline)
        self._session.flush()
        return baseline

    def add_member(self, member: BaselineMember) -> BaselineMember:
        self.authorize(Action.BASELINE_CREATE, ProjectId(member.project_id))
        self._session.add(member)
        self._session.flush()
        return member

    def get(self, project_id: ProjectId, baseline_id: uuid.UUID) -> Baseline | None:
        self.authorize(Action.BASELINE_READ, project_id)
        stmt = select(Baseline).where(Baseline.id == baseline_id)
        stmt = self.scoped(stmt, Baseline.project_id, project_id)
        return self._session.scalars(stmt).first()

    def list_for_project(self, project_id: ProjectId) -> list[Baseline]:
        self.authorize(Action.BASELINE_READ, project_id)
        stmt = select(Baseline).order_by(Baseline.frozen_at.desc())
        stmt = self.scoped(stmt, Baseline.project_id, project_id)
        return list(self._session.scalars(stmt))

    def list_members(self, project_id: ProjectId, baseline_id: uuid.UUID) -> list[BaselineMember]:
        self.authorize(Action.BASELINE_READ, project_id)
        stmt = (
            select(BaselineMember)
            .where(BaselineMember.baseline_id == baseline_id)
            .order_by(BaselineMember.created_at)
        )
        stmt = self.scoped(stmt, BaselineMember.project_id, project_id)
        return list(self._session.scalars(stmt))

    def label_exists(self, project_id: ProjectId, label: str) -> bool:
        self.authorize(Action.BASELINE_READ, project_id)
        stmt = select(Baseline.id).where(Baseline.label == label)
        stmt = self.scoped(stmt, Baseline.project_id, project_id)
        return self._session.scalars(stmt).first() is not None
