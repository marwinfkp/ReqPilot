"""The requirements repository service - creation, versioning, transitions.

Everything here is deterministic. There is no model, no prompt, and no
orchestration: this is the system of record the later phases will write into.

Three invariants this module is responsible for:

1. **Versions are immutable.** An edit creates a successor; it never rewrites a
   row. Editing an APPROVED or BASELINED version leaves that version exactly as
   it was and raises a change gate for the successor.
2. **State moves only through validated transitions.** There is no method that
   sets a state directly, and the API exposes none either.
3. **Every meaningful action is audited**, in the same transaction as the write,
   so an audited action always happened and an unaudited one never did.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    Action,
    ActorKind,
    ApprovalTaskStatus,
    AuditEventType,
    ClarificationStatus,
    Gate,
    RequirementCategory,
    RequirementPriority,
    ResourceType,
)
from reqpilot.domain.errors import AuthorizationError, ImmutableVersionError, ReqPilotError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import RequirementState, TransitionContext, validate_transition
from reqpilot.domain.models.requirements import Requirement, RequirementVersion
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.domain.requirement_ids import (
    RequirementKind,
    next_requirement_id,
    parse_requirement_id,
)
from reqpilot.domain.versioning import compute_version_hash
from reqpilot.repositories.approval import ApprovalTaskRepository
from reqpilot.repositories.compliance import (
    ComplianceMappingRepository,
    SecurityFindingRepository,
)
from reqpilot.repositories.elicitation import ClarificationRepository, QualityFindingRepository
from reqpilot.repositories.extraction import ClassificationRepository
from reqpilot.repositories.quality import ConflictRepository
from reqpilot.repositories.requirements import (
    RequirementRepository,
    RequirementVersionRepository,
)
from reqpilot.repositories.risk import RiskRepository
from reqpilot.services.audit import AuditService


@dataclass(frozen=True)
class RequirementContent:
    """The governed content of a requirement version.

    A value object rather than a pile of keyword arguments, so that creating a
    requirement and creating a successor version take exactly the same shape and
    cannot drift apart.

    Fields that later roadmap phases populate - applicable regulations (P6) and
    risk level (P7) - are deliberately absent rather than stubbed. The approved
    schema carries them; nothing invents values for them. Acceptance criteria and
    classification labels (P3) are separate rows bound to the version, so adding
    them never rewrites it.
    """

    statement: str
    original_text: str | None = None
    category: RequirementCategory | None = None
    priority: RequirementPriority | None = None
    justification: str | None = None
    dependencies: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    source_refs: tuple[dict, ...] = ()
    #: The extraction role's heuristic review signal (P3), **not** a calibrated
    #: probability. Set once at creation and, as P1 decided, outside the content
    #: hash: it describes how the proposal was made, not what it says.
    review_signal: float | None = None

    def validated(self) -> RequirementContent:
        """Return self after checking what P1 can check about the content."""
        if not self.statement or not self.statement.strip():
            raise ReqPilotError("a requirement statement cannot be empty")
        if self.review_signal is not None and not 0.0 <= self.review_signal <= 1.0:
            raise ReqPilotError("a review signal lies in [0, 1]")
        return self


#: The only targets a non-human actor may move a version to: the transitions the
#: extraction and classification nodes own (architecture H.3, C.3), and - from P4
#: - ``CLARIFICATION_REQUIRED -> CLARIFIED``, whose guard requires a recorded human
#: answer to a clarification (H.3). Everything else from analysis to approval is
#: a human's, or a later phase's, to trigger.
AUTOMATED_TRANSITION_TARGETS: frozenset[RequirementState] = frozenset(
    {
        RequirementState.EXTRACTED,
        RequirementState.CLASSIFIED,
        RequirementState.CLARIFIED,
        RequirementState.INVALID,
    }
)


class RequirementService:
    """Create, version and transition requirements within one project."""

    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._requirements = RequirementRepository(session, actor)
        self._versions = RequirementVersionRepository(session, actor)
        self._tasks = ApprovalTaskRepository(session, actor)
        self._classifications = ClassificationRepository(session, actor)
        self._findings = QualityFindingRepository(session, actor)
        self._clarifications = ClarificationRepository(session, actor)
        self._conflicts = ConflictRepository(session, actor)
        self._mappings = ComplianceMappingRepository(session, actor)
        self._security = SecurityFindingRepository(session, actor)
        self._risks = RiskRepository(session, actor)
        self._audit = AuditService(session)

    # -- creation ---------------------------------------------------------
    def create_requirement(
        self,
        *,
        project_id: ProjectId,
        domain: str,
        content: RequirementContent,
        kind: RequirementKind = RequirementKind.FUNCTIONAL,
        human_id: str | None = None,
    ) -> tuple[Requirement, RequirementVersion]:
        """Create a requirement and its first version in ``CANDIDATE``.

        The human identifier is allocated deterministically as the next free
        sequence for ``(kind, domain)`` within the project, unless one is
        supplied explicitly - in which case it is validated against the approved
        convention and checked for collision.
        """
        content = content.validated()
        require(
            self._actor,
            Action.REQUIREMENT_CREATE,
            ResourceRef(resource_type=ResourceType.REQUIREMENT, project_id=project_id),
        )

        if human_id is None:
            human_id = next_requirement_id(
                kind, domain, self._requirements.existing_human_ids(project_id)
            )
        else:
            parse_requirement_id(human_id)  # raises RequirementIdError if malformed
            if self._requirements.get_by_human_id(project_id, human_id) is not None:
                raise ReqPilotError(f"requirement id {human_id} is already used in this project")

        requirement = Requirement(
            project_id=project_id,
            human_id=human_id,
            created_by=self._actor.actor_id,
        )
        self._requirements.add(requirement)

        version = self._build_version(
            requirement=requirement,
            version_no=1,
            content=content,
            change_reason="initial version",
        )
        self._versions.add(version)

        requirement.current_version_id = version.id
        self._session.flush()

        self._audit.append(
            event_type=AuditEventType.REQUIREMENT_CREATED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="requirement",
            subject_id=str(requirement.id),
            payload={"human_id": human_id, "kind": str(kind)},
        )
        self._audit_version_created(project_id, requirement, version)
        return requirement, version

    def create_version(
        self,
        *,
        project_id: ProjectId,
        requirement_id: uuid.UUID,
        content: RequirementContent,
        change_reason: str,
    ) -> RequirementVersion:
        """Create a successor version. **Never mutates the predecessor.**

        If the current version is APPROVED or BASELINED, that version is left
        exactly as it is - still approved, still baselined - and a G7 change gate
        is raised for the successor. The successor does **not** inherit approval,
        and its content hash differs, so no earlier decision can cover it.
        """
        content = content.validated()
        require(
            self._actor,
            Action.REQUIREMENT_UPDATE,
            ResourceRef(resource_type=ResourceType.REQUIREMENT, project_id=project_id),
        )

        requirement = self._requirements.get(project_id, requirement_id)
        if requirement is None:
            raise ReqPilotError("requirement not found in this project")

        predecessor = self._current_version(project_id, requirement)
        predecessor_state = predecessor.state if predecessor else None
        needs_change_gate = predecessor_state in (
            RequirementState.APPROVED,
            RequirementState.BASELINED,
        )

        next_no = self._versions.highest_version_no(project_id, requirement_id) + 1
        version = self._build_version(
            requirement=requirement,
            version_no=next_no,
            content=content,
            change_reason=change_reason,
        )
        self._versions.add(version)

        # The predecessor's state is untouched. Only the pointer moves.
        requirement.current_version_id = version.id
        self._session.flush()

        self._audit_version_created(project_id, requirement, version)

        if needs_change_gate:
            self._raise_change_gate(project_id, requirement, version)

        return version

    # -- lifecycle --------------------------------------------------------
    def transition(
        self,
        *,
        project_id: ProjectId,
        version_id: uuid.UUID,
        target: RequirementState,
        context_overrides: TransitionContext | None = None,
    ) -> RequirementVersion:
        """Move a version to ``target`` if the approved transition table allows it.

        The only way a state changes. There is no setter, and the API exposes no
        field that writes this column.
        """
        require(
            self._actor,
            Action.REQUIREMENT_TRANSITION,
            ResourceRef(resource_type=ResourceType.REQUIREMENT_VERSION, project_id=project_id),
        )
        if self._actor.kind is not ActorKind.HUMAN and target not in AUTOMATED_TRANSITION_TARGETS:
            # P3 defence in depth: a pipeline may move a version only along the
            # transitions extraction and classification own (architecture H.3).
            raise AuthorizationError(
                f"a {self._actor.kind} actor may not move a version to {target}; "
                f"only to {sorted(str(t) for t in AUTOMATED_TRANSITION_TARGETS)}"
            )
        version = self._versions.get(project_id, version_id)
        if version is None:
            raise ReqPilotError("requirement version not found in this project")

        ctx = context_overrides or self.build_context(project_id, version)
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
            payload={"from": str(source), "to": str(target)},
        )
        return version

    def build_context(
        self, project_id: ProjectId, version: RequirementVersion
    ) -> TransitionContext:
        """Assemble the guard context from what actually exists in P1.

        Fields whose producers belong to later roadmap phases stay at their
        defaults - zero open problems - which is accurate rather than permissive.
        From P4 open quality findings count; from P5 open conflicts do too. From
        P6 every high-impact interpretation (G2) and every high-risk derived
        security/privacy requirement (G3) of this version that is still pending
        counts as a blocking gate - read from the persisted P6 status, so a
        pending row blocks even if its task were somehow missing (fail closed).
        Risk (P7) and G5 are still later.
        """
        blocking = [
            task
            for task in self._tasks.list_for_project(project_id)
            if task.subject_id == version.id
            and task.status is ApprovalTaskStatus.OPEN
            and task.blocking
            and task.gate is not Gate.G1_REQUIREMENT_BASELINE
        ]
        # Labels: the current classification revision (P3, FR-CLS-001), or the
        # single manually set category a P1 version may carry.
        labels = len(self._classifications.current_categories(project_id, version.id))
        # P4: open quality findings block VALIDATED (H.3), and a clarification of
        # this version must have a recorded answer before it can be CLARIFIED.
        open_findings = self._findings.open_count(project_id, version.id)
        answered = any(
            c.status is ClarificationStatus.ANSWERED and c.answer_utterance_id is not None
            for c in self._clarifications.for_version(project_id, version.id)
        )
        # P6: G2 and G3 subjects are the mapping and the finding, not the version,
        # so they are counted from the P6 rows bound to this exact version.
        pending_gates = self._mappings.pending_count(
            project_id, version.id
        ) + self._security.pending_count(project_id, version.id)
        # P7: a HIGH risk that no human has decided. FR-RSK-007's "a
        # high-severity risk blocks baseline approval until reviewed", enforced
        # here in deterministic application code - not in the UI, not in a
        # prompt, and not as a warning. The transition itself fails.
        unreviewed_high_risks = self._risks.unreviewed_high_count(project_id, version.id)
        return TransitionContext(
            source_ref_count=len(version.source_refs or []),
            label_count=labels or (1 if version.category is not None else 0),
            has_current_validation=version.state is RequirementState.VALIDATED,
            blocking_gate_task_count=len(blocking) + pending_gates,
            is_baselined=version.state is RequirementState.BASELINED,
            open_defect_count=open_findings,
            # P5: an open or under-review conflict on either side blocks
            # VALIDATED and PENDING_APPROVAL ([DESIGN] D12: a guard, not a state).
            open_conflict_count=self._conflicts.blocking_count(project_id, version.id),
            unreviewed_high_risk_count=unreviewed_high_risks,
            clarification_answer_present=answered,
        )

    def withdraw(
        self, *, project_id: ProjectId, version_id: uuid.UUID, reason: str
    ) -> RequirementVersion:
        """Withdraw a version that has not been baselined."""
        require(
            self._actor,
            Action.REQUIREMENT_WITHDRAW,
            ResourceRef(resource_type=ResourceType.REQUIREMENT_VERSION, project_id=project_id),
        )
        version = self._versions.get(project_id, version_id)
        if version is None:
            raise ReqPilotError("requirement version not found in this project")

        ctx = self.build_context(project_id, version)
        validate_transition(version.state, RequirementState.WITHDRAWN, ctx)
        previous = version.state
        version.state = RequirementState.WITHDRAWN
        self._session.flush()

        self._audit.append(
            event_type=AuditEventType.REQUIREMENT_WITHDRAWN,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="requirement_version",
            subject_id=str(version.id),
            subject_version=str(version.version_no),
            payload={"from": str(previous), "reason_supplied": bool(reason)},
        )
        return version

    def supersede(
        self,
        *,
        project_id: ProjectId,
        version_id: uuid.UUID,
        successor_id: uuid.UUID,
    ) -> RequirementVersion:
        """Mark a version superseded once its successor has been approved."""
        version = self._versions.get(project_id, version_id)
        successor = self._versions.get(project_id, successor_id)
        if version is None or successor is None:
            raise ReqPilotError("requirement version not found in this project")

        ctx = TransitionContext(
            successor_is_approved=successor.state
            in (RequirementState.APPROVED, RequirementState.BASELINED)
        )
        validate_transition(version.state, RequirementState.SUPERSEDED, ctx)
        version.state = RequirementState.SUPERSEDED
        version.superseded_by_id = successor.id
        self._session.flush()

        self._audit.append(
            event_type=AuditEventType.REQUIREMENT_SUPERSEDED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="requirement_version",
            subject_id=str(version.id),
            subject_version=str(version.version_no),
            payload={"superseded_by": str(successor.id)},
        )
        return version

    # -- reads ------------------------------------------------------------
    def list_requirements(self, project_id: ProjectId) -> list[Requirement]:
        return self._requirements.list_for_project(project_id)

    def get_requirement(
        self, project_id: ProjectId, requirement_id: uuid.UUID
    ) -> Requirement | None:
        return self._requirements.get(project_id, requirement_id)

    def get_version(
        self, project_id: ProjectId, version_id: uuid.UUID
    ) -> RequirementVersion | None:
        return self._versions.get(project_id, version_id)

    def version_history(
        self, project_id: ProjectId, requirement_id: uuid.UUID
    ) -> list[RequirementVersion]:
        return self._versions.list_for_requirement(project_id, requirement_id)

    # -- internals --------------------------------------------------------
    def _current_version(
        self, project_id: ProjectId, requirement: Requirement
    ) -> RequirementVersion | None:
        if requirement.current_version_id is None:
            return None
        return self._versions.get(project_id, requirement.current_version_id)

    def _build_version(
        self,
        *,
        requirement: Requirement,
        version_no: int,
        content: RequirementContent,
        change_reason: str,
    ) -> RequirementVersion:
        content_hash = compute_version_hash(
            requirement_id=str(requirement.id),
            human_id=requirement.human_id,
            version_no=version_no,
            statement=content.statement,
            category=str(content.category) if content.category else None,
            priority=str(content.priority) if content.priority else None,
            justification=content.justification,
            dependencies=list(content.dependencies),
            assumptions=list(content.assumptions),
            source_refs=list(content.source_refs),
        )
        return RequirementVersion(
            requirement_id=requirement.id,
            project_id=requirement.project_id,
            version_no=version_no,
            state=RequirementState.CANDIDATE,
            statement=content.statement,
            original_text=content.original_text,
            category=content.category,
            priority=content.priority,
            justification=content.justification,
            dependencies=list(content.dependencies),
            assumptions=list(content.assumptions),
            source_refs=list(content.source_refs),
            content_hash=content_hash,
            review_signal=content.review_signal,
            change_reason=change_reason,
            created_by=self._actor.actor_id,
        )

    def _raise_change_gate(
        self, project_id: ProjectId, requirement: Requirement, version: RequirementVersion
    ) -> None:
        """Raise G7 for a successor to an already-approved version."""
        from reqpilot.domain.ids import new_task_group_id
        from reqpilot.services.approval.service import ApprovalService, tasks_required_for

        approvals = ApprovalService(self._session, self._actor)
        group_id = new_task_group_id()
        for role in tasks_required_for(Gate.G7_APPROVED_REQUIREMENT_CHANGE):
            approvals.create_task(
                project_id=project_id,
                gate=Gate.G7_APPROVED_REQUIREMENT_CHANGE,
                subject_type="requirement_version",
                subject_id=version.id,
                subject_version=str(version.version_no),
                subject_version_hash=version.content_hash,
                required_role=role,
                task_group_id=group_id,
            )

    def _audit_version_created(
        self, project_id: ProjectId, requirement: Requirement, version: RequirementVersion
    ) -> None:
        self._audit.append(
            event_type=AuditEventType.REQUIREMENT_VERSION_CREATED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="requirement_version",
            subject_id=str(version.id),
            subject_version=str(version.version_no),
            # References only: the statement itself never enters an audit payload.
            payload={
                "requirement_id": str(requirement.id),
                "human_id": requirement.human_id,
                "version_no": version.version_no,
                "content_hash": version.content_hash,
            },
        )


def assert_version_unmodified(version: RequirementVersion) -> None:
    """Raise if any immutable field of ``version`` has an uncommitted change.

    A belt-and-braces guard: the service never edits a version in place, and
    this makes that provable at any call site rather than merely intended.
    """
    insp = sa_inspect(version)
    for field in sorted(RequirementVersion.IMMUTABLE_FIELDS):
        if field not in insp.attrs:  # pragma: no cover - defensive
            continue
        if insp.attrs[field].history.has_changes():
            raise ImmutableVersionError(
                f"requirement_version.{field} is immutable; create a successor version instead"
            )
