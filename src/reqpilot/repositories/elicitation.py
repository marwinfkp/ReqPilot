"""Project-scoped repositories for elicitation and clarification (P4).

Every public method authorises through ``policy.can`` for the resource's project
before touching the session and applies the mandatory project predicate - the
second layer of authorization the architecture asks for (ADR-009). Which
*session* a Stakeholder-role user may read or answer - only their own - is the
elicitation services' check, on top of this.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from reqpilot.domain.enums import (
    Action,
    ClarificationStatus,
    InterviewSessionKind,
    QualityFindingStatus,
    QualityFindingType,
    ResourceType,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.elicitation import (
    Clarification,
    InterviewSession,
    QualityFinding,
    Stakeholder,
    Utterance,
)
from reqpilot.repositories.base import ProjectScopedRepository


class StakeholderRepository(ProjectScopedRepository[Stakeholder]):
    resource_type = ResourceType.STAKEHOLDER

    def add(self, stakeholder: Stakeholder) -> Stakeholder:
        self.authorize(Action.STAKEHOLDER_CREATE, ProjectId(stakeholder.project_id))
        self._session.add(stakeholder)
        self._session.flush()
        return stakeholder

    def get(self, project_id: ProjectId, stakeholder_id: uuid.UUID) -> Stakeholder | None:
        self.authorize(Action.STAKEHOLDER_READ, project_id)
        stmt = select(Stakeholder).where(Stakeholder.id == stakeholder_id)
        return self._session.scalars(self.scoped(stmt, Stakeholder.project_id, project_id)).first()

    def list_for_project(self, project_id: ProjectId) -> list[Stakeholder]:
        self.authorize(Action.STAKEHOLDER_READ, project_id)
        stmt = self.scoped(select(Stakeholder), Stakeholder.project_id, project_id)
        return list(self._session.scalars(stmt.order_by(Stakeholder.created_at, Stakeholder.id)))


class InterviewSessionRepository(ProjectScopedRepository[InterviewSession]):
    resource_type = ResourceType.INTERVIEW_SESSION

    def add(self, session_row: InterviewSession, *, action: Action) -> InterviewSession:
        self.authorize(action, ProjectId(session_row.project_id))
        self._session.add(session_row)
        self._session.flush()
        return session_row

    def save(self, session_row: InterviewSession, *, action: Action) -> InterviewSession:
        """Persist a change the calling service made, under ``action``."""
        self.authorize(action, ProjectId(session_row.project_id))
        self._session.flush()
        return session_row

    def get(self, project_id: ProjectId, session_id: uuid.UUID) -> InterviewSession | None:
        self.authorize(Action.SESSION_READ, project_id)
        stmt = select(InterviewSession).where(InterviewSession.id == session_id)
        return self._session.scalars(
            self.scoped(stmt, InterviewSession.project_id, project_id)
        ).first()

    def list_for_project(
        self, project_id: ProjectId, *, kind: InterviewSessionKind | None = None
    ) -> list[InterviewSession]:
        self.authorize(Action.SESSION_READ, project_id)
        stmt = select(InterviewSession)
        if kind is not None:
            stmt = stmt.where(InterviewSession.kind == kind)
        stmt = self.scoped(stmt, InterviewSession.project_id, project_id)
        return list(
            self._session.scalars(stmt.order_by(InterviewSession.created_at, InterviewSession.id))
        )


class UtteranceRepository(ProjectScopedRepository[Utterance]):
    """Utterances are append-only: there is an ``add`` and there are reads."""

    resource_type = ResourceType.UTTERANCE

    def add(self, utterance: Utterance, *, action: Action) -> Utterance:
        self.authorize(action, ProjectId(utterance.project_id))
        self._session.add(utterance)
        self._session.flush()
        return utterance

    def get(self, project_id: ProjectId, utterance_id: uuid.UUID) -> Utterance | None:
        self.authorize(Action.SESSION_READ, project_id)
        stmt = select(Utterance).where(Utterance.id == utterance_id)
        return self._session.scalars(self.scoped(stmt, Utterance.project_id, project_id)).first()

    def for_session(self, project_id: ProjectId, session_id: uuid.UUID) -> list[Utterance]:
        self.authorize(Action.SESSION_READ, project_id)
        stmt = select(Utterance).where(Utterance.session_id == session_id)
        stmt = self.scoped(stmt, Utterance.project_id, project_id)
        return list(self._session.scalars(stmt.order_by(Utterance.seq)))

    def next_seq(self, project_id: ProjectId, session_id: uuid.UUID) -> int:
        self.authorize(Action.SESSION_READ, project_id)
        stmt = select(func.max(Utterance.seq)).where(Utterance.session_id == session_id)
        current = self._session.scalar(self.scoped(stmt, Utterance.project_id, project_id))
        return int(current or 0) + 1


class QualityFindingRepository(ProjectScopedRepository[QualityFinding]):
    resource_type = ResourceType.QUALITY_FINDING

    def add(
        self, finding: QualityFinding, *, action: Action = Action.QUALITY_FINDING_CREATE
    ) -> QualityFinding:
        """Record a finding: by an analyst (P4, the default) or a detector (P5,
        ``QUALITY_FINDING_DETECT``, which the pipeline may perform)."""
        if action not in (Action.QUALITY_FINDING_CREATE, Action.QUALITY_FINDING_DETECT):
            raise ValueError(f"{action} does not record a quality finding")
        self.authorize(action, ProjectId(finding.project_id))
        self._session.add(finding)
        self._session.flush()
        return finding

    def save(self, finding: QualityFinding, *, action: Action) -> QualityFinding:
        """Persist a resolution (P5: ``QUALITY_FINDING_RESOLVE`` / ``_DISMISS``)."""
        if action not in (Action.QUALITY_FINDING_RESOLVE, Action.QUALITY_FINDING_DISMISS):
            raise ValueError(f"{action} does not close a quality finding")
        self.authorize(action, ProjectId(finding.project_id))
        self._session.flush()
        return finding

    def list_for_project(
        self,
        project_id: ProjectId,
        *,
        status: QualityFindingStatus | None = None,
        finding_type: QualityFindingType | None = None,
    ) -> list[QualityFinding]:
        self.authorize(Action.QUALITY_FINDING_READ, project_id)
        stmt = self.scoped(select(QualityFinding), QualityFinding.project_id, project_id)
        if status is not None:
            stmt = stmt.where(QualityFinding.status == status)
        if finding_type is not None:
            stmt = stmt.where(QualityFinding.finding_type == finding_type)
        return list(self._session.scalars(stmt.order_by(QualityFinding.created_at)))

    def get(self, project_id: ProjectId, finding_id: uuid.UUID) -> QualityFinding | None:
        self.authorize(Action.QUALITY_FINDING_READ, project_id)
        stmt = select(QualityFinding).where(QualityFinding.id == finding_id)
        return self._session.scalars(
            self.scoped(stmt, QualityFinding.project_id, project_id)
        ).first()

    def for_version(self, project_id: ProjectId, version_id: uuid.UUID) -> list[QualityFinding]:
        self.authorize(Action.QUALITY_FINDING_READ, project_id)
        stmt = select(QualityFinding).where(QualityFinding.requirement_version_id == version_id)
        stmt = self.scoped(stmt, QualityFinding.project_id, project_id)
        return list(self._session.scalars(stmt.order_by(QualityFinding.created_at)))

    def open_count(self, project_id: ProjectId, version_id: uuid.UUID) -> int:
        return sum(
            1
            for f in self.for_version(project_id, version_id)
            if f.status is QualityFindingStatus.OPEN
        )


class ClarificationRepository(ProjectScopedRepository[Clarification]):
    resource_type = ResourceType.CLARIFICATION

    def add(self, clarification: Clarification) -> Clarification:
        self.authorize(Action.CLARIFICATION_RAISE, ProjectId(clarification.project_id))
        self._session.add(clarification)
        self._session.flush()
        return clarification

    def save(self, clarification: Clarification, *, action: Action) -> Clarification:
        self.authorize(action, ProjectId(clarification.project_id))
        self._session.flush()
        return clarification

    def get(self, project_id: ProjectId, clarification_id: uuid.UUID) -> Clarification | None:
        self.authorize(Action.CLARIFICATION_READ, project_id)
        stmt = select(Clarification).where(Clarification.id == clarification_id)
        return self._session.scalars(
            self.scoped(stmt, Clarification.project_id, project_id)
        ).first()

    def list_for_project(
        self, project_id: ProjectId, *, status: ClarificationStatus | None = None
    ) -> list[Clarification]:
        """The open-issues list (``FR-CLR-002``): open first, then oldest first."""
        self.authorize(Action.CLARIFICATION_READ, project_id)
        stmt = select(Clarification)
        if status is not None:
            stmt = stmt.where(Clarification.status == status)
        stmt = self.scoped(stmt, Clarification.project_id, project_id)
        items = list(self._session.scalars(stmt))
        return sorted(
            items,
            key=lambda c: (c.status is not ClarificationStatus.OPEN, c.created_at, str(c.id)),
        )

    def for_version(self, project_id: ProjectId, version_id: uuid.UUID) -> list[Clarification]:
        self.authorize(Action.CLARIFICATION_READ, project_id)
        stmt = select(Clarification).where(Clarification.requirement_version_id == version_id)
        stmt = self.scoped(stmt, Clarification.project_id, project_id)
        return list(self._session.scalars(stmt.order_by(Clarification.created_at)))

    def open_for_finding(
        self, project_id: ProjectId, finding_id: uuid.UUID
    ) -> Clarification | None:
        self.authorize(Action.CLARIFICATION_READ, project_id)
        stmt = select(Clarification).where(
            Clarification.quality_finding_id == finding_id,
            Clarification.status == ClarificationStatus.OPEN,
        )
        return self._session.scalars(
            self.scoped(stmt, Clarification.project_id, project_id)
        ).first()
