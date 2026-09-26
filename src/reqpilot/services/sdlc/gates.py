"""G6 - SDLC selection - as a subject of the P1 approval service (``FR-SDL-008``; M.3).

The approval service stays the **only** decision path, and the five checks it
applies to every gate apply here unchanged: the task is open; the policy lets a
human in the gate's role decide it (G6 requires PM, Architect, Security Reviewer
and Compliance Officer - ``GATE_REQUIRED_ROLES``); the role exercised is the
task's own; no self-approval; and the binding is exact. This module supplies what
that service needs for an ``sdlc_run`` subject:

* **Binding** - the run's recommendation hash (the ranking and the explanation as
  stored), recomputed at decision time. A run that has been superseded, or is no
  longer awaiting G6, is refused as stale.
* **Authors** - who may not sign: the person who started the run and everyone
  whose factor override it carries. They shaped the recommendation.
* **Settlement** - G6 is co-approval: one task per role in one group. Only when
  all four are APPROVED is the selection recorded - and it is always the
  computed first candidate (the database refuses any other). One REJECT rejects
  the run; one MODIFY asks for revision (both cancel the remaining tasks). An
  approved selection supersedes any earlier selection. Each approval becomes an
  ``APPROVED_BY`` trace edge (N.2 #23) when the trace graph is next synchronised
  (a human action; policy rule 11), exactly as every other gate's decisions do.

Nothing here reads model output.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    ApprovalDecisionType,
    ApprovalTaskStatus,
    AuditEventType,
    Gate,
    SdlcRunStatus,
)
from reqpilot.domain.errors import ReqPilotError, StaleApprovalError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.approval import ApprovalDecision, ApprovalTask
from reqpilot.domain.models.base import utc_now
from reqpilot.domain.models.sdlc import SdlcRun
from reqpilot.domain.policy import Actor
from reqpilot.repositories.approval import ApprovalDecisionRepository, ApprovalTaskRepository
from reqpilot.repositories.sdlc import SdlcRunRepository
from reqpilot.services.audit import AuditService
from reqpilot.services.sdlc.service import SDLC_SUBJECT, run_recommendation_hash


def is_sdlc_task(task: ApprovalTask) -> bool:
    return task.gate is Gate.G6_SDLC_SELECTION and task.subject_type == SDLC_SUBJECT


@dataclass(frozen=True)
class SdlcSubject:
    run: SdlcRun
    current_hash: str
    authors: frozenset[uuid.UUID]


class SdlcGateService:
    """Binding, authors and settlement for G6. Called by the approval service."""

    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._runs = SdlcRunRepository(session, actor)
        self._tasks = ApprovalTaskRepository(session, actor)
        self._decisions = ApprovalDecisionRepository(session, actor)
        self._audit = AuditService(session)

    def subject(self, project_id: ProjectId, task: ApprovalTask) -> SdlcSubject:
        run = self._runs.get(project_id, task.subject_id)
        if run is None:
            raise ReqPilotError("the approval subject no longer exists in this project")
        if (
            run.status is not SdlcRunStatus.AWAITING_G6
            or run.g6_task_group_id != task.task_group_id
        ):
            raise StaleApprovalError(
                f"G6 was raised for a recommendation that is now {run.status}; a later run "
                "needs its own G6"
            )
        authors = {run.created_by}
        authors.update(
            row.overridden_by
            for row in self._runs.factors(project_id, run.id)
            if row.is_overridden and row.overridden_by is not None
        )
        return SdlcSubject(
            run=run, current_hash=run_recommendation_hash(run), authors=frozenset(authors)
        )

    # ------------------------------------------------------------------
    def settle(
        self,
        project_id: ProjectId,
        task: ApprovalTask,
        decision: ApprovalDecisionType,
        record: ApprovalDecision,
        *,
        subject_complete: bool,
    ) -> None:
        run = self._runs.get(project_id, task.subject_id)
        if run is None:  # pragma: no cover - checked at binding
            return
        if decision is ApprovalDecisionType.APPROVE:
            if subject_complete:
                self._select(project_id, task, run)
            return
        # REJECT or MODIFY: the recommendation will not be selected as it stands.
        for sibling in self._tasks.list_in_group(project_id, task.task_group_id):  # type: ignore[arg-type]
            if sibling.status is ApprovalTaskStatus.OPEN:
                sibling.status = ApprovalTaskStatus.CANCELLED
        run.status = (
            SdlcRunStatus.REJECTED
            if decision is ApprovalDecisionType.REJECT
            else SdlcRunStatus.REVISION_REQUESTED
        )
        self._runs.save_lifecycle(run)
        self._audit.append(
            event_type=AuditEventType.SDLC_G6_SETTLED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type=SDLC_SUBJECT,
            subject_id=str(run.id),
            subject_version=task.subject_version,
            payload={
                "gate": str(task.gate),
                "task_id": str(task.id),
                "task_group_id": str(task.task_group_id),
                "decision_id": str(record.id),
                "outcome": "rejected"
                if decision is ApprovalDecisionType.REJECT
                else "revision_requested",
                "run_status": str(run.status),
            },
        )

    def _select(self, project_id: ProjectId, task: ApprovalTask, run: SdlcRun) -> None:
        """All four roles approved: record the selection - the computed first, only."""
        group = self._tasks.list_in_group(project_id, task.task_group_id)  # type: ignore[arg-type]
        decisions = [
            d
            for t in group
            for d in self._decisions.list_for_task(project_id, t.id)
            if d.decision is ApprovalDecisionType.APPROVE
        ]
        run.status = SdlcRunStatus.SELECTED
        run.selected_candidate = run.top_candidate
        run.selected_at = utc_now()
        self._runs.save_lifecycle(run)
        replaced = [
            r
            for r in self._runs.list_for_project(project_id, status=SdlcRunStatus.SELECTED)
            if r.id != run.id
        ]
        for earlier in replaced:
            earlier.status = SdlcRunStatus.SUPERSEDED
            self._runs.save_lifecycle(earlier)
            self._audit.append(
                event_type=AuditEventType.SDLC_RUN_SUPERSEDED,
                actor_kind=self._actor.kind,
                actor_ref=str(self._actor.actor_id),
                project_id=project_id,
                subject_type=SDLC_SUBJECT,
                subject_id=str(earlier.id),
                subject_version=earlier.ruleset_ref,
                payload={
                    "sdlc_run_id": str(earlier.id),
                    "previous_status": str(SdlcRunStatus.SELECTED),
                    "superseded_by": str(run.id),
                    "g6_tasks_cancelled": 0,
                },
            )
        self._audit.append(
            event_type=AuditEventType.SDLC_SELECTION_RECORDED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type=SDLC_SUBJECT,
            subject_id=str(run.id),
            subject_version=run.ruleset_ref,
            graph_run_id=run.graph_run_id,
            payload={
                "sdlc_run_id": str(run.id),
                "selected_candidate": run.selected_candidate,
                "computed_top_candidate": run.top_candidate,
                "ranking_hash": run.ranking_hash,
                "recommendation_hash": run.recommendation_hash,
                "task_group_id": str(run.g6_task_group_id),
                "approvals": [
                    {
                        "decision_id": str(d.id),
                        "role": str(d.role_exercised),
                        "decided_by": str(d.decided_by),
                    }
                    for d in decisions
                ],
                "superseded_selections": [str(r.id) for r in replaced],
                "explanation_status": str(run.explanation_status),
            },
        )
