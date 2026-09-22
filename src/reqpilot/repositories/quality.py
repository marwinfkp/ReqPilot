"""Repositories for conflicts and the project glossary (P5; architecture G.4, G.5).

Every method authorises the actor for the action in the row's project before it
touches the session (the second layer of ADR-009).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import or_, select

from reqpilot.domain.enums import (
    BLOCKING_CONFLICT_STATUSES,
    Action,
    ConflictStatus,
    ResourceType,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.quality import Conflict, GlossaryTerm
from reqpilot.repositories.base import ProjectScopedRepository

_CONFLICT_WRITES = frozenset(
    {Action.CONFLICT_REVIEW, Action.CONFLICT_RESOLVE, Action.CONFLICT_DISMISS}
)


class ConflictRepository(ProjectScopedRepository[Conflict]):
    resource_type = ResourceType.CONFLICT

    def add(self, conflict: Conflict) -> Conflict:
        self.authorize(Action.CONFLICT_DETECT, ProjectId(conflict.project_id))
        self._session.add(conflict)
        self._session.flush()
        return conflict

    def save(self, conflict: Conflict, *, action: Action) -> Conflict:
        if action not in _CONFLICT_WRITES:
            raise ValueError(f"{action} does not change a conflict")
        self.authorize(action, ProjectId(conflict.project_id))
        self._session.flush()
        return conflict

    def get(self, project_id: ProjectId, conflict_id: uuid.UUID) -> Conflict | None:
        self.authorize(Action.CONFLICT_READ, project_id)
        stmt = select(Conflict).where(Conflict.id == conflict_id)
        return self._session.scalars(self.scoped(stmt, Conflict.project_id, project_id)).first()

    def list_for_project(
        self, project_id: ProjectId, *, status: ConflictStatus | None = None
    ) -> list[Conflict]:
        self.authorize(Action.CONFLICT_READ, project_id)
        stmt = self.scoped(select(Conflict), Conflict.project_id, project_id)
        if status is not None:
            stmt = stmt.where(Conflict.status == status)
        return list(self._session.scalars(stmt.order_by(Conflict.created_at)))

    def touching(self, project_id: ProjectId, version_id: uuid.UUID) -> list[Conflict]:
        """Every conflict with this version on either side."""
        self.authorize(Action.CONFLICT_READ, project_id)
        stmt = select(Conflict).where(
            or_(Conflict.version_a_id == version_id, Conflict.version_b_id == version_id)
        )
        stmt = self.scoped(stmt, Conflict.project_id, project_id)
        return list(self._session.scalars(stmt.order_by(Conflict.created_at)))

    def blocking_count(self, project_id: ProjectId, version_id: uuid.UUID) -> int:
        """Open or under-review conflicts touching the version (the D12 guard)."""
        return sum(
            1
            for c in self.touching(project_id, version_id)
            if c.status in BLOCKING_CONFLICT_STATUSES
        )

    def for_pair(
        self, project_id: ProjectId, version_a_id: uuid.UUID, version_b_id: uuid.UUID
    ) -> list[Conflict]:
        """Every conflict ever recorded for this exact pair of versions, any status."""
        self.authorize(Action.CONFLICT_READ, project_id)
        stmt = select(Conflict).where(
            or_(
                (Conflict.version_a_id == version_a_id) & (Conflict.version_b_id == version_b_id),
                (Conflict.version_a_id == version_b_id) & (Conflict.version_b_id == version_a_id),
            )
        )
        return list(self._session.scalars(self.scoped(stmt, Conflict.project_id, project_id)))

    def pairs_recorded(
        self, project_id: ProjectId, version_ids: Iterable[uuid.UUID]
    ) -> set[frozenset[uuid.UUID]]:
        """The version pairs among ``version_ids`` that already have a conflict row."""
        ids = set(version_ids)
        return {
            frozenset({c.version_a_id, c.version_b_id})
            for c in self.list_for_project(project_id)
            if c.version_a_id in ids or c.version_b_id in ids
        }


class GlossaryRepository(ProjectScopedRepository[GlossaryTerm]):
    resource_type = ResourceType.GLOSSARY_TERM

    def add(self, term: GlossaryTerm) -> GlossaryTerm:
        self.authorize(Action.GLOSSARY_MANAGE, ProjectId(term.project_id))
        self._session.add(term)
        self._session.flush()
        return term

    def list_for_project(self, project_id: ProjectId) -> list[GlossaryTerm]:
        self.authorize(Action.GLOSSARY_READ, project_id)
        stmt = self.scoped(select(GlossaryTerm), GlossaryTerm.project_id, project_id)
        return list(self._session.scalars(stmt.order_by(GlossaryTerm.term_key)))

    def get_by_key(self, project_id: ProjectId, term_key: str) -> GlossaryTerm | None:
        self.authorize(Action.GLOSSARY_READ, project_id)
        stmt = select(GlossaryTerm).where(GlossaryTerm.term_key == term_key)
        return self._session.scalars(self.scoped(stmt, GlossaryTerm.project_id, project_id)).first()
