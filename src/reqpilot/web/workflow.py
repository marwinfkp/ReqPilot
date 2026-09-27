"""Demonstration UI for project workflows (P10; M1).

Two pages: a project's workflows (with a form to generate the workflow of an SDLC
run that passed G6), and one workflow - the selected SDLC and its G6 status, the
open items, every phase in order with its activities, gates, roles, deliverables,
criteria and testing and traceability requirements, the record each compliance
checkpoint and risk activity was derived from, the Project Manager's edit forms,
the change log, and Markdown / DOCX export links.

The page states plainly which gates are which: G1-G8 are ReqPilot's and are decided
in the approval queue; the gates *in* a generated workflow - production readiness
included - belong to the generated project's own process.

**The UI is not the enforcement point.** Every rule P10 adds - G6 verified from the
persisted records, fail-closed validation, mandatory elements and provenance fixed,
Project Manager-only edits with a logged reason - holds with these pages removed.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from reqpilot.api.dependencies import (
    AppSettings,
    CurrentActor,
    DbSession,
    Gateway,
    RiskRulesDep,
    SdlcRulesDep,
)
from reqpilot.api.lookup import require_found
from reqpilot.api.routes.workflow import workflow_out
from reqpilot.api.workflow_schemas import WORKFLOW_NOTICE
from reqpilot.domain.enums import Action, ResourceType, SdlcRunStatus, WorkflowStatus
from reqpilot.domain.errors import WorkflowError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.identity import Project
from reqpilot.domain.policy import Actor, ResourceRef, can, require
from reqpilot.domain.workflow.edits import (
    ACTIVITY_LIST_FIELDS,
    ACTIVITY_TEXT_FIELDS,
    GATE_LIST_FIELDS,
    GATE_TEXT_FIELDS,
    PHASE_LIST_FIELDS,
    PHASE_TEXT_FIELDS,
    AddActivity,
)
from reqpilot.graph.sdlc_runner import SdlcRunner
from reqpilot.services.sdlc.service import SdlcService
from reqpilot.services.workflow import WorkflowService

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)

_FIELDS = {
    "phase": (PHASE_TEXT_FIELDS, PHASE_LIST_FIELDS),
    "activity": (ACTIVITY_TEXT_FIELDS, ACTIVITY_LIST_FIELDS),
    "gate": (GATE_TEXT_FIELDS, GATE_LIST_FIELDS),
}


def _may(actor: Actor, pid: ProjectId, action: Action) -> bool:
    return can(
        actor, action, ResourceRef(resource_type=ResourceType.WORKFLOW, project_id=pid)
    ).allowed


def _codes(exc: WorkflowError) -> str:
    """Only the finding codes travel in a redirect URL - never free text."""
    codes = sorted({str(f.get("code", "")) for f in exc.findings if f.get("code")})
    return ",".join(codes[:8]) or "REFUSED"


@router.get("/projects/{project_id}/workflows", response_class=HTMLResponse)
def workflows_page(
    project_id: uuid.UUID,
    request: Request,
    session: DbSession,
    actor: CurrentActor,
    sdlc_rules: SdlcRulesDep,
    refused: str | None = None,
) -> HTMLResponse:
    pid = ProjectId(project_id)
    require(
        actor,
        Action.WORKFLOW_READ,
        ResourceRef(resource_type=ResourceType.WORKFLOW, project_id=pid),
    )
    may_generate = _may(actor, pid, Action.WORKFLOW_GENERATE)
    selected = (
        [
            r
            for r in SdlcService(session, actor, sdlc_rules).list_runs(pid)
            if r.status is SdlcRunStatus.SELECTED
        ]
        if may_generate
        else []
    )
    return TEMPLATES.TemplateResponse(
        request,
        "workflows.html",
        {
            "actor": actor,
            "project": session.get(Project, pid),
            "notice": WORKFLOW_NOTICE,
            "workflows": WorkflowService(session, actor).list_workflows(pid),
            "selected_runs": selected,
            "may_generate": may_generate,
            "refused": [c for c in (refused or "").split(",") if c],
        },
    )


@router.post("/sdlc-runs/{run_id}/workflow")
def generate(
    run_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    gateway: Gateway,
    sdlc_rules: SdlcRulesDep,
    risk_rules: RiskRulesDep,
    settings: AppSettings,
) -> RedirectResponse:
    pid, run = require_found(
        actor,
        Action.WORKFLOW_GENERATE,
        ResourceType.WORKFLOW,
        lambda p: SdlcService(session, actor, sdlc_rules).get(p, run_id),
    )
    summary = SdlcRunner(
        session, gateway, settings=settings, sdlc_rules=sdlc_rules, risk_rules=risk_rules
    ).generate_workflow(actor=actor, project_id=pid, run_id=run.id)
    if summary.workflow_id is None:
        codes = ",".join(sorted({f["code"] for f in summary.workflow_findings})[:8]) or "REFUSED"
        return RedirectResponse(
            f"/ui/projects/{pid}/workflows?refused={codes}", status_code=status.HTTP_303_SEE_OTHER
        )
    return RedirectResponse(
        f"/ui/workflows/{summary.workflow_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/workflows/{workflow_id}", response_class=HTMLResponse)
def workflow_page(
    workflow_id: uuid.UUID,
    request: Request,
    session: DbSession,
    actor: CurrentActor,
    error: str | None = None,
) -> HTMLResponse:
    service = WorkflowService(session, actor)
    pid, workflow = require_found(
        actor, Action.WORKFLOW_READ, ResourceType.WORKFLOW, lambda p: service.get(p, workflow_id)
    )
    view = service.view(pid, workflow)
    return TEMPLATES.TemplateResponse(
        request,
        "workflow.html",
        {
            "actor": actor,
            "project": session.get(Project, pid),
            "wf": workflow_out(view),
            "changes": view.changes,
            "roles": service.templates.roles,
            "may_edit": _may(actor, pid, Action.WORKFLOW_EDIT)
            and workflow.status is not WorkflowStatus.SUPERSEDED,
            "may_export": _may(actor, pid, Action.WORKFLOW_EXPORT),
            "fields": {kind: sorted(text | lists) for kind, (text, lists) in _FIELDS.items()},
            "error": [c for c in (error or "").split(",") if c],
        },
    )


def _value(field: str, element_type: str, raw: str) -> object:
    _text, lists = _FIELDS[element_type]
    if field in lists:
        return [line.strip() for line in raw.splitlines() if line.strip()]
    return raw


def _back(workflow_id: uuid.UUID, exc: WorkflowError | None = None) -> RedirectResponse:
    suffix = f"?error={_codes(exc)}" if exc is not None else ""
    return RedirectResponse(
        f"/ui/workflows/{workflow_id}{suffix}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/workflows/{workflow_id}/{element_type}/{element_id}/edit")
def edit_element(
    workflow_id: uuid.UUID,
    element_type: str,
    element_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    field: str = Form(...),
    value: str = Form(""),
    reason: str = Form(""),
) -> RedirectResponse:
    service = WorkflowService(session, actor)
    pid, workflow = require_found(
        actor, Action.WORKFLOW_READ, ResourceType.WORKFLOW, lambda p: service.get(p, workflow_id)
    )
    if element_type not in _FIELDS:
        return _back(
            workflow_id, WorkflowError("unknown element", ({"code": "EDIT_UNKNOWN_ELEMENT"},))
        )
    try:
        edit = service.edit_for_element(
            pid,
            workflow.id,
            element_type=element_type,
            element_id=element_id,
            changes={field: _value(field, element_type, value)},
        )
        service.edit(pid, workflow.id, edit, reason=reason)
    except WorkflowError as exc:
        return _back(workflow_id, exc)
    return _back(workflow_id)


@router.post("/workflows/{workflow_id}/phases/{phase_id}/activities")
def add_activity(
    workflow_id: uuid.UUID,
    phase_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    name: str = Form(...),
    description: str = Form(""),
    responsible_roles: str = Form(""),
    deliverables: str = Form(""),
    reason: str = Form(""),
) -> RedirectResponse:
    service = WorkflowService(session, actor)
    pid, workflow = require_found(
        actor, Action.WORKFLOW_READ, ResourceType.WORKFLOW, lambda p: service.get(p, workflow_id)
    )
    try:
        edit = AddActivity(
            phase_key=service.phase_key(pid, workflow.id, phase_id),
            name=name,
            description=description,
            responsible_roles=tuple(
                r.strip() for r in responsible_roles.replace(",", "\n").splitlines() if r.strip()
            ),
            deliverables=tuple(d.strip() for d in deliverables.splitlines() if d.strip()),
        )
        service.edit(pid, workflow.id, edit, reason=reason)
    except WorkflowError as exc:
        return _back(workflow_id, exc)
    return _back(workflow_id)


@router.post("/workflows/{workflow_id}/activities/{activity_id}/remove")
def remove_activity(
    workflow_id: uuid.UUID,
    activity_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    reason: str = Form(""),
) -> RedirectResponse:
    service = WorkflowService(session, actor)
    pid, workflow = require_found(
        actor, Action.WORKFLOW_READ, ResourceType.WORKFLOW, lambda p: service.get(p, workflow_id)
    )
    try:
        edit = service.edit_for_element(
            pid, workflow.id, element_type="activity", element_id=activity_id, remove=True
        )
        service.edit(pid, workflow.id, edit, reason=reason)
    except WorkflowError as exc:
        return _back(workflow_id, exc)
    return _back(workflow_id)
