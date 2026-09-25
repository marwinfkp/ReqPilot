"""G4, G5 and G7 as subjects of the P1 approval service (architecture M.3; ``FR-HIL-001``).

The approval service stays the **only** decision path. This module supplies what
it needs for the three gates P8 completes, exactly as ``services/compliance/gates.py``
does for G2/G3 and ``services/risk/gates.py`` for G8:

* **Binding** - the subject's current content hash, recomputed at decision time,
  and a staleness rule: a decision about a subject that has moved on is refused.
* **Authors** - who may not sign (no self-approval).
* **Settlement** - what an approval or rejection does, audited.
* **Raising** - the deterministic trigger that creates the tasks, and nothing else.

G4 - conflicting stakeholder decision (``FR-CNF-005``; M.3 "Analyst + affected
stakeholders"). Its subject is a P5 ``conflict`` that involves a stakeholder
disagreement and has been **resolved** by an analyst through P5: the gate binds
the recorded resolution, and the analyst and each affected stakeholder must sign
it. The P5 semantics are untouched - there is no ``CONFLICTED`` state, the P5
guard still blocks ``VALIDATED`` while the conflict is open, and resolving a
conflict in P5 raises nothing by itself. What P8 adds is that a disagreement
resolution cannot reach a baseline until G4 has passed.

G5 - architecture-critical requirement. Its subject is a requirement version,
raised by the M.3 predicate (:func:`reqpilot.domain.governance.architecture_critical_reasons`)
or an analyst flag, and decided by the Project Manager (the "Architect / PM" of
M.3; ReqPilot has no separate architect role - approved Phase 0 F.1 lists
"Project Manager / Software Architect" as one actor).

G7 - change to an approved requirement. Its subject is the **successor** version.
P1 already raises its tasks when a successor of an APPROVED or BASELINED version
is created; P8 decides them. Approving G7 approves the change; the successor then
goes through the ordinary analysis and G1, and only when G1 passes does the old
version become SUPERSEDED. Rejecting G7 withdraws the successor. The approved
predecessor is never touched in between (architecture M.3: "baselined version
unchanged meanwhile").

Nothing here reads model output.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    ApprovalDecisionType,
    ApprovalTaskStatus,
    AuditEventType,
    ConflictStatus,
    Gate,
    ResourceType,
    Role,
)
from reqpilot.domain.errors import ReqPilotError, StaleApprovalError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import RequirementState, TransitionContext, validate_transition
from reqpilot.domain.lifecycle.states import TERMINAL_STATES
from reqpilot.domain.models.approval import ApprovalDecision, ApprovalTask
from reqpilot.domain.models.base import as_utc
from reqpilot.domain.models.elicitation import Stakeholder
from reqpilot.domain.models.quality import Conflict
from reqpilot.domain.policy import Actor
from reqpilot.repositories.approval import ApprovalDecisionRepository, ApprovalTaskRepository
from reqpilot.repositories.elicitation import StakeholderRepository
from reqpilot.repositories.quality import ConflictRepository
from reqpilot.repositories.requirements import (
    RequirementRepository,
    RequirementVersionRepository,
)
from reqpilot.services.audit import AuditService

CONFLICT_SUBJECT = ResourceType.CONFLICT.value
VERSION_SUBJECT = ResourceType.REQUIREMENT_VERSION.value

#: The P8 gate subjects and their gates. G5 and G7 share the requirement-version
#: subject type with G1, so they are told apart by gate, never by subject alone.
GOVERNANCE_GATES: frozenset[Gate] = frozenset(
    {
        Gate.G4_STAKEHOLDER_CONFLICT,
        Gate.G5_ARCHITECTURE_CRITICAL,
        Gate.G7_APPROVED_REQUIREMENT_CHANGE,
    }
)

#: Hash algorithm label for the G4 binding; bumping it invalidates old bindings.
CONFLICT_GATE_HASH = "g4-sha256-v1"


def is_governance_task(task: ApprovalTask) -> bool:
    """Whether the task is one of the P8 gates this module settles."""
    if task.gate is Gate.G4_STAKEHOLDER_CONFLICT:
        return task.subject_type == CONFLICT_SUBJECT
    if task.gate in (Gate.G5_ARCHITECTURE_CRITICAL, Gate.G7_APPROVED_REQUIREMENT_CHANGE):
        return task.subject_type == VERSION_SUBJECT
    return False


def conflict_gate_hash(conflict: Conflict, version_a_hash: str, version_b_hash: str) -> str:
    """What a G4 signature covers: the conflict, both exact versions and the resolution.

    The resolution and its recorded reason are part of it, so the stakeholders
    sign the decision they were shown and nothing else.
    """
    payload = {
        "algorithm": CONFLICT_GATE_HASH,
        "project_id": str(conflict.project_id),
        "conflict_id": str(conflict.id),
        "version_a_id": str(conflict.version_a_id),
        "version_b_id": str(conflict.version_b_id),
        "version_a_hash": version_a_hash,
        "version_b_hash": version_b_hash,
        "status": str(conflict.status),
        "resolution": str(conflict.resolution) if conflict.resolution else "",
        "resolution_reason": conflict.resolution_reason or "",
        "stakeholder_a": conflict.stakeholder_a or "",
        "stakeholder_b": conflict.stakeholder_b or "",
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalise_label(text: str | None) -> str:
    return " ".join((text or "").lower().split())


@dataclass(frozen=True)
class GovernedSubject:
    """A P8 gate subject as the approval service sees it."""

    subject_type: str
    subject_id: uuid.UUID
    current_hash: str
    #: Whose work the subject is. None of them may sign it (no self-approval).
    authors: frozenset[uuid.UUID]


@dataclass(frozen=True)
class GateState:
    """Where a gate stands for one subject, read from the persisted tasks."""

    required: bool
    passed: bool
    open_tasks: int
    rejected: bool
    reasons: tuple[str, ...] = ()

    @property
    def raised(self) -> bool:
        return self.passed or self.open_tasks > 0 or self.rejected


class GovernanceGateService:
    """Binding, authors and settlement for G4, G5 and G7. Called by the approval service."""

    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._tasks = ApprovalTaskRepository(session, actor)
        self._decisions = ApprovalDecisionRepository(session, actor)
        self._versions = RequirementVersionRepository(session, actor)
        self._requirements = RequirementRepository(session, actor)
        self._conflicts = ConflictRepository(session, actor)
        self._audit = AuditService(session)

    # ------------------------------------------------------------------
    # binding
    # ------------------------------------------------------------------
    def subject(self, project_id: ProjectId, task: ApprovalTask) -> GovernedSubject:
        if task.gate is Gate.G4_STAKEHOLDER_CONFLICT:
            return self._conflict_subject(project_id, task.subject_id)
        return self._version_subject(project_id, task)

    def _conflict_subject(self, project_id: ProjectId, conflict_id: uuid.UUID) -> GovernedSubject:
        conflict = self._conflicts.get(project_id, conflict_id)
        if conflict is None:
            raise ReqPilotError("the approval subject no longer exists in this project")
        a = self._versions.get(project_id, conflict.version_a_id)
        b = self._versions.get(project_id, conflict.version_b_id)
        if a is None or b is None:  # pragma: no cover - composite foreign keys
            raise ReqPilotError("a conflicting requirement version no longer exists")
        if conflict.status is not ConflictStatus.RESOLVED:
            raise StaleApprovalError(
                "G4 binds a recorded resolution; this conflict is "
                f"{conflict.status}, so there is no resolution to sign"
            )
        authors = frozenset(v for v in (a.created_by, b.created_by) if v is not None)
        return GovernedSubject(
            subject_type=CONFLICT_SUBJECT,
            subject_id=conflict.id,
            current_hash=conflict_gate_hash(conflict, a.content_hash, b.content_hash),
            authors=authors,
        )

    def _version_subject(self, project_id: ProjectId, task: ApprovalTask) -> GovernedSubject:
        version = self._versions.get(project_id, task.subject_id)
        if version is None:
            raise ReqPilotError("the approval subject no longer exists in this project")
        requirement = self._requirements.get(project_id, version.requirement_id)
        if requirement is None:  # pragma: no cover - foreign key
            raise ReqPilotError("the approval subject's requirement no longer exists")
        if (
            requirement.current_version_id != version.id
            or version.state in TERMINAL_STATES
            or version.state is RequirementState.REJECTED
        ):
            raise StaleApprovalError(
                f"{task.gate} was raised for a requirement version that is no longer current; "
                "a later version needs its own gate"
            )
        return GovernedSubject(
            subject_type=VERSION_SUBJECT,
            subject_id=version.id,
            current_hash=version.content_hash,
            authors=frozenset({version.created_by}) if version.created_by else frozenset(),
        )

    # ------------------------------------------------------------------
    # settlement
    # ------------------------------------------------------------------
    def on_passed(
        self, project_id: ProjectId, task: ApprovalTask, record: ApprovalDecision
    ) -> None:
        """Every required role has approved its own task for this subject."""
        self._settled(project_id, task, record, passed=True)

    def on_rejected(
        self, project_id: ProjectId, task: ApprovalTask, record: ApprovalDecision
    ) -> None:
        """A required role rejected. The gate has failed for this subject."""
        if task.gate is Gate.G7_APPROVED_REQUIREMENT_CHANGE:
            # M.3 G7 "on reject: new version discarded". Withdrawal is the P1
            # transition that discards a version without deleting it.
            self._gate_transition(project_id, task, RequirementState.WITHDRAWN)
        elif task.gate is Gate.G5_ARCHITECTURE_CRITICAL:
            # M.3 G5 "on reject: back to clarification" - where the lifecycle
            # allows it. From any other state the rejection stands as a blocker.
            version = self._versions.get(project_id, task.subject_id)
            if version is not None and version.state is RequirementState.ANALYZED:
                self._gate_transition(project_id, task, RequirementState.CLARIFICATION_REQUIRED)
        self._settled(project_id, task, record, passed=False)

    def _settled(
        self, project_id: ProjectId, task: ApprovalTask, record: ApprovalDecision, *, passed: bool
    ) -> None:
        if task.gate is Gate.G5_ARCHITECTURE_CRITICAL:
            # G5 has no subject row of its own to update: the decision and the
            # task statuses are the record. GATE_PASSED is written by the
            # approval service; a rejection is already APPROVAL_REJECTED.
            return
        event = (
            AuditEventType.CONFLICT_GATE_SETTLED
            if task.gate is Gate.G4_STAKEHOLDER_CONFLICT
            else AuditEventType.CHANGE_GATE_SETTLED
        )
        self._audit.append(
            event_type=event,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type=task.subject_type,
            subject_id=str(task.subject_id),
            subject_version=task.subject_version,
            payload={
                "gate": str(task.gate),
                "task_id": str(task.id),
                "task_group_id": str(task.task_group_id) if task.task_group_id else None,
                "decision_id": str(record.id),
                "outcome": "passed" if passed else "rejected",
                "subject_version_hash": record.subject_version_hash,
            },
        )

    def _gate_transition(
        self, project_id: ProjectId, task: ApprovalTask, target: RequirementState
    ) -> None:
        """A gate-consequent transition, authorised by the decision (P1 precedent)."""
        version = self._versions.get(project_id, task.subject_id)
        if version is None:  # pragma: no cover - checked at binding
            return
        ctx = TransitionContext(is_baselined=version.state is RequirementState.BASELINED)
        source = version.state
        validate_transition(source, target, ctx)
        version.state = target
        self._session.flush()
        self._audit.append(
            event_type=AuditEventType.STATE_TRANSITION,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type=VERSION_SUBJECT,
            subject_id=str(version.id),
            subject_version=str(version.version_no),
            payload={"from": str(source), "to": str(target), "via_gate": str(task.gate)},
        )
        # Any other open task about the discarded version is moot.
        if target is RequirementState.WITHDRAWN:
            for other in self._tasks.list_for_project(project_id):
                if (
                    other.subject_id == version.id
                    and other.id != task.id
                    and other.status is ApprovalTaskStatus.OPEN
                ):
                    other.status = ApprovalTaskStatus.CANCELLED
            self._session.flush()

    # ------------------------------------------------------------------
    # state, read from the persisted tasks (used by readiness and the queue)
    # ------------------------------------------------------------------
    def tasks_for(
        self, project_id: ProjectId, gate: Gate, subject_id: uuid.UUID
    ) -> list[ApprovalTask]:
        return [
            t
            for t in self._tasks.list_for_project(project_id)
            if t.gate is gate and t.subject_id == subject_id
        ]

    def state(
        self,
        project_id: ProjectId,
        gate: Gate,
        subject_id: uuid.UUID,
        current_hash: str,
        *,
        required: bool,
        reasons: tuple[str, ...] = (),
    ) -> GateState:
        """Whether the gate has passed for this exact subject content.

        Passed means: some task group for this subject, bound to the current
        hash, has every task APPROVED. A group bound to an older hash covers
        nothing now (exact-version binding).
        """
        tasks = self.tasks_for(project_id, gate, subject_id)
        current = [t for t in tasks if t.subject_version_hash == current_hash]
        groups: dict[uuid.UUID | None, list[ApprovalTask]] = {}
        for t in current:
            groups.setdefault(t.task_group_id, []).append(t)
        passed = any(
            group and all(t.status is ApprovalTaskStatus.APPROVED for t in group)
            for group in groups.values()
        )
        open_tasks = sum(1 for t in current if t.status is ApprovalTaskStatus.OPEN)
        rejected = any(t.status is ApprovalTaskStatus.REJECTED for t in current) and not passed
        return GateState(
            required=required or bool(tasks),
            passed=passed,
            open_tasks=open_tasks,
            rejected=rejected,
            reasons=reasons,
        )


# ---------------------------------------------------------------------------
# G4: which stakeholders are "affected", and raising the tasks
# ---------------------------------------------------------------------------


def affected_stakeholder_users(
    stakeholders: list[Stakeholder], labels: tuple[str | None, ...]
) -> list[tuple[str, uuid.UUID | None]]:
    """``(label, user id or None)`` for each distinct side of a disagreement.

    A conflict records each side's stakeholder as the speaker label its sources
    carry (P5). That label is matched to a project ``stakeholder`` record by
    exact normalised name or role; when the record is linked to a user account,
    that person - and only that person - signs the side (the task is assigned).
    When no linked record exists, the side is signed by a human holding the
    Stakeholder role in the project, which the P8 report records as a limitation.
    """
    out: list[tuple[str, uuid.UUID | None]] = []
    seen_users: set[uuid.UUID] = set()
    seen_labels: set[str] = set()
    for label in labels:
        key = normalise_label(label)
        if not key or key in seen_labels:
            continue
        seen_labels.add(key)
        match = next(
            (
                s
                for s in sorted(stakeholders, key=lambda s: (as_utc(s.created_at), str(s.id)))
                if s.user_id is not None
                and key in (normalise_label(s.name), normalise_label(s.stakeholder_role))
            ),
            None,
        )
        user = match.user_id if match is not None else None
        if user is not None and user in seen_users:
            continue
        if user is not None:
            seen_users.add(user)
        out.append((label or key, user))
    return out


class ConflictGateRaiser:
    """Raise G4 for a resolved stakeholder-disagreement conflict. Raising, not deciding."""

    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._gates = GovernanceGateService(session, actor)
        self._conflicts = ConflictRepository(session, actor)
        self._stakeholders = StakeholderRepository(session, actor)

    def raise_for(self, project_id: ProjectId, conflict: Conflict) -> list[ApprovalTask]:
        from reqpilot.domain.enums import Action
        from reqpilot.domain.ids import new_task_group_id
        from reqpilot.services.approval.service import ApprovalService

        if not conflict.involves_stakeholder_disagreement:
            return []
        if conflict.status is not ConflictStatus.RESOLVED:
            return []
        subject = self._gates._conflict_subject(project_id, conflict.id)
        state = self._gates.state(
            project_id,
            Gate.G4_STAKEHOLDER_CONFLICT,
            conflict.id,
            subject.current_hash,
            required=True,
        )
        if state.passed or state.open_tasks:
            return []
        approvals = ApprovalService(self._session, self._actor)
        group = new_task_group_id()
        label = f"conflict {str(conflict.id)[:8]}"
        tasks = [
            approvals.create_task(
                project_id=project_id,
                gate=Gate.G4_STAKEHOLDER_CONFLICT,
                subject_type=CONFLICT_SUBJECT,
                subject_id=conflict.id,
                subject_version=label,
                subject_version_hash=subject.current_hash,
                required_role=Role.ANALYST,
                task_group_id=group,
                _action=Action.GATE_TASK_RAISE,
            )
        ]
        stakeholders = self._stakeholders.list_for_project(project_id)
        for _label, user in affected_stakeholder_users(
            stakeholders, (conflict.stakeholder_a, conflict.stakeholder_b)
        ):
            tasks.append(
                approvals.create_task(
                    project_id=project_id,
                    gate=Gate.G4_STAKEHOLDER_CONFLICT,
                    subject_type=CONFLICT_SUBJECT,
                    subject_id=conflict.id,
                    subject_version=label,
                    subject_version_hash=subject.current_hash,
                    required_role=Role.STAKEHOLDER,
                    task_group_id=group,
                    assignee_user_id=user,
                    _action=Action.GATE_TASK_RAISE,
                )
            )
        return tasks


def approved_decisions_for(
    session: Session, project_id: ProjectId, task_ids: list[uuid.UUID]
) -> list[ApprovalDecision]:
    """The APPROVE decisions recorded against the given tasks (read helper)."""
    if not task_ids:
        return []
    stmt = select(ApprovalDecision).where(
        ApprovalDecision.project_id == project_id,
        ApprovalDecision.task_id.in_(task_ids),
        ApprovalDecision.decision == ApprovalDecisionType.APPROVE,
    )
    return list(session.scalars(stmt.order_by(ApprovalDecision.decided_at)))
