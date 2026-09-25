"""Reading the persisted trace graph (architecture N.1-N.4).

A read model over ``traceability_link`` rows: nothing here writes, and nothing
here infers an edge. Every answer - the RTM, the coverage report, E6, a
version's historical provenance - is computed from these persisted edges, so a
relationship that was never recorded is reported as missing rather than
assumed (``FR-TRC-003``).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from reqpilot.domain.enums import Action, ResourceType
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.traceability import TraceabilityLink
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.domain.traceability import TraceLinkType, TraceNodeType
from reqpilot.repositories.traceability import TraceLinkRepository

NodeKey = tuple[str, str]


@dataclass
class TraceGraph:
    """An in-memory index of one project's persisted edges."""

    project_id: ProjectId
    links: list[TraceabilityLink]
    _out: dict[NodeKey, list[TraceabilityLink]] = field(default_factory=dict)
    _in: dict[NodeKey, list[TraceabilityLink]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        out: dict[NodeKey, list[TraceabilityLink]] = defaultdict(list)
        inbound: dict[NodeKey, list[TraceabilityLink]] = defaultdict(list)
        for link in self.links:
            out[(link.from_type, link.from_id)].append(link)
            inbound[(link.to_type, link.to_id)].append(link)
        self._out, self._in = dict(out), dict(inbound)

    def outgoing(
        self, node_type: TraceNodeType, node_id: object, link_type: TraceLinkType | None = None
    ) -> list[TraceabilityLink]:
        found = self._out.get((str(node_type), str(node_id)), [])
        return [e for e in found if link_type is None or e.link_type == str(link_type)]

    def incoming(
        self, node_type: TraceNodeType, node_id: object, link_type: TraceLinkType | None = None
    ) -> list[TraceabilityLink]:
        found = self._in.get((str(node_type), str(node_id)), [])
        return [e for e in found if link_type is None or e.link_type == str(link_type)]

    def targets(
        self, node_type: TraceNodeType, node_id: object, link_type: TraceLinkType
    ) -> list[str]:
        return sorted({e.to_id for e in self.outgoing(node_type, node_id, link_type)})

    def sources(
        self, node_type: TraceNodeType, node_id: object, link_type: TraceLinkType
    ) -> list[str]:
        return sorted({e.from_id for e in self.incoming(node_type, node_id, link_type)})

    def anchored(self, version_id: uuid.UUID) -> list[TraceabilityLink]:
        """The edges recorded for one exact version (``FR-TRC-004``)."""
        return sorted(
            (e for e in self.links if e.anchor_version_id == version_id),
            key=lambda e: (e.link_type, e.from_type, e.from_id, e.to_type, e.to_id),
        )


class TraceQueryService:
    """Loads the persisted graph for a project. Reads only."""

    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._links = TraceLinkRepository(session, actor)

    def graph(self, project_id: ProjectId) -> TraceGraph:
        require(
            self._actor,
            Action.TRACE_READ,
            ResourceRef(resource_type=ResourceType.TRACEABILITY_LINK, project_id=project_id),
        )
        return TraceGraph(project_id=project_id, links=self._links.list_for_project(project_id))

    def version_links(self, project_id: ProjectId, version_id: uuid.UUID) -> list[TraceabilityLink]:
        """The graph of one exact version as recorded - historical versions included."""
        return self._links.anchored_to(project_id, version_id)
