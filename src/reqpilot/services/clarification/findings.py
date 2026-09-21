"""Quality findings - the minimal P4 interface (architecture G.4, E #6).

The clarification loop needs a defect to bind a question to (``FR-CLR-001``).
Detecting defects is the Quality-Analysis role, roadmap **P5**, and is not built
here. What P4 provides is the ``quality_finding`` record and one way to create
one: an Analyst records it by hand (``detected_by = human``). P5's detector will
write the same rows.

Recording a finding does not change the requirement version. An open finding
does block ``ANALYZED -> VALIDATED`` (H.3), through the P1 transition context.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    AuditEventType,
    FindingDetector,
    FindingSeverity,
    QualityFindingStatus,
    QualityFindingType,
)
from reqpilot.domain.errors import ClarificationError, ProjectIsolationError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.elicitation import QualityFinding
from reqpilot.domain.policy import Actor
from reqpilot.repositories.elicitation import QualityFindingRepository
from reqpilot.repositories.requirements import RequirementVersionRepository
from reqpilot.services.audit import AuditService


class QualityFindingService:
    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._findings = QualityFindingRepository(session, actor)
        self._versions = RequirementVersionRepository(session, actor)
        self._audit = AuditService(session)

    def record(
        self,
        *,
        project_id: ProjectId,
        version_id: uuid.UUID,
        finding_type: QualityFindingType,
        severity: FindingSeverity,
        rationale: str,
        span_quote: str | None = None,
    ) -> QualityFinding:
        version = self._versions.get(project_id, version_id)
        if version is None:
            raise ProjectIsolationError("requirement version not found in this project")
        rationale = " ".join(rationale.split())
        if not rationale:
            raise ClarificationError("a finding needs a rationale")
        span = " ".join((span_quote or "").split()) or None
        if span is not None and span.lower() not in " ".join(version.statement.split()).lower():
            raise ClarificationError(
                "the finding's span must be words of the requirement statement"
            )
        finding = self._findings.add(
            QualityFinding(
                project_id=project_id,
                requirement_version_id=version.id,
                finding_type=finding_type,
                severity=severity,
                rationale=rationale,
                span_quote=span,
                status=QualityFindingStatus.OPEN,
                detected_by=FindingDetector.HUMAN,
                recorded_by=self._actor.actor_id,
            )
        )
        self._audit.append(
            event_type=AuditEventType.QUALITY_FINDING_RAISED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="requirement_version",
            subject_id=str(version.id),
            subject_version=str(version.version_no),
            payload={
                "finding_id": str(finding.id),
                "finding_type": str(finding_type),
                "severity": str(severity),
                "detected_by": str(FindingDetector.HUMAN),
            },
        )
        return finding

    def get(self, project_id: ProjectId, finding_id: uuid.UUID) -> QualityFinding | None:
        return self._findings.get(project_id, finding_id)

    def for_version(self, project_id: ProjectId, version_id: uuid.UUID) -> list[QualityFinding]:
        return self._findings.for_version(project_id, version_id)
