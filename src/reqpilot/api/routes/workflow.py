"""Project workflow endpoints (P10; ``FR-WFL-001``..``-008``; architecture S, M.4).

* ``POST /sdlc-runs/{id}/workflow`` - generate the project workflow of a run that
  passed G6 (Analyst or Project Manager; ``WORKFLOW_GENERATE``). 201 with the new
  workflow, 200 when the same run and approved inputs already had it, **409** with
  the validation findings when refused (G6 not passed, a pending G8 or G3, a
  validation failure). The body carries a run id only - nothing a client sends can
  stand in for the persisted G6 decisions.
* ``GET  /sdlc-runs/{id}/workflow`` - the run's workflow (architecture S), including
  the production-readiness gate.
* ``GET  /projects/{id}/workflows`` and ``GET /workflows/{id}`` - read, with each
  element's provenance and the open items.
* ``PATCH /workflows/{id}/phases|activities|gates/{element_id}``, ``POST
  /workflows/{id}/phases/{phase_id}/activities`` and ``POST
  /workflows/{id}/activities/{activity_id}/remove`` - the Project Manager's edits,
  each with a reason (``FR-WFL-007``). A mandatory element cannot be removed and
  no edit can touch provenance; an edit that fails validation is a 409.
* ``GET  /workflows/{id}/changes`` - the change log.
* ``GET  /workflows/{id}/export?format=markdown|docx`` - ``FR-WFL-008``.

Every handler authorises through the policy; a workflow or run outside the caller's
reach is a 404, exactly as for one that does not exist.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from reqpilot.api.dependencies import (
    AppSettings,
    CurrentActor,
    DbSession,
    Gateway,
    RiskRulesDep,
    SdlcRulesDep,
)
from reqpilot.api.lookup import require_found
from reqpilot.api.workflow_schemas import (
    ActivityAddIn,
    ActivityEditIn,
    ActivityOut,
    ActivityRemoveIn,
    ChangeOut,
    FindingOut,
    GateEditIn,
    GateOut,
    PhaseEditIn,
    PhaseOut,
    SourceOut,
    WorkflowGenerateOut,
    WorkflowOut,
    WorkflowSummaryOut,
    edit_changes,
)
from reqpilot.domain.enums import Action, ArtifactFormat, ResourceType, WorkflowStatus
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.workflow import Workflow, WorkflowChange
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.domain.workflow.edits import AddActivity, WorkflowEdit
from reqpilot.graph.sdlc_runner import SdlcRunner
from reqpilot.services.sdlc.service import SdlcService
from reqpilot.services.workflow import WorkflowService, WorkflowView

router = APIRouter(prefix="/api/v1", tags=["workflow"])


def _find_workflow(
    actor: Actor, session: Session, workflow_id: uuid.UUID
) -> tuple[ProjectId, Workflow]:
    service = WorkflowService(session, actor)
    return require_found(
        actor,
        Action.WORKFLOW_READ,
        ResourceType.WORKFLOW,
        lambda pid: service.get(pid, workflow_id),
    )


def _sources(view: WorkflowView, links: tuple) -> list[SourceOut]:  # type: ignore[type-arg]
    return [
        SourceOut(
            source_type=s.source_type,
            source_id=s.source_id,
            relation=s.relation,
            label=view.source_labels.get((s.source_type, s.source_id), s.source_type),
        )
        for s in links
    ]


def workflow_out(view: WorkflowView) -> WorkflowOut:
    workflow, plan = view.workflow, view.plan
    phase_rows = {p.key: p for p in view.phases}
    activity_rows = {a.key: a for a in view.activities}
    gate_rows = {g.key: g for g in view.gates}
    return WorkflowOut(
        id=workflow.id,
        project_id=workflow.project_id,
        sdlc_run_id=workflow.sdlc_run_id,
        selected_candidate_id=workflow.selected_candidate_id,
        candidate_key=workflow.candidate_key,
        candidate_label=plan.candidate_label,
        composition=list(plan.composition),
        approach=plan.approach,
        baseline_id=workflow.baseline_id,
        supersedes_workflow_id=workflow.supersedes_workflow_id,
        status=workflow.status,
        revision=workflow.revision,
        template_ref=workflow.template_ref,
        input_fingerprint=workflow.input_fingerprint,
        generated_hash=workflow.generated_hash,
        content_hash=workflow.content_hash,
        realises=_sources(view, plan.sources),
        open_items=[FindingOut(**f.as_dict()) for f in plan.open_items],
        phases=[
            PhaseOut(
                id=phase_rows[p.key].id,
                position=phase_rows[p.key].position,
                key=p.key,
                name=p.name,
                description=p.description,
                stages=list(p.stages),
                cycle=p.cycle,
                verifies_phase_key=p.verifies,
                responsible_roles=list(p.responsible_roles),
                deliverables=list(p.deliverables),
                entry_criteria=list(p.entry_criteria),
                exit_criteria=list(p.exit_criteria),
                testing_requirements=list(p.testing_requirements),
                traceability_requirements=list(p.traceability_requirements),
                origin=p.origin,
                activities=[
                    ActivityOut(
                        id=activity_rows[a.key].id,
                        key=a.key,
                        kind=a.kind,
                        name=a.name,
                        description=a.description,
                        responsible_roles=list(a.responsible_roles),
                        deliverables=list(a.deliverables),
                        mandatory=a.mandatory,
                        origin=a.origin,
                        sources=_sources(view, a.sources),
                    )
                    for a in p.activities
                ],
                gates=[
                    GateOut(
                        id=gate_rows[g.key].id,
                        key=g.key,
                        kind=g.kind,
                        name=g.name,
                        purpose=g.purpose,
                        approver_roles=list(g.approver_roles),
                        required_evidence=list(g.required_evidence),
                        entry_criteria=list(g.entry_criteria),
                        exit_criteria=list(g.exit_criteria),
                        mandatory=g.mandatory,
                        origin=g.origin,
                        sources=_sources(view, g.sources),
                    )
                    for g in p.gates
                ],
            )
            for p in plan.phases
        ],
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


def change_out(change: WorkflowChange) -> ChangeOut:
    return ChangeOut(
        id=change.id,
        revision=change.revision,
        operation=change.operation,
        element_type=change.element_type,
        element_id=change.element_id,
        element_key=change.element_key,
        changes=dict(change.changes),
        reason=change.reason,
        actor_id=change.actor_id,
        role_exercised=change.role_exercised,
        content_hash_before=change.content_hash_before,
        content_hash_after=change.content_hash_after,
        created_at=change.created_at,
    )


# ----------------------------------------------------------------------
# generation and reading
# ----------------------------------------------------------------------
@router.post("/sdlc-runs/{run_id}/workflow", response_model=None)
def generate_workflow(
    run_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    gateway: Gateway,
    sdlc_rules: SdlcRulesDep,
    risk_rules: RiskRulesDep,
    settings: AppSettings,
) -> JSONResponse:
    """Generate (or return) the workflow of a G6-selected run; 409 with findings if refused."""
    pid, run = require_found(
        actor,
        Action.WORKFLOW_GENERATE,
        ResourceType.WORKFLOW,
        lambda p: SdlcService(session, actor, sdlc_rules).get(p, run_id),
    )
    summary = SdlcRunner(
        session, gateway, settings=settings, sdlc_rules=sdlc_rules, risk_rules=risk_rules
    ).generate_workflow(actor=actor, project_id=pid, run_id=run.id)
    out = WorkflowGenerateOut(
        graph_run_id=summary.graph_run_id,
        status=summary.status,
        sdlc_run_id=summary.sdlc_run_id,
        workflow_id=summary.workflow_id,
        workflow_status=summary.workflow_status,
        reused=summary.workflow_reused,
        errors=list(summary.errors),
        findings=list(summary.workflow_findings),
    )
    if summary.workflow_id is None:
        code = status.HTTP_409_CONFLICT
    elif summary.workflow_reused:
        code = status.HTTP_200_OK
    else:
        code = status.HTTP_201_CREATED
    return JSONResponse(status_code=code, content=out.model_dump(mode="json"))


@router.get("/sdlc-runs/{run_id}/workflow", response_model=WorkflowOut)
def run_workflow(
    run_id: uuid.UUID, session: DbSession, actor: CurrentActor, sdlc_rules: SdlcRulesDep
) -> WorkflowOut:
    service = WorkflowService(session, actor, sdlc_rules=sdlc_rules)
    pid, workflow = require_found(
        actor,
        Action.WORKFLOW_READ,
        ResourceType.WORKFLOW,
        lambda p: service.for_run(p, run_id),
    )
    return workflow_out(service.view(pid, workflow))


@router.get("/projects/{project_id}/workflows", response_model=list[WorkflowSummaryOut])
def list_workflows(
    project_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> list[WorkflowSummaryOut]:
    pid = ProjectId(project_id)
    require(
        actor,
        Action.WORKFLOW_READ,
        ResourceRef(resource_type=ResourceType.WORKFLOW, project_id=pid),
    )
    return [
        WorkflowSummaryOut(
            id=w.id,
            sdlc_run_id=w.sdlc_run_id,
            candidate_key=w.candidate_key,
            status=w.status,
            revision=w.revision,
            open_items=len(w.open_items or []),
            created_at=w.created_at,
            updated_at=w.updated_at,
        )
        for w in WorkflowService(session, actor).list_workflows(pid)
    ]


@router.get("/workflows/{workflow_id}", response_model=WorkflowOut)
def get_workflow(workflow_id: uuid.UUID, session: DbSession, actor: CurrentActor) -> WorkflowOut:
    pid, workflow = _find_workflow(actor, session, workflow_id)
    return workflow_out(WorkflowService(session, actor).view(pid, workflow))


@router.get("/workflows/{workflow_id}/changes", response_model=list[ChangeOut])
def workflow_changes(
    workflow_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> list[ChangeOut]:
    pid, workflow = _find_workflow(actor, session, workflow_id)
    return [change_out(c) for c in WorkflowService(session, actor).changes(pid, workflow.id)]


# ----------------------------------------------------------------------
# editing (FR-WFL-007; the Project Manager)
# ----------------------------------------------------------------------
def _edit(
    session: Session,
    actor: Actor,
    workflow_id: uuid.UUID,
    reason: str,
    build: object,
) -> ChangeOut:
    pid, workflow = _find_workflow(actor, session, workflow_id)
    require(
        actor,
        Action.WORKFLOW_EDIT,
        ResourceRef(resource_type=ResourceType.WORKFLOW, project_id=pid),
    )
    if workflow.status is WorkflowStatus.SUPERSEDED:
        from reqpilot.services.workflow.sources import refused

        raise refused("WORKFLOW_SUPERSEDED", "a superseded workflow is kept unchanged")
    service = WorkflowService(session, actor)
    edit: WorkflowEdit = build(service, pid, workflow)  # type: ignore[operator]
    return change_out(service.edit(pid, workflow.id, edit, reason=reason))


@router.patch("/workflows/{workflow_id}/phases/{phase_id}", response_model=ChangeOut)
def edit_phase(
    workflow_id: uuid.UUID,
    phase_id: uuid.UUID,
    payload: PhaseEditIn,
    session: DbSession,
    actor: CurrentActor,
) -> ChangeOut:
    return _edit(
        session,
        actor,
        workflow_id,
        payload.reason,
        lambda s, pid, w: s.edit_for_element(
            pid, w.id, element_type="phase", element_id=phase_id, changes=edit_changes(payload)
        ),
    )


@router.patch("/workflows/{workflow_id}/activities/{activity_id}", response_model=ChangeOut)
def edit_activity(
    workflow_id: uuid.UUID,
    activity_id: uuid.UUID,
    payload: ActivityEditIn,
    session: DbSession,
    actor: CurrentActor,
) -> ChangeOut:
    return _edit(
        session,
        actor,
        workflow_id,
        payload.reason,
        lambda s, pid, w: s.edit_for_element(
            pid,
            w.id,
            element_type="activity",
            element_id=activity_id,
            changes=edit_changes(payload),
        ),
    )


@router.patch("/workflows/{workflow_id}/gates/{gate_id}", response_model=ChangeOut)
def edit_gate(
    workflow_id: uuid.UUID,
    gate_id: uuid.UUID,
    payload: GateEditIn,
    session: DbSession,
    actor: CurrentActor,
) -> ChangeOut:
    return _edit(
        session,
        actor,
        workflow_id,
        payload.reason,
        lambda s, pid, w: s.edit_for_element(
            pid, w.id, element_type="gate", element_id=gate_id, changes=edit_changes(payload)
        ),
    )


@router.post(
    "/workflows/{workflow_id}/phases/{phase_id}/activities",
    response_model=ChangeOut,
    status_code=status.HTTP_201_CREATED,
)
def add_activity(
    workflow_id: uuid.UUID,
    phase_id: uuid.UUID,
    payload: ActivityAddIn,
    session: DbSession,
    actor: CurrentActor,
) -> ChangeOut:
    return _edit(
        session,
        actor,
        workflow_id,
        payload.reason,
        lambda s, pid, w: AddActivity(
            phase_key=s.phase_key(pid, w.id, phase_id),
            name=payload.name,
            description=payload.description,
            responsible_roles=tuple(payload.responsible_roles),
            deliverables=tuple(payload.deliverables),
        ),
    )


@router.post("/workflows/{workflow_id}/activities/{activity_id}/remove", response_model=ChangeOut)
def remove_activity(
    workflow_id: uuid.UUID,
    activity_id: uuid.UUID,
    payload: ActivityRemoveIn,
    session: DbSession,
    actor: CurrentActor,
) -> ChangeOut:
    return _edit(
        session,
        actor,
        workflow_id,
        payload.reason,
        lambda s, pid, w: s.edit_for_element(
            pid, w.id, element_type="activity", element_id=activity_id, remove=True
        ),
    )


# ----------------------------------------------------------------------
# export (FR-WFL-008)
# ----------------------------------------------------------------------
@router.get("/workflows/{workflow_id}/export", response_model=None)
def export_workflow(
    workflow_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    format: ArtifactFormat = ArtifactFormat.MARKDOWN,
) -> Response:
    pid, workflow = _find_workflow(actor, session, workflow_id)
    export = WorkflowService(session, actor).export(pid, workflow.id, format)
    return Response(
        content=export.data,
        media_type=export.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{export.filename}"',
            "X-Content-SHA256": export.sha256,
            "X-Content-Type-Options": "nosniff",
        },
    )
