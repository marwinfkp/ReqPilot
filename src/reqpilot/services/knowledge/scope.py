"""A project's knowledge scope: allowlist, jurisdictions, KB pin (``FR-RAG-002``; J.4, J.6).

These three settings decide what a project's retrieval can ever return, so they
are governed like any other security-relevant configuration: changed only by the
project's Knowledge-Base Administrator, and every change audited on the
project's own hash chain.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from reqpilot.domain.enums import AuditEventType
from reqpilot.domain.errors import KnowledgeBaseError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.knowledge import NormativeSource, SourceAllowlist
from reqpilot.domain.policy import Actor
from reqpilot.repositories.knowledge import KnowledgeScopeRepository
from reqpilot.services.audit import AuditService
from reqpilot.services.knowledge.admin import normalise_jurisdiction


@dataclass(frozen=True)
class ProjectKnowledgeScope:
    """What one project's retrieval may draw on."""

    project_id: ProjectId
    jurisdiction_scope: tuple[str, ...]
    kb_version_pin: int | None
    current_kb_version: int
    allowlisted_sources: tuple[NormativeSource, ...]

    @property
    def effective_kb_version(self) -> int:
        return self.kb_version_pin if self.kb_version_pin is not None else self.current_kb_version


class KnowledgeScopeService:
    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._repo = KnowledgeScopeRepository(session, actor)
        self._audit = AuditService(session)

    def scope(self, project_id: ProjectId) -> ProjectKnowledgeScope:
        project = self._repo.project(project_id)
        if project is None:
            raise KnowledgeBaseError("project not found")
        return ProjectKnowledgeScope(
            project_id=project_id,
            jurisdiction_scope=tuple(project.jurisdiction_scope or ()),
            kb_version_pin=project.kb_version_pin,
            current_kb_version=self._repo.current_kb_version(project_id),
            allowlisted_sources=tuple(self._repo.allowlisted_sources(project_id)),
        )

    def set_scope(
        self,
        project_id: ProjectId,
        *,
        jurisdiction_scope: Sequence[str],
        kb_version_pin: int | None,
    ) -> ProjectKnowledgeScope:
        """Set the jurisdictions and the KB-version pin together."""
        codes = sorted({normalise_jurisdiction(c) for c in jurisdiction_scope})
        current = self._repo.current_kb_version(project_id)
        if kb_version_pin is not None and not 1 <= kb_version_pin <= current:
            raise KnowledgeBaseError(
                f"a KB pin must name an existing KB version (1..{current}), got {kb_version_pin}"
            )
        self._repo.set_scope(project_id, jurisdictions=codes, kb_version_pin=kb_version_pin)
        self._record(
            project_id,
            {"change": "scope_set", "jurisdiction_scope": codes, "kb_version_pin": kb_version_pin},
        )
        return self.scope(project_id)

    def allow(self, project_id: ProjectId, source_id: uuid.UUID) -> SourceAllowlist:
        """Add a normative source to the project's allowlist."""
        if not self._repo.source_exists(project_id, source_id):
            raise KnowledgeBaseError(f"normative source {source_id} not found")
        if self._repo.entry(project_id, source_id) is not None:
            raise KnowledgeBaseError("the source is already allowlisted for this project")
        entry = SourceAllowlist(
            project_id=project_id, normative_source_id=source_id, added_by=self._actor.actor_id
        )
        self._repo.add(entry)
        self._record(
            project_id, {"change": "allowlist_added", "normative_source_id": str(source_id)}
        )
        return entry

    def disallow(self, project_id: ProjectId, source_id: uuid.UUID) -> None:
        """Remove a source from the project's allowlist. Existing evidence still resolves."""
        entry = self._repo.entry(project_id, source_id)
        if entry is None:
            raise KnowledgeBaseError("the source is not on this project's allowlist")
        self._repo.remove(project_id, entry)
        self._record(
            project_id, {"change": "allowlist_removed", "normative_source_id": str(source_id)}
        )

    def _record(self, project_id: ProjectId, payload: dict[str, object]) -> None:
        self._audit.append(
            event_type=AuditEventType.KB_SCOPE_CHANGED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="project",
            subject_id=str(project_id),
            payload=payload,
        )
