"""Human review of what the quality engine detected (P5; policy rule 8).

* **Findings** are resolved (the defect is gone - usually because a new version
  fixed it) or dismissed (it never was one), each once, with a reason. Either
  removes the finding from the P1 guard that blocks ``VALIDATED``.
* **Conflicts** are taken under review, then resolved with the G4 decision
  (architecture M.3: choose A, choose B, synthesise new - or reconciled by a
  stated condition) or dismissed as not a conflict, each with a reason. Choosing
  one side may withdraw the other through the P1 lifecycle, as the same human
  action. Resolving or dismissing removes the D12 guard.
* **The glossary** gains terms an analyst defines (``FR-QAL-005``).
* **A conflict can enter the P4 clarification loop**: an analyst records an
  inconsistency finding on one side, which ``ClarificationRunner`` then raises a
  question for.

Every one of these is a human action; the pipeline can perform none of them,
whatever roles it carries. Nothing here edits a requirement version.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    BLOCKING_CONFLICT_STATUSES,
    Action,
    AuditEventType,
    ConflictResolution,
    ConflictStatus,
    FindingDetector,
    FindingSeverity,
    QualityFindingStatus,
    QualityFindingType,
)
from reqpilot.domain.errors import ProjectIsolationError, QualityError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.base import utc_now
from reqpilot.domain.models.elicitation import QualityFinding
from reqpilot.domain.models.quality import Conflict, GlossaryTerm
from reqpilot.domain.policy import Actor
from reqpilot.repositories.elicitation import QualityFindingRepository
from reqpilot.repositories.quality import ConflictRepository, GlossaryRepository
from reqpilot.repositories.requirements import RequirementVersionRepository
from reqpilot.services.audit import AuditService
from reqpilot.services.requirements import RequirementService

MAX_REASON_CHARS = 2000


def _reason(reason: str) -> str:
    cleaned = " ".join((reason or "").split())
    if not cleaned:
        raise QualityError("a reason is required")
    if len(cleaned) > MAX_REASON_CHARS:
        raise QualityError("the reason is too long")
    return cleaned


def term_key(term: str) -> str:
    return " ".join(term.lower().split())


class FindingReviewService:
    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._findings = QualityFindingRepository(session, actor)
        self._versions = RequirementVersionRepository(session, actor)
        self._audit = AuditService(session)

    def list_for_project(
        self,
        project_id: ProjectId,
        *,
        status: QualityFindingStatus | None = None,
        finding_type: QualityFindingType | None = None,
    ) -> list[QualityFinding]:
        return self._findings.list_for_project(project_id, status=status, finding_type=finding_type)

    def get(self, project_id: ProjectId, finding_id: uuid.UUID) -> QualityFinding | None:
        return self._findings.get(project_id, finding_id)

    def _close(
        self,
        project_id: ProjectId,
        finding_id: uuid.UUID,
        reason: str,
        status: QualityFindingStatus,
        action: Action,
        event: AuditEventType,
    ) -> QualityFinding:
        finding = self._findings.get(project_id, finding_id)
        if finding is None:
            raise ProjectIsolationError("quality finding not found in this project")
        self._findings.authorize(action, project_id)
        if finding.status is not QualityFindingStatus.OPEN:
            raise QualityError(f"the finding is already {finding.status}")
        cleaned = _reason(reason)  # validated before anything changes
        finding.status = status
        finding.resolution_reason = cleaned
        finding.resolved_by = self._actor.actor_id
        finding.resolved_at = utc_now()
        finding.updated_at = utc_now()
        self._findings.save(finding, action=action)
        version = self._versions.get(project_id, finding.requirement_version_id)
        self._audit.append(
            event_type=event,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="quality_finding",
            subject_id=str(finding.id),
            subject_version=str(version.version_no) if version else None,
            payload={
                "requirement_version_id": str(finding.requirement_version_id),
                "finding_type": str(finding.finding_type),
                "status": str(status),
                "reason_supplied": True,
            },
        )
        return finding

    def resolve(self, project_id: ProjectId, finding_id: uuid.UUID, reason: str) -> QualityFinding:
        return self._close(
            project_id,
            finding_id,
            reason,
            QualityFindingStatus.RESOLVED,
            Action.QUALITY_FINDING_RESOLVE,
            AuditEventType.QUALITY_FINDING_RESOLVED,
        )

    def dismiss(self, project_id: ProjectId, finding_id: uuid.UUID, reason: str) -> QualityFinding:
        return self._close(
            project_id,
            finding_id,
            reason,
            QualityFindingStatus.DISMISSED,
            Action.QUALITY_FINDING_DISMISS,
            AuditEventType.QUALITY_FINDING_DISMISSED,
        )


class GlossaryService:
    def __init__(self, session: Session, actor: Actor) -> None:
        self._actor = actor
        self._terms = GlossaryRepository(session, actor)
        self._audit = AuditService(session)

    def list_terms(self, project_id: ProjectId) -> list[GlossaryTerm]:
        return self._terms.list_for_project(project_id)

    def add(self, project_id: ProjectId, *, term: str, definition: str) -> GlossaryTerm:
        cleaned = " ".join((term or "").split())
        meaning = " ".join((definition or "").split())
        if not cleaned or len(cleaned) > 200:
            raise QualityError("a glossary term needs 1-200 characters")
        if not meaning:
            raise QualityError("a glossary term needs a definition")
        key = term_key(cleaned)
        if self._terms.get_by_key(project_id, key) is not None:
            raise QualityError(f"{cleaned!r} is already defined in this project")
        row = self._terms.add(
            GlossaryTerm(
                project_id=project_id,
                term=cleaned,
                term_key=key,
                definition=meaning,
                created_by=self._actor.actor_id,
            )
        )
        self._audit.append(
            event_type=AuditEventType.GLOSSARY_TERM_ADDED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="glossary_term",
            subject_id=str(row.id),
            payload={"term_id": str(row.id)},
        )
        return row


class ConflictService:
    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._conflicts = ConflictRepository(session, actor)
        self._versions = RequirementVersionRepository(session, actor)
        self._findings = QualityFindingRepository(session, actor)
        self._audit = AuditService(session)

    def list_for_project(
        self, project_id: ProjectId, *, status: ConflictStatus | None = None
    ) -> list[Conflict]:
        return self._conflicts.list_for_project(project_id, status=status)

    def get(self, project_id: ProjectId, conflict_id: uuid.UUID) -> Conflict | None:
        return self._conflicts.get(project_id, conflict_id)

    def require(self, project_id: ProjectId, conflict_id: uuid.UUID) -> Conflict:
        conflict = self._conflicts.get(project_id, conflict_id)
        if conflict is None:
            raise ProjectIsolationError("conflict not found in this project")
        return conflict

    def _event(self, event: AuditEventType, conflict: Conflict, payload: dict) -> None:
        self._audit.append(
            event_type=event,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=ProjectId(conflict.project_id),
            subject_type="conflict",
            subject_id=str(conflict.id),
            payload={
                "version_a_id": str(conflict.version_a_id),
                "version_b_id": str(conflict.version_b_id),
                "status": str(conflict.status),
                **payload,
            },
        )

    def review(self, project_id: ProjectId, conflict_id: uuid.UUID) -> Conflict:
        """Take an open conflict under review. It still blocks (D12)."""
        conflict = self.require(project_id, conflict_id)
        self._conflicts.authorize(Action.CONFLICT_REVIEW, project_id)
        if conflict.status is not ConflictStatus.OPEN:
            raise QualityError(
                f"only an open conflict is taken under review; it is {conflict.status}"
            )
        conflict.status = ConflictStatus.UNDER_REVIEW
        conflict.reviewed_by = self._actor.actor_id
        conflict.reviewed_at = utc_now()
        conflict.updated_at = utc_now()
        self._conflicts.save(conflict, action=Action.CONFLICT_REVIEW)
        self._event(AuditEventType.CONFLICT_REVIEWED, conflict, {})
        return conflict

    def resolve(
        self,
        project_id: ProjectId,
        conflict_id: uuid.UUID,
        *,
        resolution: ConflictResolution,
        reason: str,
        withdraw_other: bool = False,
    ) -> Conflict:
        """The G4 decision (``FR-CNF-005``). The system proposes; a human resolves."""
        conflict = self.require(project_id, conflict_id)
        self._conflicts.authorize(Action.CONFLICT_RESOLVE, project_id)
        if conflict.status not in BLOCKING_CONFLICT_STATUSES:
            raise QualityError(f"the conflict is already {conflict.status}")
        cleaned = _reason(reason)
        losing: uuid.UUID | None = None
        if withdraw_other:
            if resolution is ConflictResolution.CHOOSE_A:
                losing = conflict.version_b_id
            elif resolution is ConflictResolution.CHOOSE_B:
                losing = conflict.version_a_id
            else:
                raise QualityError("only choosing one side can withdraw the other")
        conflict.status = ConflictStatus.RESOLVED
        conflict.resolution = resolution
        conflict.resolution_reason = cleaned
        conflict.resolved_by = self._actor.actor_id
        conflict.resolved_at = utc_now()
        conflict.updated_at = utc_now()
        self._conflicts.save(conflict, action=Action.CONFLICT_RESOLVE)
        if losing is not None:
            # A separate, guarded P1 action by the same human: never a rewrite.
            RequirementService(self._session, self._actor).withdraw(
                project_id=project_id,
                version_id=losing,
                reason=f"conflict {conflict.id} resolved: {resolution}",
            )
        self._event(
            AuditEventType.CONFLICT_RESOLVED,
            conflict,
            {
                "resolution": str(resolution),
                "withdrawn_version_id": str(losing) if losing else None,
                "reason_supplied": True,
            },
        )
        return conflict

    def dismiss(self, project_id: ProjectId, conflict_id: uuid.UUID, reason: str) -> Conflict:
        """Not a conflict (a false positive). Recorded, and never raised again for this pair."""
        conflict = self.require(project_id, conflict_id)
        self._conflicts.authorize(Action.CONFLICT_DISMISS, project_id)
        if conflict.status not in BLOCKING_CONFLICT_STATUSES:
            raise QualityError(f"the conflict is already {conflict.status}")
        cleaned = _reason(reason)  # validated before anything changes
        conflict.status = ConflictStatus.DISMISSED
        conflict.resolution_reason = cleaned
        conflict.resolved_by = self._actor.actor_id
        conflict.resolved_at = utc_now()
        conflict.updated_at = utc_now()
        self._conflicts.save(conflict, action=Action.CONFLICT_DISMISS)
        self._event(AuditEventType.CONFLICT_DISMISSED, conflict, {"reason_supplied": True})
        return conflict

    def clarification_finding(
        self, project_id: ProjectId, conflict_id: uuid.UUID, *, side: str
    ) -> QualityFinding:
        """Record, as the analyst, the inconsistency finding a clarification is raised for.

        The P4 clarification loop binds a question to one version and one
        finding (``FR-CLR-001``); a conflict enters it this way, on the side the
        analyst chooses to ask about. The conflict itself stays open until
        resolved.
        """
        conflict = self.require(project_id, conflict_id)
        if conflict.status not in BLOCKING_CONFLICT_STATUSES:
            raise QualityError("only an open conflict is clarified")
        if side not in ("a", "b"):
            raise QualityError("side must be 'a' or 'b'")
        version_id, other_id, evidence, other_evidence = (
            (conflict.version_a_id, conflict.version_b_id, conflict.evidence_a, conflict.evidence_b)
            if side == "a"
            else (
                conflict.version_b_id,
                conflict.version_a_id,
                conflict.evidence_b,
                conflict.evidence_a,
            )
        )
        version = self._versions.get(project_id, version_id)
        if version is None:  # pragma: no cover - a foreign key
            raise ProjectIsolationError("requirement version not found in this project")
        span = evidence if evidence and evidence in version.statement else None
        finding = self._findings.add(
            QualityFinding(
                project_id=project_id,
                requirement_version_id=version.id,
                finding_type=QualityFindingType.INCONSISTENCY,
                severity=FindingSeverity.HIGH,
                rationale=(
                    f"conflicts with requirement version {other_id} (conflict {conflict.id}, "
                    f"{conflict.kind}): the other side states {other_evidence!r}"
                )[:2000],
                span_quote=span,
                status=QualityFindingStatus.OPEN,
                detected_by=FindingDetector.HUMAN,
                recorded_by=self._actor.actor_id,
                rule_id=f"conflict:{conflict.id}",
                related_version_id=other_id,
                evidence=[{"kind": "conflict", "ref": str(conflict.id)}],
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
                "finding_type": str(QualityFindingType.INCONSISTENCY),
                "severity": str(FindingSeverity.HIGH),
                "detected_by": str(FindingDetector.HUMAN),
                "conflict_id": str(conflict.id),
            },
        )
        return finding
