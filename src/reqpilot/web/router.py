"""Module M1 - the minimal demonstration UI (architecture ADR-008).

Server-rendered Jinja2. Deliberately plain: this exists to *demonstrate* the
governance workflow, not to be a product surface. There are no dashboards, no
analytics and no charts.

The property that matters, and the reason the architecture chose server
rendering: **authorization is evaluated server-side on every request**. The
templates hide actions the actor may not take, but hiding a button is a
convenience - the service layer refuses the action regardless, and the UI tests
prove that by driving the same service paths.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from reqpilot.api.dependencies import CurrentActor, DbSession
from reqpilot.api.lookup import require_found
from reqpilot.domain.classification import display_label
from reqpilot.domain.enums import (
    Action,
    ApprovalDecisionType,
    ApprovalTaskStatus,
    RequirementCategory,
    RequirementPriority,
    ResourceType,
    Role,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import RequirementState, allowed_targets, check_transition
from reqpilot.domain.models.identity import Project
from reqpilot.domain.policy import Actor
from reqpilot.domain.requirement_ids import RequirementKind
from reqpilot.services.approval import ApprovalService, required_roles, requires_all_roles
from reqpilot.services.audit import AuditService
from reqpilot.services.baseline import BaselineService
from reqpilot.services.classification import RELABELLABLE_STATES, ClassificationService
from reqpilot.services.extraction import RequirementRecordService
from reqpilot.services.requirements import RequirementContent, RequirementService
from reqpilot.services.review.service import ReviewQueueService

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
TEMPLATES.env.globals["label"] = display_label

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)


def _projects_for(session: Session, actor: Actor) -> list[Project]:
    """The projects this actor belongs to, in name order."""
    out: list[Project] = []
    for project_id in actor.roles_by_project:
        project = session.get(Project, project_id)
        if project is not None:
            out.append(project)
    return sorted(out, key=lambda p: p.name)


@router.get("/", response_class=HTMLResponse)
def index(request: Request, session: DbSession, actor: CurrentActor) -> HTMLResponse:
    """Project picker. Shows only projects the actor belongs to."""
    return TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {
            "projects": _projects_for(session, actor),
            "actor": actor,
            "roles_by_project": actor.roles_by_project,
        },
    )


@router.get("/projects/{project_id}", response_class=HTMLResponse)
def project_view(
    project_id: uuid.UUID, request: Request, session: DbSession, actor: CurrentActor
) -> HTMLResponse:
    """Requirements, open approval tasks and baselines for one project."""
    pid = ProjectId(project_id)
    project = session.get(Project, project_id)
    if project is None or pid not in actor.roles_by_project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    service = RequirementService(session, actor)
    requirements = service.list_requirements(pid)
    rows = []
    for requirement in requirements:
        current = (
            service.get_version(pid, requirement.current_version_id)
            if requirement.current_version_id
            else None
        )
        rows.append({"requirement": requirement, "current": current})

    return TEMPLATES.TemplateResponse(
        request,
        "project.html",
        {
            "project": project,
            "rows": rows,
            "tasks": ApprovalService(session, actor).list_open_tasks(pid),
            "baselines": BaselineService(session, actor).list_for_project(pid),
            "actor": actor,
            "actor_roles": sorted(actor.roles_in(pid)),
            "categories": list(RequirementCategory),
            "priorities": list(RequirementPriority),
            "kinds": list(RequirementKind),
        },
    )


@router.post("/projects/{project_id}/requirements")
def create_requirement(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    statement: str = Form(...),
    domain: str = Form(...),
    kind: str = Form(RequirementKind.FUNCTIONAL.value),
    category: str = Form(""),
    priority: str = Form(""),
    justification: str = Form(""),
    source_ref: str = Form("interview-1"),
) -> RedirectResponse:
    """Create a requirement. Authorization is the service's job, not the form's."""
    RequirementService(session, actor).create_requirement(
        project_id=ProjectId(project_id),
        domain=domain,
        kind=RequirementKind(kind),
        content=RequirementContent(
            statement=statement,
            category=RequirementCategory(category) if category else None,
            priority=RequirementPriority(priority) if priority else None,
            justification=justification or None,
            source_refs=({"kind": "utterance", "ref": source_ref},),
        ),
    )
    return RedirectResponse(f"/ui/projects/{project_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/requirements/{requirement_id}", response_class=HTMLResponse)
def requirement_view(
    requirement_id: uuid.UUID, request: Request, session: DbSession, actor: CurrentActor
) -> HTMLResponse:
    """One requirement: current version, full history, and what may happen next."""
    service = RequirementService(session, actor)
    pid, requirement = require_found(
        actor,
        Action.REQUIREMENT_READ,
        ResourceType.REQUIREMENT,
        lambda p: service.get_requirement(p, requirement_id),
    )
    versions = service.version_history(pid, requirement.id)
    current = next((v for v in versions if v.id == requirement.current_version_id), None)
    targets: list[RequirementState] = []
    if current is not None:
        ctx = service.build_context(pid, current)
        targets = sorted(
            t
            for t in allowed_targets(current.state)
            if check_transition(current.state, t, ctx) is None
        )

    tasks = [
        t
        for t in ApprovalService(session, actor).list_tasks(pid)
        if any(t.subject_id == v.id for v in versions)
    ]
    # P3: the normalised record, labels with their history, criteria, reviews.
    record = RequirementRecordService(session, actor).record(pid, requirement.id)
    classification = ClassificationService(session, actor)
    history = classification.history(pid, current.id) if current else []
    review_items = (
        [
            i
            for i in ReviewQueueService(session, actor).list(pid)
            if i.requirement_version_id == current.id
        ]
        if current and record.open_review_items is not None
        else []
    )
    return TEMPLATES.TemplateResponse(
        request,
        "requirement.html",
        {
            "project_id": pid,
            "requirement": requirement,
            "versions": versions,
            "current": current,
            "targets": targets,
            "tasks": tasks,
            "actor": actor,
            "actor_roles": sorted(actor.roles_in(pid)),
            "categories": list(RequirementCategory),
            "priorities": list(RequirementPriority),
            "record": record,
            "label_history": history,
            "review_items": review_items,
            "may_override": current is not None
            and current.state in RELABELLABLE_STATES
            and Role.ANALYST in actor.roles_in(pid),
            "signal_caveat": "review-prioritisation signal, not a calibrated probability",
        },
    )


@router.post("/requirements/{requirement_id}/versions")
def create_version(
    requirement_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    statement: str = Form(...),
    change_reason: str = Form(...),
    category: str = Form(""),
    priority: str = Form(""),
) -> RedirectResponse:
    """Create a successor version. The predecessor is never edited."""
    service = RequirementService(session, actor)
    pid, requirement = require_found(
        actor,
        Action.REQUIREMENT_READ,
        ResourceType.REQUIREMENT,
        lambda p: service.get_requirement(p, requirement_id),
    )
    current = (
        service.get_version(pid, requirement.current_version_id)
        if requirement.current_version_id
        else None
    )
    service.create_version(
        project_id=pid,
        requirement_id=requirement.id,
        content=RequirementContent(
            statement=statement,
            category=RequirementCategory(category)
            if category
            else (current.category if current else None),
            priority=RequirementPriority(priority)
            if priority
            else (current.priority if current else None),
            source_refs=tuple(current.source_refs) if current else (),
        ),
        change_reason=change_reason,
    )
    return RedirectResponse(
        f"/ui/requirements/{requirement_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/requirement-versions/{version_id}/transition")
def transition(
    version_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    target: str = Form(...),
    requirement_id: str = Form(...),
) -> RedirectResponse:
    service = RequirementService(session, actor)
    pid, _version = require_found(
        actor,
        Action.REQUIREMENT_READ,
        ResourceType.REQUIREMENT_VERSION,
        lambda p: service.get_version(p, version_id),
    )
    service.transition(project_id=pid, version_id=version_id, target=RequirementState(target))
    return RedirectResponse(
        f"/ui/requirements/{requirement_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/projects/{project_id}/submit")
def submit_for_baseline(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    version_ids: list[str] = Form(default=[]),
) -> RedirectResponse:
    """Submit the selected validated versions for G1."""
    if version_ids:
        ApprovalService(session, actor).submit_versions_for_baseline(
            project_id=ProjectId(project_id),
            version_ids=[uuid.UUID(v) for v in version_ids],
        )
    return RedirectResponse(f"/ui/projects/{project_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/projects/{project_id}/tasks", response_class=HTMLResponse)
def task_queue(
    project_id: uuid.UUID, request: Request, session: DbSession, actor: CurrentActor
) -> HTMLResponse:
    """The review queue, with what each gate requires made explicit."""
    pid = ProjectId(project_id)
    if pid not in actor.roles_by_project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    approvals = ApprovalService(session, actor)
    requirements = RequirementService(session, actor)
    rows = []
    all_tasks = approvals.list_tasks(pid)
    for task in all_tasks:
        decisions = approvals.decisions_for(pid, task.id)
        version = requirements.get_version(pid, task.subject_id)

        # A co-approval gate raises one task per role. Show the reviewer the
        # whole picture for this subject, not just their own slice, so they can
        # see which other signature is still outstanding.
        siblings = [
            t
            for t in all_tasks
            if t.subject_id == task.subject_id
            and t.gate is task.gate
            and t.task_group_id == task.task_group_id
        ]
        signed = {t.required_role for t in siblings if t.status is ApprovalTaskStatus.APPROVED}

        rows.append(
            {
                "task": task,
                "version": version,
                "decisions": decisions,
                "needed": sorted(required_roles(task.gate)),
                "outstanding": sorted({t.required_role for t in siblings} - signed),
                "requires_all": requires_all_roles(task.gate),
                # Each task names one role; the form offers only that role.
                "this_role": task.required_role,
                # The author may not approve their own version, so the UI says so
                # rather than offering a button that will be refused.
                "is_author": version is not None and version.created_by == actor.actor_id,
                "can_sign": task.required_role in actor.roles_in(pid),
            }
        )

    return TEMPLATES.TemplateResponse(
        request,
        "tasks.html",
        {
            "project_id": pid,
            "rows": rows,
            "actor": actor,
            "actor_roles": sorted(actor.roles_in(pid)),
            "all_roles": list(Role),
        },
    )


@router.post("/approval-tasks/{task_id}/decide")
def decide(
    task_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    project_id: str = Form(...),
    decision: str = Form(...),
    role_exercised: str = Form(...),
    justification: str = Form(""),
    baseline_label: str = Form(""),
) -> RedirectResponse:
    """Record a decision. The service refuses anything the policy forbids."""
    ApprovalService(session, actor).decide(
        project_id=ProjectId(uuid.UUID(project_id)),
        task_id=task_id,
        decision=ApprovalDecisionType(decision),
        role_exercised=Role(role_exercised),
        justification=justification or None,
        baseline_label=baseline_label or None,
    )
    return RedirectResponse(
        f"/ui/projects/{project_id}/tasks", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/baselines/{baseline_id}", response_class=HTMLResponse)
def baseline_view(
    baseline_id: uuid.UUID, request: Request, session: DbSession, actor: CurrentActor
) -> HTMLResponse:
    service = BaselineService(session, actor)
    pid, baseline = require_found(
        actor,
        Action.BASELINE_READ,
        ResourceType.BASELINE,
        lambda p: service.get(p, baseline_id),
    )
    return TEMPLATES.TemplateResponse(
        request,
        "baseline.html",
        {
            "baseline": baseline,
            "members": service.member_versions(pid, baseline_id),
            "project_id": pid,
            "actor": actor,
        },
    )


@router.get("/projects/{project_id}/audit", response_class=HTMLResponse)
def audit_view(
    project_id: uuid.UUID, request: Request, session: DbSession, actor: CurrentActor
) -> HTMLResponse:
    """The audit trail. Read-only, chain-ordered, references only."""
    from reqpilot.domain.enums import Action, ResourceType
    from reqpilot.domain.policy import ResourceRef, require

    pid = ProjectId(project_id)
    require(
        actor,
        Action.AUDIT_READ,
        ResourceRef(resource_type=ResourceType.AUDIT_EVENT, project_id=pid),
    )
    audit = AuditService(session)
    ok, first_bad = audit.verify_project_chain(project_id)
    return TEMPLATES.TemplateResponse(
        request,
        "audit.html",
        {
            "project_id": pid,
            "events": audit.list_for_project(project_id),
            "chain_ok": ok,
            "first_bad": first_bad,
            "actor": actor,
        },
    )
