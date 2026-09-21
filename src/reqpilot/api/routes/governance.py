"""Approval, baseline and audit endpoints (architecture section S).

Two properties of this module are the point of the whole phase:

* ``POST /projects/{id}/baselines`` creates a **G1 approval task**, not a
  baseline. The baseline resource comes into existence only after the gate has
  passed, which is why there is no "create baseline" endpoint at all.
* ``POST /approval-tasks/{id}/decide`` is the **only** approval path. There is
  no state-setting endpoint and no resume payload that carries an approval.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter, status

from reqpilot.api.dependencies import CurrentActor, DbSession
from reqpilot.api.lookup import require_found
from reqpilot.api.schemas import (
    ApprovalDecisionOut,
    ApprovalTaskOut,
    AuditEventOut,
    BaselineDetailOut,
    BaselineOut,
    DecideIn,
    DecisionOut,
    RequirementVersionOut,
    SubmitBaselineIn,
)
from reqpilot.domain.enums import Action, ResourceType
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.approval import ApprovalTask
from reqpilot.domain.models.baseline import Baseline
from reqpilot.domain.policy import Actor
from reqpilot.services.approval import ApprovalService
from reqpilot.services.audit import AuditService
from reqpilot.services.baseline import BaselineService

router = APIRouter(prefix="/api/v1", tags=["governance"])


def _find_task(
    actor: Actor, loader: Callable[[ProjectId], ApprovalTask | None]
) -> tuple[ProjectId, ApprovalTask]:
    """Search only the actor's own projects; never issue an unscoped query."""
    return require_found(actor, Action.APPROVAL_TASK_READ, ResourceType.APPROVAL_TASK, loader)


def _find_baseline(
    actor: Actor, loader: Callable[[ProjectId], Baseline | None]
) -> tuple[ProjectId, Baseline]:
    return require_found(actor, Action.BASELINE_READ, ResourceType.BASELINE, loader)


# --- approval ---------------------------------------------------------------


@router.get("/projects/{project_id}/approval-tasks", response_model=list[ApprovalTaskOut])
def list_approval_tasks(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    only_open: bool = True,
) -> list[ApprovalTaskOut]:
    service = ApprovalService(session, actor)
    tasks = (
        service.list_open_tasks(ProjectId(project_id))
        if only_open
        else service.list_tasks(ProjectId(project_id))
    )
    return [ApprovalTaskOut.model_validate(t) for t in tasks]


@router.get("/approval-tasks/{task_id}/decisions", response_model=list[ApprovalDecisionOut])
def list_task_decisions(
    task_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> list[ApprovalDecisionOut]:
    service = ApprovalService(session, actor)
    project_id, _task = _find_task(actor, lambda pid: service.get_task(pid, task_id))
    return [
        ApprovalDecisionOut.model_validate(d) for d in service.decisions_for(project_id, task_id)
    ]


@router.post("/approval-tasks/{task_id}/decide", response_model=DecisionOut)
def decide_approval_task(
    task_id: uuid.UUID,
    payload: DecideIn,
    session: DbSession,
    actor: CurrentActor,
) -> DecisionOut:
    """Record a human decision. The only way anything becomes APPROVED."""
    service = ApprovalService(session, actor)
    project_id, _task = _find_task(actor, lambda pid: service.get_task(pid, task_id))
    outcome = service.decide(
        project_id=project_id,
        task_id=task_id,
        decision=payload.decision,
        role_exercised=payload.role_exercised,
        justification=payload.justification,
        baseline_label=payload.baseline_label,
    )
    return DecisionOut(
        task=ApprovalTaskOut.model_validate(outcome.task),
        decision=ApprovalDecisionOut.model_validate(outcome.decision),
        task_closed=outcome.task_closed,
        group_complete=outcome.group_complete,
        baseline_id=outcome.baseline_id,
    )


# --- baselines --------------------------------------------------------------


@router.post(
    "/projects/{project_id}/baselines",
    response_model=list[ApprovalTaskOut],
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_for_baseline(
    project_id: uuid.UUID,
    payload: SubmitBaselineIn,
    session: DbSession,
    actor: CurrentActor,
) -> list[ApprovalTaskOut]:
    """Submit a validated set for G1.

    Returns **approval tasks**, and the response is ``202 Accepted`` rather than
    ``201 Created``, because no baseline exists yet and none will until the gate
    passes.
    """
    tasks = ApprovalService(session, actor).submit_versions_for_baseline(
        project_id=ProjectId(project_id), version_ids=payload.version_ids
    )
    return [ApprovalTaskOut.model_validate(t) for t in tasks]


@router.get("/projects/{project_id}/baselines", response_model=list[BaselineOut])
def list_baselines(
    project_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> list[BaselineOut]:
    service = BaselineService(session, actor)
    return [BaselineOut.model_validate(b) for b in service.list_for_project(ProjectId(project_id))]


@router.get("/baselines/{baseline_id}", response_model=BaselineDetailOut)
def get_baseline(
    baseline_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> BaselineDetailOut:
    service = BaselineService(session, actor)
    project_id, baseline = _find_baseline(actor, lambda pid: service.get(pid, baseline_id))
    members = service.member_versions(project_id, baseline_id)
    return BaselineDetailOut(
        baseline=BaselineOut.model_validate(baseline),
        members=[RequirementVersionOut.model_validate(m) for m in members],
    )


# --- audit ------------------------------------------------------------------


@router.get("/projects/{project_id}/audit", response_model=list[AuditEventOut])
def list_audit(
    project_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> list[AuditEventOut]:
    """The project's audit trail, in chain order.

    Read through the repository-layer authorization check first, so a caller
    outside the project gets the same answer as for a project that does not
    exist.
    """
    from reqpilot.domain.enums import Action, ResourceType
    from reqpilot.domain.policy import ResourceRef, require

    require(
        actor,
        Action.AUDIT_READ,
        ResourceRef(resource_type=ResourceType.AUDIT_EVENT, project_id=ProjectId(project_id)),
    )
    events = AuditService(session).list_for_project(project_id)
    return [AuditEventOut.model_validate(e) for e in events]
