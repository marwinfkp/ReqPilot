"""Repositories for the risk register (P7; architecture G.6).

Every method authorises the actor for the action in the row's project before it
touches the session (the second layer of ADR-009).

There is deliberately **no method that writes a severity**. ``add`` takes a
fully-formed :class:`~reqpilot.domain.models.risk.Risk` whose severity the
engine computed from the matrix, the ORM guard refuses any later change to it,
and the database's composite foreign key refuses a severity that is not the
matrix's value for the row's own cell. A caller that wanted to force one would
have to change the approved matrix, which is versioned, seeded and asserted.

There is also no delete: risk history is kept, and a risk that should not have
been raised is *rejected* by a human with a recorded rationale, not erased.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select

from reqpilot.domain.enums import (
    UNREVIEWED_RISK_STATUSES,
    Action,
    MitigationStatus,
    ResourceType,
    RiskCategory,
    RiskScope,
    RiskSeverity,
    RiskStatus,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.base import utc_now
from reqpilot.domain.models.risk import Risk, RiskEvidence, RiskMitigation
from reqpilot.repositories.base import ProjectScopedRepository


class RiskRepository(ProjectScopedRepository[Risk]):
    resource_type = ResourceType.RISK

    def add(
        self,
        risk: Risk,
        evidence_ids: Sequence[uuid.UUID],
        mitigations: Sequence[RiskMitigation] = (),
    ) -> Risk:
        """A validated, rated risk with its evidence links and suggestions, in one flush."""
        project_id = ProjectId(risk.project_id)
        self.authorize(Action.RISK_ANALYSE, project_id)
        if not evidence_ids or len(set(evidence_ids)) != risk.evidence_count:
            raise ValueError("a risk's evidence links must match its evidence count")
        self._session.add(risk)
        self._session.flush()
        for evidence_id in dict.fromkeys(evidence_ids):
            self._session.add(
                RiskEvidence(project_id=project_id, risk_id=risk.id, evidence_id=evidence_id)
            )
        for mitigation in mitigations:
            mitigation.project_id = project_id
            mitigation.risk_id = risk.id
            self._session.add(mitigation)
        self._session.flush()
        return risk

    def link_task(self, risk: Risk, task_id: uuid.UUID) -> None:
        self.authorize(Action.GATE_TASK_RAISE, ProjectId(risk.project_id))
        risk.approval_task_id = task_id
        self._session.flush()

    def record_status(
        self,
        risk: Risk,
        status: RiskStatus,
        *,
        rationale: str | None = None,
        decided_by: uuid.UUID | None = None,
    ) -> None:
        """Move a risk along its approved status path (architecture I.4).

        The write is authorised by the G8 decision that has just been recorded,
        or by the human's ``RISK_MANAGE`` action, so the repository checks only
        that the caller may read the register in this project - exactly as the
        P1 gate-consequent transition does.
        """
        self.authorize(Action.RISK_READ, ProjectId(risk.project_id))
        risk.status = status
        if rationale is not None:
            risk.decision_rationale = rationale
        if decided_by is not None:
            risk.decided_by = decided_by
            risk.decided_at = utc_now()
        self._session.flush()

    def get(self, project_id: ProjectId, risk_id: uuid.UUID) -> Risk | None:
        self.authorize(Action.RISK_READ, project_id)
        stmt = select(Risk).where(Risk.id == risk_id)
        return self._session.scalars(self.scoped(stmt, Risk.project_id, project_id)).first()

    def list_for_project(
        self,
        project_id: ProjectId,
        *,
        version_id: uuid.UUID | None = None,
        graph_run_id: uuid.UUID | None = None,
        scope: RiskScope | None = None,
        category: RiskCategory | None = None,
        severity: RiskSeverity | None = None,
    ) -> list[Risk]:
        self.authorize(Action.RISK_READ, project_id)
        stmt = self.scoped(select(Risk), Risk.project_id, project_id)
        if version_id is not None:
            stmt = stmt.where(Risk.requirement_version_id == version_id)
        if graph_run_id is not None:
            stmt = stmt.where(Risk.graph_run_id == graph_run_id)
        if scope is not None:
            stmt = stmt.where(Risk.scope == scope)
        if category is not None:
            stmt = stmt.where(Risk.category == category)
        if severity is not None:
            stmt = stmt.where(Risk.severity == severity)
        return list(self._session.scalars(stmt.order_by(Risk.created_at, Risk.title_key)))

    def active_for(
        self, project_id: ProjectId, version_id: uuid.UUID | None, title_key: str
    ) -> Risk | None:
        """The live risk with this title for this subject, if one exists."""
        for risk in self.list_for_project(project_id, version_id=version_id):
            if risk.title_key == title_key and risk.status is not RiskStatus.REJECTED:
                if version_id is None and risk.scope is not RiskScope.PROJECT:
                    continue
                return risk
        return None

    def active_project_risk(self, project_id: ProjectId, title_key: str) -> Risk | None:
        for risk in self.list_for_project(project_id, scope=RiskScope.PROJECT):
            if risk.title_key == title_key and risk.status is not RiskStatus.REJECTED:
                return risk
        return None

    def unreviewed_high_count(self, project_id: ProjectId, version_id: uuid.UUID) -> int:
        """HIGH risks on this exact version that no human has decided yet.

        What the ``ANALYZED -> VALIDATED`` guard counts (``FR-RSK-007``;
        architecture I.5, H.3). Read from the persisted severity and status, so
        a high risk blocks even if its G8 task were somehow missing: fail closed.
        """
        self.authorize(Action.RISK_READ, project_id)
        stmt = self.scoped(select(Risk), Risk.project_id, project_id).where(
            Risk.requirement_version_id == version_id,
            Risk.severity == RiskSeverity.HIGH,
            Risk.status.in_(tuple(UNREVIEWED_RISK_STATUSES)),
        )
        return len(list(self._session.scalars(stmt)))

    def evidence_ids(self, project_id: ProjectId, risk_id: uuid.UUID) -> list[uuid.UUID]:
        self.authorize(Action.RISK_READ, project_id)
        stmt = select(RiskEvidence.evidence_id).where(
            RiskEvidence.risk_id == risk_id, RiskEvidence.project_id == project_id
        )
        return list(self._session.scalars(stmt.order_by(RiskEvidence.created_at)))


class RiskMitigationRepository(ProjectScopedRepository[RiskMitigation]):
    resource_type = ResourceType.RISK_MITIGATION

    def get(self, project_id: ProjectId, mitigation_id: uuid.UUID) -> RiskMitigation | None:
        self.authorize(Action.RISK_READ, project_id)
        stmt = select(RiskMitigation).where(RiskMitigation.id == mitigation_id)
        return self._session.scalars(
            self.scoped(stmt, RiskMitigation.project_id, project_id)
        ).first()

    def list_for_risk(self, project_id: ProjectId, risk_id: uuid.UUID) -> list[RiskMitigation]:
        self.authorize(Action.RISK_READ, project_id)
        stmt = self.scoped(select(RiskMitigation), RiskMitigation.project_id, project_id).where(
            RiskMitigation.risk_id == risk_id
        )
        return list(self._session.scalars(stmt.order_by(RiskMitigation.created_at)))

    def list_for_project(self, project_id: ProjectId) -> list[RiskMitigation]:
        self.authorize(Action.RISK_READ, project_id)
        stmt = self.scoped(select(RiskMitigation), RiskMitigation.project_id, project_id)
        return list(self._session.scalars(stmt.order_by(RiskMitigation.created_at)))

    def add_human(self, mitigation: RiskMitigation) -> RiskMitigation:
        """A mitigation a human wrote (``FR-RSK-010``). Never AI-generated."""
        self.authorize(Action.RISK_MANAGE, ProjectId(mitigation.project_id))
        mitigation.is_ai_generated = False
        mitigation.status = MitigationStatus.ACCEPTED
        mitigation.accepted_at = utc_now()
        self._session.add(mitigation)
        self._session.flush()
        return mitigation

    def decide(
        self,
        mitigation: RiskMitigation,
        status: MitigationStatus,
        *,
        actor_id: uuid.UUID,
        rationale: str | None,
    ) -> RiskMitigation:
        """A human accepting or rejecting an AI-suggested mitigation (``FR-RSK-005``)."""
        self.authorize(Action.RISK_MANAGE, ProjectId(mitigation.project_id))
        mitigation.status = status
        mitigation.decision_rationale = rationale
        if status is MitigationStatus.ACCEPTED:
            mitigation.accepted_by = actor_id
            mitigation.accepted_at = utc_now()
        self._session.flush()
        return mitigation
