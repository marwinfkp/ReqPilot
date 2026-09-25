"""Project-scoped persistence for the typed trace graph (architecture N.1).

Append and read only. There is no update or delete method: a trace link, once
recorded, is history (``FR-TRC-004``), and the ORM guard and a PostgreSQL
trigger refuse the attempt anyway.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import or_, select

from reqpilot.domain.enums import Action, ResourceType
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.traceability import TraceabilityLink
from reqpilot.repositories.base import ProjectScopedRepository

EdgeKey = tuple[str, str, str, str, str]


class TraceLinkRepository(ProjectScopedRepository[TraceabilityLink]):
    resource_type = ResourceType.TRACEABILITY_LINK

    def add_many(self, project_id: ProjectId, links: Iterable[TraceabilityLink]) -> int:
        """Insert the given links (already validated by the trace service)."""
        self.authorize(Action.TRACE_SYNC, project_id)
        count = 0
        for link in links:
            if link.project_id != project_id:  # pragma: no cover - service guarantees it
                raise ValueError("a trace link belongs to the project it is written in")
            self._session.add(link)
            count += 1
        self._session.flush()
        return count

    def existing_keys(self, project_id: ProjectId) -> set[EdgeKey]:
        self.authorize(Action.TRACE_READ, project_id)
        stmt = select(
            TraceabilityLink.from_type,
            TraceabilityLink.from_id,
            TraceabilityLink.link_type,
            TraceabilityLink.to_type,
            TraceabilityLink.to_id,
        )
        stmt = self.scoped(stmt, TraceabilityLink.project_id, project_id)
        return {tuple(row) for row in self._session.execute(stmt)}  # type: ignore[misc]

    def list_for_project(self, project_id: ProjectId) -> list[TraceabilityLink]:
        self.authorize(Action.TRACE_READ, project_id)
        stmt = select(TraceabilityLink).order_by(
            TraceabilityLink.created_at, TraceabilityLink.link_type, TraceabilityLink.id
        )
        stmt = self.scoped(stmt, TraceabilityLink.project_id, project_id)
        return list(self._session.scalars(stmt))

    def touching(
        self, project_id: ProjectId, node_type: str, node_id: str
    ) -> list[TraceabilityLink]:
        """Every edge with this node at either end."""
        self.authorize(Action.TRACE_READ, project_id)
        stmt = select(TraceabilityLink).where(
            or_(
                (TraceabilityLink.from_type == node_type) & (TraceabilityLink.from_id == node_id),
                (TraceabilityLink.to_type == node_type) & (TraceabilityLink.to_id == node_id),
            )
        )
        stmt = self.scoped(stmt, TraceabilityLink.project_id, project_id)
        return list(self._session.scalars(stmt.order_by(TraceabilityLink.created_at)))

    def anchored_to(self, project_id: ProjectId, version_id: uuid.UUID) -> list[TraceabilityLink]:
        """The edges recorded for one exact requirement version (``FR-TRC-004``)."""
        self.authorize(Action.TRACE_READ, project_id)
        stmt = select(TraceabilityLink).where(TraceabilityLink.anchor_version_id == version_id)
        stmt = self.scoped(stmt, TraceabilityLink.project_id, project_id)
        return list(
            self._session.scalars(stmt.order_by(TraceabilityLink.link_type, TraceabilityLink.to_id))
        )
