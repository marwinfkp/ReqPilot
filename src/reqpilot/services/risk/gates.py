"""G8 as a subject of the P1 approval service (architecture I.5, M.3; ``FR-RSK-007``).

The approval service stays the **only** decision path. This module supplies what
it needs for the risk subject type, exactly as ``services/compliance/gates.py``
does for G2 and G3:

* **Binding.** The risk's content hash, recomputed from the persisted row (and,
  for a requirement-level risk, its requirement version) at decision time. A G8
  decision is refused (``StaleApprovalError``) when the requirement has moved on
  to another version, when the version is no longer being analysed, or when the
  recomputed hash no longer equals the one captured when the task was raised.
* **Settlement.** Approve -> the risk is ``ACCEPTED``; reject -> ``REJECTED``.
  Either way the risk leaves ``UNDER_REVIEW``, which is what unblocks
  ``ANALYZED -> VALIDATED`` for its requirement. A G8 decision **never** moves a
  requirement's lifecycle state and never approves or baselines a requirement:
  that is G1's. It decides the *risk*, and the baseline becomes reachable as a
  consequence rather than as a grant.

G8 is an additional ``[PROJ]`` gate with no problem-statement counterpart
(approved Phase 0 C14/C19): it exists because risk analysis is in the MVP. Its
required role is the Security Reviewer, from ``GATE_REQUIRED_ROLES`` - one
source of truth, no local copy. Phase 0 F.1 names the Risk Owner as the role
that owns the register and notes that in a small team it may be the same person
as the Security Reviewer or the PM; the *gate* is decided by the Security
Reviewer, and the register's ``owner_role`` records who owns the entry.

Nothing here reads model output. The gate fired from a persisted, deterministically
computed column; the human's decision is the only thing that clears it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    ApprovalDecisionType,
    AuditEventType,
    Gate,
    ResourceType,
    RiskScope,
    RiskStatus,
)
from reqpilot.domain.errors import ReqPilotError, StaleApprovalError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.lifecycle.states import TERMINAL_STATES
from reqpilot.domain.models.approval import ApprovalDecision, ApprovalTask
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.domain.models.risk import Risk
from reqpilot.domain.policy import Actor
from reqpilot.domain.risk.hashing import risk_hash
from reqpilot.repositories.requirements import (
    RequirementRepository,
    RequirementVersionRepository,
)
from reqpilot.repositories.risk import RiskRepository
from reqpilot.services.audit import AuditService

RISK_SUBJECT = ResourceType.RISK.value

#: The P7 approval subject and the one gate it is decided at.
RISK_GATED_SUBJECTS: dict[str, Gate] = {RISK_SUBJECT: Gate.G8_HIGH_SEVERITY_RISK}

#: A version in one of these states is not being analysed: a decision about a
#: risk of it would be about history, and is refused as stale.
_NOT_CURRENT: frozenset[RequirementState] = TERMINAL_STATES | {RequirementState.REJECTED}


def recompute_risk_hash(
    risk: Risk, version: RequirementVersion | None, evidence_ids: list[uuid.UUID]
) -> str:
    """The risk's current content hash - what a G8 decision must still match.

    The evidence links are part of it, as they are for a P6 mapping: a risk that
    lost or gained a citation is not the risk the reviewer was shown.
    """
    return risk_hash(
        project_id=str(risk.project_id),
        scope=str(risk.scope),
        requirement_version_id=str(risk.requirement_version_id)
        if risk.requirement_version_id
        else None,
        version_content_hash=version.content_hash if version else None,
        category=str(risk.category),
        title=risk.title,
        description=risk.description,
        likelihood=str(risk.likelihood),
        impact=str(risk.impact),
        severity=str(risk.severity),
        matrix_version=risk.matrix_version,
        likelihood_rationale=risk.likelihood_rationale,
        impact_rationale=risk.impact_rationale,
        evidence_ids=[str(e) for e in evidence_ids],
    )


@dataclass(frozen=True)
class GatedRisk:
    """A risk as the approval service sees it."""

    row: Risk
    version: RequirementVersion | None
    current_hash: str

    @property
    def authored_by(self) -> uuid.UUID:
        """Whom the no-self-approval check compares the decider against.

        For a requirement-level risk that is the author of the version the risk
        was identified from; for a project-level risk, whoever recorded it -
        which is the analyst the pipeline acted for. Either way the person whose
        work produced the risk cannot be the person who signs it off.
        """
        if self.version is not None:
            return self.version.created_by or uuid.UUID(int=0)
        return self.row.recorded_by


class RiskGateService:
    """Binding and settlement of G8 decisions. Called only by the approval service."""

    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._risks = RiskRepository(session, actor)
        self._versions = RequirementVersionRepository(session, actor)
        self._requirements = RequirementRepository(session, actor)
        self._audit = AuditService(session)

    def subject(self, project_id: ProjectId, task: ApprovalTask) -> GatedRisk:
        """Load and re-bind the subject of a G8 task, refusing a stale one."""
        expected_gate = RISK_GATED_SUBJECTS.get(task.subject_type)
        if expected_gate is None or task.gate is not expected_gate:
            raise ReqPilotError(
                f"a {task.subject_type} subject is decided only at {expected_gate}, not {task.gate}"
            )
        risk = self._risks.get(project_id, task.subject_id)
        if risk is None:
            raise ReqPilotError("the approval subject no longer exists in this project")

        version: RequirementVersion | None = None
        if risk.scope is RiskScope.REQUIREMENT and risk.requirement_version_id is not None:
            version = self._versions.get(project_id, risk.requirement_version_id)
            requirement = (
                self._requirements.get(project_id, version.requirement_id) if version else None
            )
            if version is None or requirement is None:
                raise ReqPilotError("the approval subject's requirement version no longer exists")
            if requirement.current_version_id != version.id or version.state in _NOT_CURRENT:
                raise StaleApprovalError(
                    "this risk was identified for a requirement version that is no longer "
                    "current; a later version needs its own analysis and its own gate"
                )
        return GatedRisk(
            row=risk,
            version=version,
            current_hash=recompute_risk_hash(
                risk, version, self._risks.evidence_ids(project_id, risk.id)
            ),
        )

    def settle(
        self,
        project_id: ProjectId,
        task: ApprovalTask,
        decision: ApprovalDecisionType,
        record: ApprovalDecision,
    ) -> None:
        """Apply an APPROVE or REJECT to the risk. MODIFY leaves it under review."""
        if decision is ApprovalDecisionType.MODIFY:
            return
        subject = self.subject(project_id, task)
        risk = subject.row
        approved = decision is ApprovalDecisionType.APPROVE
        # Approving at G8 means "this risk has been reviewed and is accepted as
        # analysed"; rejecting means "this is not a risk as recorded". Either
        # way a human has looked, which is precisely what FR-RSK-007 requires
        # before the requirement can be baselined.
        self._risks.record_status(
            risk,
            RiskStatus.ACCEPTED if approved else RiskStatus.REJECTED,
            rationale=record.justification,
            decided_by=self._actor.actor_id,
        )
        self._audit.append(
            event_type=AuditEventType.RISK_DECISION_RECORDED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type=RISK_SUBJECT,
            subject_id=str(risk.id),
            subject_version=task.subject_version,
            payload={
                "gate": str(task.gate),
                "task_id": str(task.id),
                "decision_id": str(record.id),
                "decision": str(decision),
                "status": str(risk.status),
                "severity": str(risk.severity),
                "scope": str(risk.scope),
                "requirement_version_id": str(risk.requirement_version_id)
                if risk.requirement_version_id
                else None,
                "subject_version_hash": record.subject_version_hash,
            },
        )

    def unreviewed_high_count(self, project_id: ProjectId, version_id: uuid.UUID) -> int:
        """What the ``ANALYZED -> VALIDATED`` guard counts for this exact version."""
        return self._risks.unreviewed_high_count(project_id, version_id)
