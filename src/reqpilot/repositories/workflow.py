"""Project-scoped persistence for generated workflows (P10; architecture G.8).

Every method authorises the actor for the action in the row's project before it
touches the session (the second layer of ADR-009). Recording a generated workflow
needs ``WORKFLOW_RECORD``, which the pipeline's system actor holds; reads need
``WORKFLOW_READ``; the edit path - updating an element's wording, adding or removing
a non-mandatory activity, and appending the change-log row - needs
``WORKFLOW_EDIT``, which only the Project Manager holds.

There is no method that deletes a workflow, a phase, a gate, a provenance row or a
change-log row: they are history. The ORM guard and migration 0012's triggers
refuse it independently.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select

from reqpilot.domain.enums import Action, ResourceType, WorkflowStatus
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.base import utc_now
from reqpilot.domain.models.workflow import (
    Workflow,
    WorkflowActivity,
    WorkflowChange,
    WorkflowGate,
    WorkflowPhase,
    WorkflowSource,
)
from reqpilot.repositories.base import ProjectScopedRepository


class WorkflowRepository(ProjectScopedRepository[Workflow]):
    resource_type = ResourceType.WORKFLOW

    # -- writes ------------------------------------------------------------
    def add(
        self,
        workflow: Workflow,
        phases: Sequence[WorkflowPhase],
        activities: Sequence[WorkflowActivity],
        gates: Sequence[WorkflowGate],
        sources: Sequence[WorkflowSource],
    ) -> Workflow:
        """One complete generated workflow - the row and all its children - in one flush."""
        project_id = ProjectId(workflow.project_id)
        self.authorize(Action.WORKFLOW_RECORD, project_id)
        self._session.add(workflow)
        self._session.flush()
        for group in (phases, activities, gates, sources):
            for row in group:
                if row.project_id != project_id or row.workflow_id != workflow.id:
                    raise ValueError("a workflow row belongs to its workflow and project")
                self._session.add(row)
            self._session.flush()
        return workflow

    def supersede(self, workflow: Workflow) -> None:
        """Mark a live workflow superseded (the pipeline, when a newer one is generated)."""
        self.authorize(Action.WORKFLOW_RECORD, ProjectId(workflow.project_id))
        workflow.status = WorkflowStatus.SUPERSEDED
        workflow.updated_at = utc_now()
        self._session.flush()

    def save_edit(self, workflow: Workflow, change: WorkflowChange) -> None:
        """Flush an accepted edit's element changes, the new revision and its change-log row."""
        project_id = ProjectId(workflow.project_id)
        self.authorize(Action.WORKFLOW_EDIT, project_id)
        if change.project_id != project_id or change.workflow_id != workflow.id:
            raise ValueError("a change-log row belongs to its workflow and project")
        workflow.updated_at = utc_now()
        self._session.add(change)
        self._session.flush()

    def add_activity(self, activity: WorkflowActivity) -> None:
        self.authorize(Action.WORKFLOW_EDIT, ProjectId(activity.project_id))
        self._session.add(activity)
        self._session.flush()

    def remove_activity(self, activity: WorkflowActivity) -> None:
        """Remove a non-mandatory activity (the ORM guard and a trigger refuse any other)."""
        self.authorize(Action.WORKFLOW_EDIT, ProjectId(activity.project_id))
        self._session.delete(activity)
        self._session.flush()

    # -- reads -------------------------------------------------------------
    def get(self, project_id: ProjectId, workflow_id: uuid.UUID) -> Workflow | None:
        self.authorize(Action.WORKFLOW_READ, project_id)
        stmt = select(Workflow).where(Workflow.id == workflow_id)
        return self._session.scalars(self.scoped(stmt, Workflow.project_id, project_id)).first()

    def list_for_project(self, project_id: ProjectId) -> list[Workflow]:
        self.authorize(Action.WORKFLOW_READ, project_id)
        stmt = self.scoped(select(Workflow), Workflow.project_id, project_id)
        return list(self._session.scalars(stmt.order_by(Workflow.created_at, Workflow.id)))

    def live(self, project_id: ProjectId) -> Workflow | None:
        """The project's one non-superseded workflow, if any."""
        self.authorize(Action.WORKFLOW_READ, project_id)
        stmt = self.scoped(select(Workflow), Workflow.project_id, project_id).where(
            Workflow.status != WorkflowStatus.SUPERSEDED
        )
        return self._session.scalars(stmt).first()

    def for_run(self, project_id: ProjectId, sdlc_run_id: uuid.UUID) -> list[Workflow]:
        self.authorize(Action.WORKFLOW_READ, project_id)
        stmt = self.scoped(select(Workflow), Workflow.project_id, project_id).where(
            Workflow.sdlc_run_id == sdlc_run_id
        )
        return list(self._session.scalars(stmt.order_by(Workflow.created_at, Workflow.id)))

    def by_inputs(
        self, project_id: ProjectId, sdlc_run_id: uuid.UUID, input_fingerprint: str
    ) -> Workflow | None:
        self.authorize(Action.WORKFLOW_READ, project_id)
        stmt = self.scoped(select(Workflow), Workflow.project_id, project_id).where(
            Workflow.sdlc_run_id == sdlc_run_id,
            Workflow.input_fingerprint == input_fingerprint,
        )
        return self._session.scalars(stmt).first()

    def phases(self, project_id: ProjectId, workflow_id: uuid.UUID) -> list[WorkflowPhase]:
        self.authorize(Action.WORKFLOW_READ, project_id)
        stmt = self.scoped(select(WorkflowPhase), WorkflowPhase.project_id, project_id).where(
            WorkflowPhase.workflow_id == workflow_id
        )
        return list(self._session.scalars(stmt.order_by(WorkflowPhase.position)))

    def activities(self, project_id: ProjectId, workflow_id: uuid.UUID) -> list[WorkflowActivity]:
        self.authorize(Action.WORKFLOW_READ, project_id)
        stmt = self.scoped(select(WorkflowActivity), WorkflowActivity.project_id, project_id).where(
            WorkflowActivity.workflow_id == workflow_id
        )
        return list(self._session.scalars(stmt.order_by(WorkflowActivity.position)))

    def gates(self, project_id: ProjectId, workflow_id: uuid.UUID) -> list[WorkflowGate]:
        self.authorize(Action.WORKFLOW_READ, project_id)
        stmt = self.scoped(select(WorkflowGate), WorkflowGate.project_id, project_id).where(
            WorkflowGate.workflow_id == workflow_id
        )
        return list(self._session.scalars(stmt.order_by(WorkflowGate.position)))

    def sources(self, project_id: ProjectId, workflow_id: uuid.UUID) -> list[WorkflowSource]:
        self.authorize(Action.WORKFLOW_READ, project_id)
        stmt = self.scoped(select(WorkflowSource), WorkflowSource.project_id, project_id).where(
            WorkflowSource.workflow_id == workflow_id
        )
        return list(
            self._session.scalars(
                stmt.order_by(
                    WorkflowSource.element_type,
                    WorkflowSource.element_id,
                    WorkflowSource.source_type,
                    WorkflowSource.source_id,
                    WorkflowSource.relation,
                )
            )
        )

    def all_sources(self, project_id: ProjectId) -> list[WorkflowSource]:
        self.authorize(Action.WORKFLOW_READ, project_id)
        stmt = self.scoped(select(WorkflowSource), WorkflowSource.project_id, project_id)
        return list(self._session.scalars(stmt))

    def changes(self, project_id: ProjectId, workflow_id: uuid.UUID) -> list[WorkflowChange]:
        self.authorize(Action.WORKFLOW_READ, project_id)
        stmt = self.scoped(select(WorkflowChange), WorkflowChange.project_id, project_id).where(
            WorkflowChange.workflow_id == workflow_id
        )
        return list(self._session.scalars(stmt.order_by(WorkflowChange.revision)))
