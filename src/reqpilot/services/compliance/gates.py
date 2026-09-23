"""G2 and G3 as subjects of the P1 approval service (architecture M.2, M.3, I.8).

The approval service stays the **only** decision path. This module supplies what
it needs for the two P6 subject types, without which a decision could not be
bound or settled:

* **Binding.** The subject's content hash, recomputed from the persisted row and
  its requirement version at decision time. A G2/G3 decision is refused
  (``StaleApprovalError``) when the requirement has moved on to another version,
  when the version is no longer being analysed, or when the recomputed hash no
  longer equals the one captured when the task was raised.
* **Settlement.** Approve -> the mapping or finding is ``APPROVED``; reject ->
  ``REJECTED`` (a G2 rejection also records a gap for the control, M.3: "mapping
  discarded, gap recorded"). A G2/G3 decision **never** moves a requirement's
  lifecycle state and never approves or baselines a requirement: that is G1's.

Nothing here reads model output. The gate fired from a persisted, deterministically
evaluated column; the human's decision is the only thing that clears it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from reqpilot.domain.compliance.hashing import finding_hash, mapping_hash
from reqpilot.domain.enums import (
    ApprovalDecisionType,
    AuditEventType,
    ComplianceGapOrigin,
    ComplianceMappingStatus,
    Gate,
    ResourceType,
    SecurityFindingStatus,
)
from reqpilot.domain.errors import ReqPilotError, StaleApprovalError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.lifecycle.states import TERMINAL_STATES
from reqpilot.domain.models.approval import ApprovalDecision, ApprovalTask
from reqpilot.domain.models.compliance import (
    ComplianceGap,
    ComplianceMapping,
    SecurityPrivacyFinding,
)
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.domain.policy import Actor
from reqpilot.repositories.compliance import (
    ComplianceGapRepository,
    ComplianceMappingRepository,
    SecurityFindingRepository,
)
from reqpilot.repositories.requirements import (
    RequirementRepository,
    RequirementVersionRepository,
)
from reqpilot.services.audit import AuditService

MAPPING_SUBJECT = ResourceType.COMPLIANCE_MAPPING.value
FINDING_SUBJECT = ResourceType.SECURITY_PRIVACY_FINDING.value

#: The P6 approval subjects and the one gate each is decided at.
GATED_ANALYSIS_SUBJECTS: dict[str, Gate] = {
    MAPPING_SUBJECT: Gate.G2_REGULATORY_INTERPRETATION,
    FINDING_SUBJECT: Gate.G3_HIGH_RISK_SECURITY,
}

#: A version in one of these states is not being analysed: a decision about an
#: interpretation of it would be about history, and is refused as stale.
_NOT_CURRENT: frozenset[RequirementState] = TERMINAL_STATES | {RequirementState.REJECTED}


def recompute_mapping_hash(
    mapping: ComplianceMapping, version: RequirementVersion, evidence_ids: list[uuid.UUID]
) -> str:
    return mapping_hash(
        project_id=str(mapping.project_id),
        requirement_version_id=str(mapping.requirement_version_id),
        version_content_hash=version.content_hash,
        control_key=mapping.control_key,
        checklist_ref=mapping.checklist_ref,
        relationship=str(mapping.relationship),
        rationale=mapping.rationale,
        candidate_text=mapping.candidate_text,
        implied_obligation=mapping.implied_obligation,
        jurisdiction=mapping.jurisdiction,
        source_type=str(mapping.source_type),
        evidence_ids=[str(e) for e in evidence_ids],
        is_high_impact=bool(mapping.is_high_impact),
    )


def recompute_finding_hash(
    finding: SecurityPrivacyFinding, version: RequirementVersion, evidence_ids: list[uuid.UUID]
) -> str:
    return finding_hash(
        project_id=str(finding.project_id),
        requirement_version_id=str(finding.requirement_version_id),
        version_content_hash=version.content_hash,
        category=str(finding.category),
        family=str(finding.family),
        derived_requirement=finding.derived_requirement,
        proposed_risk_level=finding.proposed_risk_level,
        risk_level=str(finding.risk_level),
        risk_rules_version=finding.risk_rules_version,
        evidence_ids=[str(e) for e in evidence_ids],
    )


@dataclass(frozen=True)
class GatedSubject:
    """A P6 approval subject as the approval service sees it."""

    row: ComplianceMapping | SecurityPrivacyFinding
    version: RequirementVersion
    current_hash: str

    @property
    def authored_by(self) -> uuid.UUID:
        """Whom the no-self-approval check compares the decider against.

        For an interpretation of a requirement, that is the author of the
        requirement version being interpreted.
        """
        return self.version.created_by or uuid.UUID(int=0)


class AnalysisGateService:
    """Binding and settlement of G2/G3 decisions. Called only by the approval service."""

    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._mappings = ComplianceMappingRepository(session, actor)
        self._findings = SecurityFindingRepository(session, actor)
        self._gaps = ComplianceGapRepository(session, actor)
        self._versions = RequirementVersionRepository(session, actor)
        self._requirements = RequirementRepository(session, actor)
        self._audit = AuditService(session)

    def subject(self, project_id: ProjectId, task: ApprovalTask) -> GatedSubject:
        """Load and re-bind the subject of a G2/G3 task, refusing a stale one."""
        expected_gate = GATED_ANALYSIS_SUBJECTS.get(task.subject_type)
        if expected_gate is None or task.gate is not expected_gate:
            raise ReqPilotError(
                f"a {task.subject_type} subject is decided only at {expected_gate}, not {task.gate}"
            )
        row: ComplianceMapping | SecurityPrivacyFinding | None
        if task.subject_type == MAPPING_SUBJECT:
            row = self._mappings.get(project_id, task.subject_id)
        else:
            row = self._findings.get(project_id, task.subject_id)
        if row is None:
            raise ReqPilotError("the approval subject no longer exists in this project")
        version = self._versions.get(project_id, row.requirement_version_id)
        requirement = (
            self._requirements.get(project_id, version.requirement_id) if version else None
        )
        if version is None or requirement is None:
            raise ReqPilotError("the approval subject's requirement version no longer exists")
        if requirement.current_version_id != version.id or version.state in _NOT_CURRENT:
            raise StaleApprovalError(
                "this interpretation was made for a requirement version that is no longer "
                "current; a later version needs its own analysis and its own gate"
            )
        if isinstance(row, ComplianceMapping):
            current = recompute_mapping_hash(
                row, version, self._mappings.evidence_ids(project_id, row.id)
            )
        else:
            current = recompute_finding_hash(
                row, version, self._findings.evidence_ids(project_id, row.id)
            )
        return GatedSubject(row=row, version=version, current_hash=current)

    def settle(
        self,
        project_id: ProjectId,
        task: ApprovalTask,
        decision: ApprovalDecisionType,
        record: ApprovalDecision,
    ) -> None:
        """Apply an APPROVE or REJECT to the subject. MODIFY leaves it pending."""
        if decision is ApprovalDecisionType.MODIFY:
            return
        subject = self.subject(project_id, task)
        approved = decision is ApprovalDecisionType.APPROVE
        row = subject.row
        if isinstance(row, ComplianceMapping):
            self._mappings.record_outcome(
                row,
                ComplianceMappingStatus.APPROVED if approved else ComplianceMappingStatus.REJECTED,
            )
            event = AuditEventType.COMPLIANCE_MAPPING_REVIEWED
            if not approved:
                self._rejection_gap(project_id, row)
        else:
            self._findings.record_outcome(
                row, SecurityFindingStatus.APPROVED if approved else SecurityFindingStatus.REJECTED
            )
            event = AuditEventType.SECURITY_FINDING_REVIEWED
        self._audit.append(
            event_type=event,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type=task.subject_type,
            subject_id=str(row.id),
            payload={
                "gate": str(task.gate),
                "task_id": str(task.id),
                "decision_id": str(record.id),
                "decision": str(decision),
                "status": str(row.status),
                "requirement_version_id": str(row.requirement_version_id),
                "subject_version_hash": record.subject_version_hash,
            },
        )

    def _rejection_gap(self, project_id: ProjectId, mapping: ComplianceMapping) -> None:
        """M.3 G2 "on reject: mapping discarded, gap recorded" - unless another covers it."""
        from reqpilot.domain.compliance.gaps import COVERING_STATUSES
        from reqpilot.domain.enums import COVERING_RELATIONSHIPS

        still_covered = any(
            m.id != mapping.id
            and m.control_key == mapping.control_key
            and m.checklist_jurisdiction == mapping.checklist_jurisdiction
            and m.relationship in COVERING_RELATIONSHIPS
            and m.status in COVERING_STATUSES
            for m in self._mappings.list_for_project(project_id)
        )
        if still_covered or mapping.graph_run_id is None:
            return
        gap = self._gaps.add_rejection_gap(
            ComplianceGap(
                project_id=project_id,
                graph_run_id=mapping.graph_run_id,
                control_key=mapping.control_key,
                control_title=mapping.control_title,
                obligation_kind=mapping.obligation_kind,
                is_high_impact=mapping.is_high_impact,
                checklist_ref=mapping.checklist_ref,
                checklist_domain=mapping.checklist_domain,
                checklist_jurisdiction=mapping.checklist_jurisdiction,
                origin=ComplianceGapOrigin.G2_REJECTION,
                reason=(
                    "the Compliance Officer rejected the only candidate mapping that covered "
                    "this expected control at G2"
                ),
                related_mapping_id=mapping.id,
                recorded_by=self._actor.actor_id,
            )
        )
        self._audit.append(
            event_type=AuditEventType.COMPLIANCE_GAP_FOUND,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="compliance_gap",
            subject_id=str(gap.id),
            graph_run_id=mapping.graph_run_id,
            payload={
                "control_key": gap.control_key,
                "origin": str(gap.origin),
                "related_mapping_id": str(mapping.id),
                "checklist_ref": gap.checklist_ref,
            },
        )
