"""Project-scoped persistence for approval tasks and decisions.

One deliberate asymmetry: there is **no update or delete** method for
decisions. A decision is append-only, so the repository offers no way to change
one - the same discipline the audit service follows. Task *status* is the one
mutable field, and only the approval service writes it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from reqpilot.domain.enums import Action, ApprovalTaskStatus, Gate, ResourceType
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.approval import ApprovalDecision, ApprovalTask
from reqpilot.repositories.base import ProjectScopedRepository


class ApprovalTaskRepository(ProjectScopedRepository[ApprovalTask]):
    """Approval tasks, always within one project."""

    resource_type = ResourceType.APPROVAL_TASK

    def add(
        self, task: ApprovalTask, *, action: Action = Action.REQUIREMENT_SUBMIT
    ) -> ApprovalTask:
        # Raising a task is part of submitting, not of deciding. From P6 the
        # pipeline also raises G2/G3 tasks from persisted, deterministically
        # evaluated values (GATE_TASK_RAISE); only those two actions raise tasks.
        if action not in (Action.REQUIREMENT_SUBMIT, Action.GATE_TASK_RAISE):
            raise ValueError(f"{action} does not raise approval tasks")
        self.authorize(action, ProjectId(task.project_id))
        self._session.add(task)
        self._session.flush()
        return task

    def get(self, project_id: ProjectId, task_id: uuid.UUID) -> ApprovalTask | None:
        self.authorize(Action.APPROVAL_TASK_READ, project_id)
        stmt = select(ApprovalTask).where(ApprovalTask.id == task_id)
        stmt = self.scoped(stmt, ApprovalTask.project_id, project_id)
        return self._session.scalars(stmt).first()

    def list_open(self, project_id: ProjectId) -> list[ApprovalTask]:
        self.authorize(Action.APPROVAL_TASK_READ, project_id)
        stmt = (
            select(ApprovalTask)
            .where(ApprovalTask.status == ApprovalTaskStatus.OPEN)
            .order_by(ApprovalTask.created_at)
        )
        stmt = self.scoped(stmt, ApprovalTask.project_id, project_id)
        return list(self._session.scalars(stmt))

    def list_for_project(self, project_id: ProjectId) -> list[ApprovalTask]:
        self.authorize(Action.APPROVAL_TASK_READ, project_id)
        stmt = select(ApprovalTask).order_by(ApprovalTask.created_at)
        stmt = self.scoped(stmt, ApprovalTask.project_id, project_id)
        return list(self._session.scalars(stmt))

    def list_in_group(self, project_id: ProjectId, task_group_id: uuid.UUID) -> list[ApprovalTask]:
        """Every task raised by one submission."""
        self.authorize(Action.APPROVAL_TASK_READ, project_id)
        stmt = (
            select(ApprovalTask)
            .where(ApprovalTask.task_group_id == task_group_id)
            .order_by(ApprovalTask.created_at)
        )
        stmt = self.scoped(stmt, ApprovalTask.project_id, project_id)
        return list(self._session.scalars(stmt))

    def find_open_for_subject(
        self, project_id: ProjectId, subject_id: uuid.UUID, gate: Gate
    ) -> ApprovalTask | None:
        self.authorize(Action.APPROVAL_TASK_READ, project_id)
        stmt = select(ApprovalTask).where(
            ApprovalTask.subject_id == subject_id,
            ApprovalTask.gate == gate,
            ApprovalTask.status == ApprovalTaskStatus.OPEN,
        )
        stmt = self.scoped(stmt, ApprovalTask.project_id, project_id)
        return self._session.scalars(stmt).first()


class ApprovalDecisionRepository(ProjectScopedRepository[ApprovalDecision]):
    """Approval decisions. Append and read only - no update, no delete."""

    resource_type = ResourceType.APPROVAL_TASK

    def append(self, decision: ApprovalDecision) -> ApprovalDecision:
        """Record a decision.

        Deliberately **not** authorised here with a blanket action grant: the
        policy refuses a bare ``APPROVAL_DECIDE`` on purpose, because a gate
        decision needs the gate and the role exercised, which this layer does not
        have. This method is reachable only from the approval service, which has
        already had ``policy.can`` authorise the decision with that context.
        """
        self._session.add(decision)
        self._session.flush()
        return decision

    def list_for_task(self, project_id: ProjectId, task_id: uuid.UUID) -> list[ApprovalDecision]:
        self.authorize(Action.APPROVAL_TASK_READ, project_id)
        stmt = (
            select(ApprovalDecision)
            .where(ApprovalDecision.task_id == task_id)
            .order_by(ApprovalDecision.decided_at)
        )
        stmt = self.scoped(stmt, ApprovalDecision.project_id, project_id)
        return list(self._session.scalars(stmt))

    def get(self, project_id: ProjectId, decision_id: uuid.UUID) -> ApprovalDecision | None:
        self.authorize(Action.APPROVAL_TASK_READ, project_id)
        stmt = select(ApprovalDecision).where(ApprovalDecision.id == decision_id)
        stmt = self.scoped(stmt, ApprovalDecision.project_id, project_id)
        return self._session.scalars(stmt).first()
