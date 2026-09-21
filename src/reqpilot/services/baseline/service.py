"""The baseline service and its invariant (architecture H.4).

    **No unapproved requirement version may enter a baseline** (``FR-HIL-004``).

The architecture asks for that invariant at three independent levels. This
module is the first: the service refuses to assemble the rows at all. The second
is a database trigger on PostgreSQL, installed by the migration. The third is
the approval path itself - a version reaches APPROVED only through a recorded,
role-appropriate, exactly-bound decision.

Any one of the three would be defeatable in isolation. Together they mean that
producing an unapproved baseline would require a bug in the service, a missing
trigger, *and* a forged decision simultaneously.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    Action,
    ApprovalDecisionType,
    AuditEventType,
    ProjectLifecycleState,
    ResourceType,
)
from reqpilot.domain.errors import BaselineInvariantError, ReqPilotError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import RequirementState, TransitionContext, validate_transition
from reqpilot.domain.models.approval import ApprovalDecision, ApprovalTask
from reqpilot.domain.models.baseline import Baseline, BaselineMember
from reqpilot.domain.models.identity import Project
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.domain.versioning import hashes_match
from reqpilot.repositories.approval import ApprovalDecisionRepository
from reqpilot.repositories.baseline import BaselineRepository
from reqpilot.repositories.requirements import (
    RequirementRepository,
    RequirementVersionRepository,
)
from reqpilot.services.audit import AuditService


class BaselineService:
    """Freeze an approved set of requirement versions."""

    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._baselines = BaselineRepository(session, actor)
        self._versions = RequirementVersionRepository(session, actor)
        self._requirements = RequirementRepository(session, actor)
        self._decisions = ApprovalDecisionRepository(session, actor)
        self._audit = AuditService(session)

    def commit(
        self,
        *,
        project_id: ProjectId,
        label: str,
        version_ids: list[uuid.UUID],
        approval_decision_id: uuid.UUID,
    ) -> Baseline:
        """Create a frozen baseline from approved versions.

        Every check below is a refusal, not a warning. A baseline that is
        allowed to contain one unapproved version is worth nothing as evidence.
        """
        require(
            self._actor,
            Action.BASELINE_CREATE,
            ResourceRef(resource_type=ResourceType.BASELINE, project_id=project_id),
        )
        if not version_ids:
            raise BaselineInvariantError("a baseline must contain at least one version")
        if self._baselines.label_exists(project_id, label):
            raise ReqPilotError(f"a baseline labelled {label!r} already exists in this project")

        decision = self._decisions.get(project_id, approval_decision_id)
        if decision is None:
            raise BaselineInvariantError(
                "a baseline must name the approval decision that authorised it"
            )
        if decision.decision is not ApprovalDecisionType.APPROVE:
            raise BaselineInvariantError(
                f"the authorising decision is {decision.decision}, not an approval"
            )

        versions = self._collect_and_verify(project_id, version_ids)

        baseline = Baseline(
            project_id=project_id,
            label=label,
            created_by=self._actor.actor_id,
            approval_decision_id=decision.id,
        )
        self._baselines.add(baseline)

        for version in versions:
            self._baselines.add_member(
                BaselineMember(
                    baseline_id=baseline.id,
                    requirement_version_id=version.id,
                    project_id=project_id,
                    version_hash=version.content_hash,
                )
            )
            self._promote_to_baselined(project_id, version)
            self._audit.append(
                event_type=AuditEventType.BASELINE_MEMBER_ADDED,
                actor_kind=self._actor.kind,
                actor_ref=str(self._actor.actor_id),
                project_id=project_id,
                subject_type="requirement_version",
                subject_id=str(version.id),
                subject_version=str(version.version_no),
                payload={"baseline_id": str(baseline.id), "content_hash": version.content_hash},
            )

        self._advance_project(project_id)
        self._session.flush()

        self._audit.append(
            event_type=AuditEventType.BASELINE_COMMITTED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="baseline",
            subject_id=str(baseline.id),
            payload={
                "label": label,
                "member_count": len(versions),
                "approval_decision_id": str(decision.id),
            },
        )
        return baseline

    # -- the invariant ----------------------------------------------------
    def _collect_and_verify(
        self, project_id: ProjectId, version_ids: list[uuid.UUID]
    ) -> list[RequirementVersion]:
        """Load every candidate and refuse the set unless all of them qualify."""
        seen: set[uuid.UUID] = set()
        versions: list[RequirementVersion] = []

        for version_id in version_ids:
            if version_id in seen:
                raise BaselineInvariantError(f"version {version_id} appears twice in the set")
            seen.add(version_id)

            version = self._versions.get(project_id, version_id)
            if version is None:
                # The scoped read already refuses versions from another project,
                # so this covers both "does not exist" and "not yours" without
                # distinguishing them to the caller.
                raise BaselineInvariantError(
                    f"version {version_id} is not an approvable member of this project"
                )

            if version.state is not RequirementState.APPROVED:
                raise BaselineInvariantError(
                    f"version {version_id} is {version.state}; only APPROVED versions "
                    "may enter a baseline"
                )

            approval = self._matching_approval(project_id, version)
            if approval is None:
                raise BaselineInvariantError(
                    f"version {version_id} has no approval decision bound to its exact content hash"
                )
            versions.append(version)

        return versions

    def _matching_approval(
        self, project_id: ProjectId, version: RequirementVersion
    ) -> ApprovalDecision | None:
        """Find an approval decision bound to this version's exact hash."""
        stmt = select(ApprovalTask).where(
            ApprovalTask.project_id == project_id,
            ApprovalTask.subject_id == version.id,
            ApprovalTask.subject_type == "requirement_version",
        )
        for task in self._session.scalars(stmt):
            for decision in self._decisions.list_for_task(project_id, task.id):
                if decision.decision is not ApprovalDecisionType.APPROVE:
                    continue
                if hashes_match(decision.subject_version_hash, version.content_hash):
                    return decision
        return None

    # -- effects ----------------------------------------------------------
    def _promote_to_baselined(self, project_id: ProjectId, version: RequirementVersion) -> None:
        ctx = TransitionContext(all_baseline_members_approved=True)
        validate_transition(version.state, RequirementState.BASELINED, ctx)
        version.state = RequirementState.BASELINED

        requirement = self._requirements.get(project_id, version.requirement_id)
        if requirement is not None:
            requirement.baselined_version_id = version.id
        self._session.flush()

        self._audit.append(
            event_type=AuditEventType.STATE_TRANSITION,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="requirement_version",
            subject_id=str(version.id),
            subject_version=str(version.version_no),
            payload={"from": str(RequirementState.APPROVED), "to": str(RequirementState.BASELINED)},
        )

    def _advance_project(self, project_id: ProjectId) -> None:
        """Move the project to BASELINED.

        The project lifecycle is advanced by this service, never by an API
        payload (``FR-PRJ-002``).
        """
        project = self._session.get(Project, project_id)
        if project is not None:
            project.lifecycle_state = ProjectLifecycleState.BASELINED

    # -- reads ------------------------------------------------------------
    def get(self, project_id: ProjectId, baseline_id: uuid.UUID) -> Baseline | None:
        return self._baselines.get(project_id, baseline_id)

    def list_for_project(self, project_id: ProjectId) -> list[Baseline]:
        return self._baselines.list_for_project(project_id)

    def members(self, project_id: ProjectId, baseline_id: uuid.UUID) -> list[BaselineMember]:
        return self._baselines.list_members(project_id, baseline_id)

    def member_versions(
        self, project_id: ProjectId, baseline_id: uuid.UUID
    ) -> list[RequirementVersion]:
        """The versions a baseline contains, for display and verification."""
        out: list[RequirementVersion] = []
        for member in self.members(project_id, baseline_id):
            version = self._versions.get(project_id, member.requirement_version_id)
            if version is not None:
                out.append(version)
        return out
