"""Raising the P8 gates from persisted facts (architecture C.3 ``gate_fanout``, M.3).

Raising is not deciding. This service creates the G4, G5 and G7 tasks that the
persisted state requires and that do not exist yet - idempotently, so running it
twice raises nothing the second time - and records an analyst's
architecture-critical flag. A human in the gate's role decides every task,
through the approval service.

* **G4** - every *resolved* conflict that involves a stakeholder disagreement
  (P5 records the flag deterministically from the sources) gets an Analyst task
  and one task per affected stakeholder, bound to the recorded resolution.
* **G5** - every live requirement version the M.3 predicate selects, or that an
  analyst flagged, gets a Project Manager task bound to its exact hash.
* **G7** - P1 raises G7 when a successor of the *current* approved version is
  created. P8 closes the gap where the current version had been withdrawn: any
  live version of a requirement that has an approved history, without a G7
  task, gets one (Analyst + Compliance Officer, co-approval).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    Action,
    AuditEventType,
    ConflictStatus,
    Gate,
    ResourceType,
    Role,
)
from reqpilot.domain.errors import ApprovalError, ReqPilotError
from reqpilot.domain.ids import ProjectId, new_task_group_id
from reqpilot.domain.lifecycle.states import PRE_APPROVAL_STATES
from reqpilot.domain.models.approval import ApprovalTask
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.repositories.quality import ConflictRepository
from reqpilot.repositories.requirements import (
    RequirementRepository,
    RequirementVersionRepository,
)
from reqpilot.services.audit import AuditService
from reqpilot.services.governance.gates import VERSION_SUBJECT, ConflictGateRaiser
from reqpilot.services.governance.readiness import GovernanceReadinessService

MAX_REASON_CHARS = 2000


@dataclass
class FanOutResult:
    g4: list[ApprovalTask] = field(default_factory=list)
    g5: list[ApprovalTask] = field(default_factory=list)
    g7: list[ApprovalTask] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.g4) + len(self.g5) + len(self.g7)


class GovernanceFanOut:
    def __init__(self, session: Session, actor: Actor, *, g5_threshold: float | None = None):
        self._session = session
        self._actor = actor
        self._readiness = GovernanceReadinessService(session, actor, g5_threshold=g5_threshold)
        self._conflicts = ConflictRepository(session, actor)
        self._requirements = RequirementRepository(session, actor)
        self._versions = RequirementVersionRepository(session, actor)
        self._audit = AuditService(session)

    def _live_versions(self, project_id: ProjectId) -> list[RequirementVersion]:
        """The current version of every requirement, when it is still being worked on."""
        out: list[RequirementVersion] = []
        for requirement in self._requirements.list_for_project(project_id):
            if requirement.current_version_id is None:
                continue
            version = self._versions.get(project_id, requirement.current_version_id)
            if version is not None and version.state in PRE_APPROVAL_STATES:
                out.append(version)
        return sorted(out, key=lambda v: (str(v.requirement_id), v.version_no))

    def raise_required(self, project_id: ProjectId) -> FanOutResult:
        """Raise every G4/G5/G7 task the persisted state requires and lacks."""
        require(
            self._actor,
            Action.GATE_TASK_RAISE,
            ResourceRef(resource_type=ResourceType.APPROVAL_TASK, project_id=project_id),
        )
        result = FanOutResult()
        raiser = ConflictGateRaiser(self._session, self._actor)
        for conflict in self._conflicts.list_for_project(
            project_id, status=ConflictStatus.RESOLVED
        ):
            result.g4.extend(raiser.raise_for(project_id, conflict))

        for version in self._live_versions(project_id):
            g5 = self._readiness.architecture_state(project_id, version)
            if g5.required and not g5.raised:
                result.g5.append(self._raise_g5(project_id, version))
            g7 = self._readiness.change_state(project_id, version)
            if g7.required and not g7.raised:
                result.g7.extend(self._raise_g7(project_id, version))
        return result

    def flag_architecture_critical(
        self, project_id: ProjectId, version_id: uuid.UUID, reason: str
    ) -> ApprovalTask:
        """An analyst's flag (architecture M.3 "... or analyst flag"). Raises G5."""
        require(
            self._actor,
            Action.ARCHITECTURE_FLAG,
            ResourceRef(resource_type=ResourceType.REQUIREMENT_VERSION, project_id=project_id),
        )
        cleaned = " ".join((reason or "").split())
        if not cleaned:
            raise ReqPilotError("flagging a requirement as architecture-critical needs a reason")
        if len(cleaned) > MAX_REASON_CHARS:
            raise ReqPilotError("the reason is too long")
        version = self._versions.get(project_id, version_id)
        if version is None:
            raise ReqPilotError("requirement version not found in this project")
        if version.state not in PRE_APPROVAL_STATES:
            raise ApprovalError(
                f"only a version still being worked on can be flagged; this one is {version.state}"
            )
        state = self._readiness.architecture_state(project_id, version)
        if state.open_tasks or state.passed:
            raise ApprovalError("G5 is already raised or passed for this exact version")
        task = self._raise_g5(project_id, version)
        self._audit.append(
            event_type=AuditEventType.ARCHITECTURE_CRITICAL_FLAGGED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type=VERSION_SUBJECT,
            subject_id=str(version.id),
            subject_version=str(version.version_no),
            payload={"task_id": str(task.id), "reason_supplied": True},
        )
        return task

    # ------------------------------------------------------------------
    def _raise_g5(self, project_id: ProjectId, version: RequirementVersion) -> ApprovalTask:
        from reqpilot.services.approval.service import ApprovalService, tasks_required_for

        (role,) = tasks_required_for(Gate.G5_ARCHITECTURE_CRITICAL)
        return ApprovalService(self._session, self._actor).create_task(
            project_id=project_id,
            gate=Gate.G5_ARCHITECTURE_CRITICAL,
            subject_type=VERSION_SUBJECT,
            subject_id=version.id,
            subject_version=str(version.version_no),
            subject_version_hash=version.content_hash,
            required_role=role,
            task_group_id=new_task_group_id(),
            _action=Action.GATE_TASK_RAISE,
        )

    def _raise_g7(self, project_id: ProjectId, version: RequirementVersion) -> list[ApprovalTask]:
        from reqpilot.services.approval.service import ApprovalService, tasks_required_for

        approvals = ApprovalService(self._session, self._actor)
        group = new_task_group_id()
        roles: tuple[Role, ...] = tasks_required_for(Gate.G7_APPROVED_REQUIREMENT_CHANGE)
        return [
            approvals.create_task(
                project_id=project_id,
                gate=Gate.G7_APPROVED_REQUIREMENT_CHANGE,
                subject_type=VERSION_SUBJECT,
                subject_id=version.id,
                subject_version=str(version.version_no),
                subject_version_hash=version.content_hash,
                required_role=role,
                task_group_id=group,
                _action=Action.GATE_TASK_RAISE,
            )
            for role in roles
        ]
