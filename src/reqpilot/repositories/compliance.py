"""Repositories for compliance mappings, gaps and security/privacy findings (P6; G.6).

Every method authorises the actor for the action in the row's project before it
touches the session (the second layer of ADR-009). There is no method that
deletes a row, rewrites a mapping's content or writes a risk level: the only
mutations are linking a gate task once and recording a G2/G3 outcome, and the
ORM guard and the database triggers refuse anything else.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select

from reqpilot.domain.enums import (
    Action,
    ComplianceMappingStatus,
    ResourceType,
    SecurityFindingStatus,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.compliance import (
    ComplianceGap,
    ComplianceMapping,
    ComplianceMappingEvidence,
    SecurityPrivacyFinding,
    SecurityPrivacyFindingEvidence,
)
from reqpilot.domain.models.runs import GraphRun
from reqpilot.repositories.base import ProjectScopedRepository

#: How a G2/G3 outcome is recorded on the subject. The write is authorised by
#: the decision that was just recorded (as the P1 gate-consequent transition is),
#: so the repository checks only that the decider may read the subject.
_GATE_OUTCOME_ACTION = {
    ResourceType.COMPLIANCE_MAPPING: Action.COMPLIANCE_READ,
    ResourceType.SECURITY_PRIVACY_FINDING: Action.SECURITY_READ,
}


class ComplianceMappingRepository(ProjectScopedRepository[ComplianceMapping]):
    resource_type = ResourceType.COMPLIANCE_MAPPING

    def add(
        self, mapping: ComplianceMapping, evidence_ids: Sequence[uuid.UUID]
    ) -> ComplianceMapping:
        """A validated mapping and its evidence links, in one flush."""
        project_id = ProjectId(mapping.project_id)
        self.authorize(Action.COMPLIANCE_ANALYSE, project_id)
        if not evidence_ids or len(set(evidence_ids)) != mapping.evidence_count:
            raise ValueError("a mapping's evidence links must match its evidence count")
        self._session.add(mapping)
        self._session.flush()
        for evidence_id in dict.fromkeys(evidence_ids):
            self._session.add(
                ComplianceMappingEvidence(
                    project_id=project_id, mapping_id=mapping.id, evidence_id=evidence_id
                )
            )
        self._session.flush()
        return mapping

    def link_task(self, mapping: ComplianceMapping, task_id: uuid.UUID) -> None:
        self.authorize(Action.GATE_TASK_RAISE, ProjectId(mapping.project_id))
        mapping.approval_task_id = task_id
        self._session.flush()

    def record_outcome(self, mapping: ComplianceMapping, status: ComplianceMappingStatus) -> None:
        self.authorize(_GATE_OUTCOME_ACTION[self.resource_type], ProjectId(mapping.project_id))
        mapping.status = status
        self._session.flush()

    def get(self, project_id: ProjectId, mapping_id: uuid.UUID) -> ComplianceMapping | None:
        self.authorize(Action.COMPLIANCE_READ, project_id)
        stmt = select(ComplianceMapping).where(ComplianceMapping.id == mapping_id)
        return self._session.scalars(
            self.scoped(stmt, ComplianceMapping.project_id, project_id)
        ).first()

    def list_for_project(
        self,
        project_id: ProjectId,
        *,
        version_id: uuid.UUID | None = None,
        graph_run_id: uuid.UUID | None = None,
    ) -> list[ComplianceMapping]:
        self.authorize(Action.COMPLIANCE_READ, project_id)
        stmt = self.scoped(select(ComplianceMapping), ComplianceMapping.project_id, project_id)
        if version_id is not None:
            stmt = stmt.where(ComplianceMapping.requirement_version_id == version_id)
        if graph_run_id is not None:
            stmt = stmt.where(ComplianceMapping.graph_run_id == graph_run_id)
        return list(
            self._session.scalars(
                stmt.order_by(ComplianceMapping.created_at, ComplianceMapping.control_key)
            )
        )

    def active_for(
        self, project_id: ProjectId, version_id: uuid.UUID, control_key: str
    ) -> ComplianceMapping | None:
        """The mapping of this version to this control that is not G2-rejected."""
        for mapping in self.list_for_project(project_id, version_id=version_id):
            if (
                mapping.control_key == control_key
                and mapping.status is not ComplianceMappingStatus.REJECTED
            ):
                return mapping
        return None

    def evidence_ids(self, project_id: ProjectId, mapping_id: uuid.UUID) -> list[uuid.UUID]:
        self.authorize(Action.COMPLIANCE_READ, project_id)
        stmt = select(ComplianceMappingEvidence.evidence_id).where(
            ComplianceMappingEvidence.mapping_id == mapping_id,
            ComplianceMappingEvidence.project_id == project_id,
        )
        return list(self._session.scalars(stmt.order_by(ComplianceMappingEvidence.created_at)))

    def pending_count(self, project_id: ProjectId, version_id: uuid.UUID) -> int:
        """High-impact interpretations of this version still awaiting G2."""
        return sum(
            1
            for m in self.list_for_project(project_id, version_id=version_id)
            if m.status is ComplianceMappingStatus.PENDING_REVIEW
        )


class ComplianceGapRepository(ProjectScopedRepository[ComplianceGap]):
    resource_type = ResourceType.COMPLIANCE_GAP

    def add(self, gap: ComplianceGap) -> ComplianceGap:
        self.authorize(Action.COMPLIANCE_ANALYSE, ProjectId(gap.project_id))
        self._session.add(gap)
        self._session.flush()
        return gap

    def add_rejection_gap(self, gap: ComplianceGap) -> ComplianceGap:
        """A gap recorded because G2 rejected a mapping (M.3: "gap recorded")."""
        self.authorize(Action.COMPLIANCE_READ, ProjectId(gap.project_id))
        self._session.add(gap)
        self._session.flush()
        return gap

    def list_for_run(self, project_id: ProjectId, graph_run_id: uuid.UUID) -> list[ComplianceGap]:
        self.authorize(Action.COMPLIANCE_READ, project_id)
        stmt = select(ComplianceGap).where(ComplianceGap.graph_run_id == graph_run_id)
        stmt = self.scoped(stmt, ComplianceGap.project_id, project_id)
        return list(self._session.scalars(stmt.order_by(ComplianceGap.created_at)))

    def latest_run_id(self, project_id: ProjectId) -> uuid.UUID | None:
        """The most recent run that recorded gap analysis for the project."""
        self.authorize(Action.COMPLIANCE_READ, project_id)
        stmt = (
            select(ComplianceGap.graph_run_id)
            .join(GraphRun, GraphRun.id == ComplianceGap.graph_run_id)
            .where(ComplianceGap.project_id == project_id)
            .order_by(GraphRun.started_at.desc(), ComplianceGap.created_at.desc())
            .limit(1)
        )
        return self._session.scalars(stmt).first()


class SecurityFindingRepository(ProjectScopedRepository[SecurityPrivacyFinding]):
    resource_type = ResourceType.SECURITY_PRIVACY_FINDING

    def add(
        self, finding: SecurityPrivacyFinding, evidence_ids: Sequence[uuid.UUID]
    ) -> SecurityPrivacyFinding:
        project_id = ProjectId(finding.project_id)
        self.authorize(Action.SECURITY_ANALYSE, project_id)
        if len(set(evidence_ids)) != finding.evidence_count:
            raise ValueError("a finding's evidence links must match its evidence count")
        self._session.add(finding)
        self._session.flush()
        for evidence_id in dict.fromkeys(evidence_ids):
            self._session.add(
                SecurityPrivacyFindingEvidence(
                    project_id=project_id, finding_id=finding.id, evidence_id=evidence_id
                )
            )
        self._session.flush()
        return finding

    def link_task(self, finding: SecurityPrivacyFinding, task_id: uuid.UUID) -> None:
        self.authorize(Action.GATE_TASK_RAISE, ProjectId(finding.project_id))
        finding.approval_task_id = task_id
        self._session.flush()

    def record_outcome(
        self, finding: SecurityPrivacyFinding, status: SecurityFindingStatus
    ) -> None:
        self.authorize(_GATE_OUTCOME_ACTION[self.resource_type], ProjectId(finding.project_id))
        finding.status = status
        self._session.flush()

    def get(self, project_id: ProjectId, finding_id: uuid.UUID) -> SecurityPrivacyFinding | None:
        self.authorize(Action.SECURITY_READ, project_id)
        stmt = select(SecurityPrivacyFinding).where(SecurityPrivacyFinding.id == finding_id)
        return self._session.scalars(
            self.scoped(stmt, SecurityPrivacyFinding.project_id, project_id)
        ).first()

    def list_for_project(
        self,
        project_id: ProjectId,
        *,
        version_id: uuid.UUID | None = None,
        graph_run_id: uuid.UUID | None = None,
    ) -> list[SecurityPrivacyFinding]:
        self.authorize(Action.SECURITY_READ, project_id)
        stmt = self.scoped(
            select(SecurityPrivacyFinding), SecurityPrivacyFinding.project_id, project_id
        )
        if version_id is not None:
            stmt = stmt.where(SecurityPrivacyFinding.requirement_version_id == version_id)
        if graph_run_id is not None:
            stmt = stmt.where(SecurityPrivacyFinding.graph_run_id == graph_run_id)
        return list(
            self._session.scalars(
                stmt.order_by(SecurityPrivacyFinding.created_at, SecurityPrivacyFinding.family)
            )
        )

    def active_for(
        self, project_id: ProjectId, version_id: uuid.UUID, family: str
    ) -> SecurityPrivacyFinding | None:
        for finding in self.list_for_project(project_id, version_id=version_id):
            if (
                str(finding.family) == str(family)
                and finding.status is not SecurityFindingStatus.REJECTED
            ):
                return finding
        return None

    def evidence_ids(self, project_id: ProjectId, finding_id: uuid.UUID) -> list[uuid.UUID]:
        self.authorize(Action.SECURITY_READ, project_id)
        stmt = select(SecurityPrivacyFindingEvidence.evidence_id).where(
            SecurityPrivacyFindingEvidence.finding_id == finding_id,
            SecurityPrivacyFindingEvidence.project_id == project_id,
        )
        return list(self._session.scalars(stmt))

    def pending_count(self, project_id: ProjectId, version_id: uuid.UUID) -> int:
        """High-risk derived requirements of this version still awaiting G3."""
        return sum(
            1
            for f in self.list_for_project(project_id, version_id=version_id)
            if f.status is SecurityFindingStatus.PENDING_REVIEW
        )
