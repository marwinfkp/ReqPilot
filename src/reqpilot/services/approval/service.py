"""The approval service - the only path by which anything becomes APPROVED.

This is the code-level form of the architecture's human-in-the-loop guarantee.
Five checks stand between a request and an approval, and every one of them is
deterministic:

1. **The task is open.** A decided task cannot be reused.
2. **The policy authorises the decider.** ``policy.can(APPROVAL_DECIDE)`` is
   asked with the task's gate and the role exercised, and its answer is
   enforced. It refuses any non-human actor first, then an actor with no role in
   the project, then a role the gate does not require (``GATE_REQUIRED_ROLES``),
   then a role the actor does not hold in this project.
3. **The role is this task's own.** Each task names exactly one role, so the
   role exercised must equal the task's ``required_role``.
4. **No self-approval.** An actor may not approve a subject they authored.
5. **The version binding is exact.** The subject's current content hash must
   still equal the hash captured when the task was raised. An edit after
   submission invalidates the task rather than silently widening the approval.

Only after all five does a decision row exist, and only a decision row can move
a version to APPROVED.

**From P6, G2 and G3.** Their subjects are a compliance mapping and a derived
security/privacy finding, not a requirement version. The same five checks apply;
the binding is the subject's content hash (which includes its requirement
version's hash), recomputed at decision time, and a decision about a version that
is no longer current is refused as stale. Settling a G2/G3 decision records the
outcome on the mapping or finding and **never** moves a requirement's lifecycle
state: approving an interpretation is not approving a requirement (that is G1).

**From P7, G8.** Its subject is a risk item, and it behaves exactly as G2 and G3
do: the same five checks, the same hash binding (which covers the risk's ratings
and its computed severity), the same staleness refusal. Settling it moves the
risk out of ``UNDER_REVIEW``, which is what lets the requirement's
``ANALYZED -> VALIDATED`` guard pass - a consequence of the review, never a
grant of approval (``FR-RSK-007``; architecture I.5).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    GATE_REQUIRED_ROLES,
    GATE_REQUIRES_ALL_ROLES,
    Action,
    ApprovalDecisionType,
    ApprovalTaskStatus,
    AuditEventType,
    Gate,
    ResourceType,
    Role,
)
from reqpilot.domain.errors import (
    ApprovalError,
    ReqPilotError,
    SelfApprovalError,
    StaleApprovalError,
)
from reqpilot.domain.ids import ProjectId, new_task_group_id
from reqpilot.domain.lifecycle import RequirementState, TransitionContext, validate_transition
from reqpilot.domain.models.approval import ApprovalDecision, ApprovalTask
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.domain.versioning import hashes_match
from reqpilot.repositories.approval import ApprovalDecisionRepository, ApprovalTaskRepository
from reqpilot.repositories.requirements import RequirementVersionRepository
from reqpilot.services.audit import AuditService

if TYPE_CHECKING:  # pragma: no cover
    from reqpilot.services.compliance.gates import AnalysisGateService
    from reqpilot.services.risk.gates import RiskGateService

    AnalysisGate = AnalysisGateService | RiskGateService


@dataclass(frozen=True)
class GateOutcome:
    """What a decision did to its task and, if applicable, its group."""

    task: ApprovalTask
    decision: ApprovalDecision
    task_closed: bool
    group_complete: bool
    baseline_id: uuid.UUID | None = None


def required_roles(gate: Gate) -> frozenset[Role]:
    """The roles a gate requires. One source of truth, no local copies."""
    return GATE_REQUIRED_ROLES[gate]


def requires_all_roles(gate: Gate) -> bool:
    """Whether the gate needs every required role (co-approval) or any one."""
    return GATE_REQUIRES_ALL_ROLES[gate]


def tasks_required_for(gate: Gate) -> tuple[Role, ...]:
    """The roles that each need their own approval task for this gate.

    A co-approval gate fans out into one task per required role, all sharing a
    ``task_group_id``. That is the mechanism architecture M.3 specifies for G6,
    and G1 uses it for the same reason: ``required_role`` stays singular, so
    "who must sign this off" is a single checkable value on every task.

    A single-role gate produces exactly one task.
    """
    roles = tuple(sorted(required_roles(gate)))
    if requires_all_roles(gate):
        return roles
    if len(roles) != 1:
        raise ReqPilotError(
            f"{gate} permits any one of {list(roles)}, which has no single-task "
            "representation; give it an explicit co-approval requirement instead"
        )
    return roles


def _analysis_gate_subjects() -> dict[str, Gate]:
    """Subject type -> the one gate it is decided at, for every analysis gate.

    G2 and G3 come from P6, G8 from P7. Merged here rather than copied, so the
    approval service has one answer to "is this an analysis gate, and which one".
    """
    from reqpilot.services.compliance.gates import GATED_ANALYSIS_SUBJECTS
    from reqpilot.services.risk.gates import RISK_GATED_SUBJECTS

    return {**GATED_ANALYSIS_SUBJECTS, **RISK_GATED_SUBJECTS}


class ApprovalService:
    """Raise approval tasks and record the decisions that close them."""

    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._tasks = ApprovalTaskRepository(session, actor)
        self._decisions = ApprovalDecisionRepository(session, actor)
        self._versions = RequirementVersionRepository(session, actor)
        self._audit = AuditService(session)

    # -- raising tasks ----------------------------------------------------
    def raise_analysis_gate(
        self,
        *,
        project_id: ProjectId,
        gate: Gate,
        subject_type: str,
        subject_id: uuid.UUID,
        subject_version: str | None,
        subject_version_hash: str,
    ) -> ApprovalTask:
        """Raise the G2 or G3 task a persisted P6 value requires (C.3 gate_fanout).

        Only G2 for a compliance mapping, G3 for a security/privacy finding and
        G8 for a risk item. The pipeline may raise these (GATE_TASK_RAISE);
        nobody but a human in the gate's role can decide them. Blocking, always
        (M.3, I.5).
        """
        if _analysis_gate_subjects().get(subject_type) is not gate:
            raise ReqPilotError(f"{gate} is not raised for a {subject_type} subject")
        (role,) = tasks_required_for(gate)
        return self.create_task(
            project_id=project_id,
            gate=gate,
            subject_type=subject_type,
            subject_id=subject_id,
            subject_version=subject_version,
            subject_version_hash=subject_version_hash,
            required_role=role,
            blocking=True,
            _action=Action.GATE_TASK_RAISE,
        )

    def create_task(
        self,
        *,
        project_id: ProjectId,
        gate: Gate,
        subject_type: str,
        subject_id: uuid.UUID,
        subject_version: str | None,
        subject_version_hash: str,
        required_role: Role,
        task_group_id: uuid.UUID | None = None,
        blocking: bool = True,
        _action: Action = Action.REQUIREMENT_SUBMIT,
    ) -> ApprovalTask:
        """Raise one approval task for one role, bound to an exact subject version."""
        if required_role not in required_roles(gate):
            raise ReqPilotError(f"{required_role} is not a required role for {gate}")
        task = ApprovalTask(
            project_id=project_id,
            gate=gate,
            task_group_id=task_group_id,
            subject_type=subject_type,
            subject_id=subject_id,
            subject_version=subject_version,
            subject_version_hash=subject_version_hash,
            required_role=required_role,
            status=ApprovalTaskStatus.OPEN,
            blocking=blocking,
            created_by=self._actor.actor_id,
        )
        self._tasks.add(task, action=_action)

        self._audit.append(
            event_type=AuditEventType.APPROVAL_TASK_CREATED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type=subject_type,
            subject_id=str(subject_id),
            subject_version=subject_version,
            payload={
                "task_id": str(task.id),
                "gate": str(gate),
                "required_role": str(required_role),
                "gate_requires_all_roles": requires_all_roles(gate),
                "subject_version_hash": subject_version_hash,
            },
        )
        return task

    def submit_versions_for_baseline(
        self,
        *,
        project_id: ProjectId,
        version_ids: list[uuid.UUID],
    ) -> list[ApprovalTask]:
        """The G1 trigger: an analyst submits a set of VALIDATED versions.

        G1 is a **co-approval** gate: it requires an Analyst approval *and* a
        Compliance Officer approval. Each version therefore produces one task per
        required role, and every task in the submission shares one
        ``task_group_id``. A version becomes APPROVED only when all of *its*
        tasks are approved, and the baseline is materialised only when every task
        in the group is approved - so one role's signature can never baseline
        anything on its own.

        Each version is moved ``VALIDATED → PENDING_APPROVAL`` as it is
        submitted, which is itself a guarded transition - a version that is not
        validated cannot be submitted at all.
        """
        if not version_ids:
            raise ReqPilotError("a baseline submission must contain at least one version")

        from reqpilot.services.requirements.service import RequirementService

        requirements = RequirementService(self._session, self._actor)
        group_id = new_task_group_id()
        tasks: list[ApprovalTask] = []

        for version_id in version_ids:
            version = self._versions.get(project_id, version_id)
            if version is None:
                raise ReqPilotError(f"requirement version {version_id} not found in this project")

            requirements.transition(
                project_id=project_id,
                version_id=version.id,
                target=RequirementState.PENDING_APPROVAL,
            )
            for role in tasks_required_for(Gate.G1_REQUIREMENT_BASELINE):
                tasks.append(
                    self.create_task(
                        project_id=project_id,
                        gate=Gate.G1_REQUIREMENT_BASELINE,
                        subject_type="requirement_version",
                        subject_id=version.id,
                        subject_version=str(version.version_no),
                        subject_version_hash=version.content_hash,
                        required_role=role,
                        task_group_id=group_id,
                    )
                )
        return tasks

    # -- deciding ---------------------------------------------------------
    def decide(
        self,
        *,
        project_id: ProjectId,
        task_id: uuid.UUID,
        decision: ApprovalDecisionType,
        role_exercised: Role,
        justification: str | None = None,
        baseline_label: str | None = None,
    ) -> GateOutcome:
        """Record a human decision against a task.

        The only approval path in the system. There is no endpoint that writes a
        lifecycle state, and no resume payload that carries an approval.
        """
        task = self._tasks.get(project_id, task_id)
        if task is None:
            raise ReqPilotError("approval task not found in this project")

        self._check_decider(project_id, task, role_exercised)
        current_hash = self._subject_hash(project_id, task)
        self._check_binding(task, current_hash)
        self._check_not_self_approval(project_id, task)

        if decision is ApprovalDecisionType.REJECT and not justification:
            raise ApprovalError("a rejection must carry a justification")

        record = ApprovalDecision(
            task_id=task.id,
            project_id=project_id,
            decided_by=self._actor.actor_id,
            role_exercised=role_exercised,
            decision=decision,
            justification=justification,
            subject_version_hash=current_hash,
        )
        self._decisions.append(record)

        self._audit.append(
            event_type=(
                AuditEventType.APPROVAL_GRANTED
                if decision is ApprovalDecisionType.APPROVE
                else AuditEventType.APPROVAL_REJECTED
                if decision is ApprovalDecisionType.REJECT
                else AuditEventType.APPROVAL_MODIFIED
            ),
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type=task.subject_type,
            subject_id=str(task.subject_id),
            subject_version=task.subject_version,
            payload={
                "task_id": str(task.id),
                "decision_id": str(record.id),
                "gate": str(task.gate),
                "decision": str(decision),
                "role_exercised": str(role_exercised),
                "subject_version_hash": current_hash,
            },
        )

        return self._settle(project_id, task, decision, record, baseline_label)

    # -- checks -----------------------------------------------------------
    def _check_decider(self, project_id: ProjectId, task: ApprovalTask, role: Role) -> None:
        """Task still open, decider authorised by the policy, role is the task's own."""
        if task.is_terminal():
            raise ApprovalError(
                f"approval task {task.id} is already {task.status}; a decided task "
                "cannot be decided again"
            )
        if task.status is ApprovalTaskStatus.CANCELLED:
            raise ApprovalError("approval task has been cancelled")

        # The policy is the single authority on who may decide a gate: human
        # only, project isolation first, a role the gate requires, held in this
        # project. require() raises on denial, so a refusal cannot be ignored.
        require(
            self._actor,
            Action.APPROVAL_DECIDE,
            ResourceRef(
                resource_type=ResourceType.APPROVAL_TASK,
                project_id=project_id,
                resource_id=str(task.id),
                gate=task.gate,
                role_exercised=role,
            ),
        )

        # Each task names exactly one role. Approving the Analyst task as a
        # Compliance Officer would collapse co-approval into a single signature.
        if role is not task.required_role:
            raise ApprovalError(
                f"this task must be decided as {task.required_role}, not {role}; "
                f"{task.gate} is a co-approval gate and each required role has its "
                "own task"
            )

    def _subject_hash(self, project_id: ProjectId, task: ApprovalTask) -> str:
        if self._is_analysis_gate(task):
            return self._analysis_gates(task).subject(project_id, task).current_hash
        if task.subject_type != "requirement_version":
            raise ReqPilotError(
                f"approval subject type {task.subject_type!r} is not supported in this phase"
            )
        version = self._versions.get(project_id, task.subject_id)
        if version is None:
            raise ReqPilotError("the approval subject no longer exists in this project")
        return version.content_hash

    @staticmethod
    def _check_binding(task: ApprovalTask, current_hash: str) -> None:
        if not hashes_match(task.subject_version_hash, current_hash):
            raise StaleApprovalError(
                "the subject changed after this approval task was raised; the task no "
                "longer covers the current version and a new task is required"
            )

    def _check_not_self_approval(self, project_id: ProjectId, task: ApprovalTask) -> None:
        if self._is_analysis_gate(task):
            # The interpretation or the risk was produced by the pipeline; the
            # person whose work it is about may not sign it off.
            authored_by = self._analysis_gates(task).subject(project_id, task).authored_by
            if authored_by == self._actor.actor_id:
                raise SelfApprovalError(
                    "an actor may not decide a gate on an analysis of their own work "
                    "(the requirement version, or the risk they recorded)"
                )
            return
        version = self._versions.get(project_id, task.subject_id)
        if version is not None and version.created_by == self._actor.actor_id:
            raise SelfApprovalError("an actor may not approve a requirement version they authored")

    # -- settlement -------------------------------------------------------
    def _settle(
        self,
        project_id: ProjectId,
        task: ApprovalTask,
        decision: ApprovalDecisionType,
        record: ApprovalDecision,
        baseline_label: str | None,
    ) -> GateOutcome:
        """Apply a decision's effect to the task, the subject and the group."""
        if self._is_analysis_gate(task):
            return self._settle_analysis_gate(project_id, task, decision, record)
        if decision is ApprovalDecisionType.REJECT:
            task.status = ApprovalTaskStatus.REJECTED
            self._session.flush()
            self._transition_subject(project_id, task, RequirementState.REJECTED, justified=True)
            # The subject is rejected, so its sibling role tasks are moot. Cancelling
            # them stops a later approval landing on a rejected version.
            self._cancel_siblings(project_id, task)
            return GateOutcome(task=task, decision=record, task_closed=True, group_complete=False)

        if decision is ApprovalDecisionType.MODIFY:
            # A modification is recorded but does not close the gate: the analyst
            # revises the version, which produces a new hash and a new task.
            self._session.flush()
            return GateOutcome(task=task, decision=record, task_closed=False, group_complete=False)

        # One task, one role: this decision closes this task.
        task.status = ApprovalTaskStatus.APPROVED
        self._session.flush()

        # The subject advances only once *every* role has signed its own task.
        subject_complete = self._subject_complete(project_id, task)
        if subject_complete:
            self._transition_subject(project_id, task, RequirementState.APPROVED)
            self._audit.append(
                event_type=AuditEventType.GATE_PASSED,
                actor_kind=self._actor.kind,
                actor_ref=str(self._actor.actor_id),
                project_id=project_id,
                subject_type=task.subject_type,
                subject_id=str(task.subject_id),
                subject_version=task.subject_version,
                payload={
                    "gate": str(task.gate),
                    "approving_roles": sorted(str(r) for r in required_roles(task.gate)),
                },
            )

        group_complete = subject_complete and self._group_complete(project_id, task)
        baseline_id = None
        if (
            group_complete
            and task.gate is Gate.G1_REQUIREMENT_BASELINE
            and task.task_group_id is not None
        ):
            baseline_id = self._commit_baseline(project_id, task, record, baseline_label)

        return GateOutcome(
            task=task,
            decision=record,
            task_closed=True,
            group_complete=group_complete,
            baseline_id=baseline_id,
        )

    # -- G2 / G3 (P6) and G8 (P7) ------------------------------------------
    @staticmethod
    def _is_analysis_gate(task: ApprovalTask) -> bool:
        return task.subject_type in _analysis_gate_subjects()

    def _analysis_gates(self, task: ApprovalTask) -> AnalysisGate:
        """The gate service that owns this subject type.

        Both services present the same two operations - ``subject`` (load and
        re-bind, refusing a stale subject) and ``settle`` (apply the decision to
        the subject and audit it) - so the five checks above are written once
        and apply identically to G2, G3 and G8.
        """
        from reqpilot.services.compliance.gates import GATED_ANALYSIS_SUBJECTS
        from reqpilot.services.risk.gates import RISK_GATED_SUBJECTS, RiskGateService

        if task.subject_type in RISK_GATED_SUBJECTS:
            return RiskGateService(self._session, self._actor)
        if task.subject_type in GATED_ANALYSIS_SUBJECTS:
            from reqpilot.services.compliance.gates import AnalysisGateService

            return AnalysisGateService(self._session, self._actor)
        raise ReqPilotError(  # pragma: no cover - guarded by _is_analysis_gate
            f"{task.subject_type!r} is not an analysis-gate subject"
        )

    def _settle_analysis_gate(
        self,
        project_id: ProjectId,
        task: ApprovalTask,
        decision: ApprovalDecisionType,
        record: ApprovalDecision,
    ) -> GateOutcome:
        """Settle G2/G3: the mapping or finding moves; the requirement never does."""
        if decision is ApprovalDecisionType.MODIFY:
            self._session.flush()
            return GateOutcome(task=task, decision=record, task_closed=False, group_complete=False)
        task.status = (
            ApprovalTaskStatus.APPROVED
            if decision is ApprovalDecisionType.APPROVE
            else ApprovalTaskStatus.REJECTED
        )
        self._session.flush()
        self._analysis_gates(task).settle(project_id, task, decision, record)
        if decision is ApprovalDecisionType.APPROVE:
            self._audit.append(
                event_type=AuditEventType.GATE_PASSED,
                actor_kind=self._actor.kind,
                actor_ref=str(self._actor.actor_id),
                project_id=project_id,
                subject_type=task.subject_type,
                subject_id=str(task.subject_id),
                subject_version=task.subject_version,
                payload={
                    "gate": str(task.gate),
                    "approving_roles": sorted(str(r) for r in required_roles(task.gate)),
                },
            )
        return GateOutcome(task=task, decision=record, task_closed=True, group_complete=True)

    def _sibling_tasks(self, project_id: ProjectId, task: ApprovalTask) -> list[ApprovalTask]:
        """Every task in the group that governs the same subject."""
        if task.task_group_id is None:
            return [task]
        return [
            t
            for t in self._tasks.list_in_group(project_id, task.task_group_id)
            if t.subject_id == task.subject_id
        ]

    def _subject_complete(self, project_id: ProjectId, task: ApprovalTask) -> bool:
        """Whether every required role has approved its own task for this subject.

        This is what makes G1 a co-approval gate: an Analyst approval leaves the
        Compliance Officer task open, so the subject stays PENDING_APPROVAL and
        nothing is baselined.
        """
        siblings = self._sibling_tasks(project_id, task)
        return all(t.status is ApprovalTaskStatus.APPROVED for t in siblings)

    def _cancel_siblings(self, project_id: ProjectId, task: ApprovalTask) -> None:
        """Cancel the other role tasks for a subject that has been rejected."""
        for sibling in self._sibling_tasks(project_id, task):
            if sibling.id != task.id and sibling.status is ApprovalTaskStatus.OPEN:
                sibling.status = ApprovalTaskStatus.CANCELLED
        self._session.flush()

    def _group_complete(self, project_id: ProjectId, task: ApprovalTask) -> bool:
        if task.task_group_id is None:
            return True
        siblings = self._tasks.list_in_group(project_id, task.task_group_id)
        return all(t.status is ApprovalTaskStatus.APPROVED for t in siblings)

    def _transition_subject(
        self,
        project_id: ProjectId,
        task: ApprovalTask,
        target: RequirementState,
        justified: bool = False,
    ) -> None:
        """Apply the state change a passed gate implies.

        Deliberately **not** routed through ``RequirementService.transition``.
        That method is the analyst's authoring operation and is gated on
        ``requirement.transition``, which a compliance officer rightly does not
        hold. A gate-consequent transition is authorised by the decision that
        has just been recorded, not by the decider's general standing - so
        widening the analyst's permission to make this work would have been
        exactly the wrong fix.

        The lifecycle guard still applies: the transition is validated against
        the approved table, and it is audited like any other.
        """
        version = self._versions.get(project_id, task.subject_id)
        if version is None:  # pragma: no cover - defensive
            return

        ctx = TransitionContext(
            has_valid_approval_decision=target is RequirementState.APPROVED,
            has_rejection_justification=justified,
        )
        source = version.state
        validate_transition(source, target, ctx)
        version.state = target
        self._session.flush()

        self._audit.append(
            event_type=AuditEventType.STATE_TRANSITION,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="requirement_version",
            subject_id=str(version.id),
            subject_version=str(version.version_no),
            payload={"from": str(source), "to": str(target), "via_gate": str(task.gate)},
        )

    def _commit_baseline(
        self,
        project_id: ProjectId,
        task: ApprovalTask,
        record: ApprovalDecision,
        label: str | None,
    ) -> uuid.UUID:
        from reqpilot.services.baseline.service import BaselineService

        group = self._tasks.list_in_group(project_id, task.task_group_id)  # type: ignore[arg-type]

        # A co-approval gate raises one task per role, so each subject appears
        # once per required role in the group. The baseline takes distinct
        # subjects, in submission order.
        version_ids: list[uuid.UUID] = []
        for sibling in group:
            if sibling.subject_id not in version_ids:
                version_ids.append(sibling.subject_id)

        baseline = BaselineService(self._session, self._actor).commit(
            project_id=project_id,
            label=label or f"baseline-{task.task_group_id}",
            version_ids=version_ids,
            approval_decision_id=record.id,
        )
        return baseline.id

    # -- reads ------------------------------------------------------------
    def list_open_tasks(self, project_id: ProjectId) -> list[ApprovalTask]:
        return self._tasks.list_open(project_id)

    def list_tasks(self, project_id: ProjectId) -> list[ApprovalTask]:
        return self._tasks.list_for_project(project_id)

    def get_task(self, project_id: ProjectId, task_id: uuid.UUID) -> ApprovalTask | None:
        return self._tasks.get(project_id, task_id)

    def decisions_for(self, project_id: ProjectId, task_id: uuid.UUID) -> list[ApprovalDecision]:
        return self._decisions.list_for_task(project_id, task_id)
