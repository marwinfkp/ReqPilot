"""Demonstration UI for approval governance, traceability and documents (P8; M1).

Four pages per project: the single review queue with each current version's
readiness; the RTM and coverage read from the persisted trace graph; the
artefacts with their immutable version history; one artefact version with its
sections, citations, metadata and downloads.

**The UI is not the enforcement point.** Every rule P8 adds - the gates, the
readiness check, the baseline-only generation, the trace allowlist - holds with
these pages removed, and the tests exercise all of them without it. Templates
are autoescaped: requirement text is shown as text, never as markup.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from reqpilot.api.dependencies import CurrentActor, DbSession
from reqpilot.api.lookup import require_found
from reqpilot.domain.enums import Action, ArtifactFormat, ArtifactType, ResourceType
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle.states import PRE_APPROVAL_STATES
from reqpilot.domain.models.identity import Project
from reqpilot.domain.policy import Actor, ResourceRef, can, require
from reqpilot.domain.traceability import TraceLinkType, TraceNodeType
from reqpilot.repositories.baseline import BaselineRepository
from reqpilot.repositories.requirements import RequirementVersionRepository
from reqpilot.services.baseline import BaselineService
from reqpilot.services.documents import DEFAULT_SET, ArtifactService
from reqpilot.services.governance import (
    GovernanceFanOut,
    GovernanceReadinessService,
    UnifiedReviewQueue,
)
from reqpilot.services.requirements import RequirementService
from reqpilot.services.traceability import RTM_COLUMNS, TraceGraphSync, TraceQueryService

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)


def _project(session: DbSession, actor: Actor, project_id: uuid.UUID, action: Action) -> Project:
    pid = ProjectId(project_id)
    require(actor, action, ResourceRef(resource_type=ResourceType.PROJECT, project_id=pid))
    project = session.get(Project, pid)
    if project is None:  # pragma: no cover - membership implies existence
        raise LookupError("project not found")
    return project


def _can(actor: Actor, action: Action, pid: ProjectId) -> bool:
    return can(
        actor, action, ResourceRef(resource_type=ResourceType.PROJECT, project_id=pid)
    ).allowed


@router.get("/projects/{project_id}/governance", response_class=HTMLResponse)
def governance_page(
    project_id: uuid.UUID, request: Request, session: DbSession, actor: CurrentActor
) -> HTMLResponse:
    project = _project(session, actor, project_id, Action.GOVERNANCE_READ)
    pid = ProjectId(project.id)
    queue = UnifiedReviewQueue(session, actor).build(pid)
    readiness_rows = []
    if _can(actor, Action.RISK_READ, pid) and _can(actor, Action.COMPLIANCE_READ, pid):
        requirements = RequirementService(session, actor)
        readiness = GovernanceReadinessService(session, actor)
        for requirement in requirements.list_requirements(pid):
            if requirement.current_version_id is None:
                continue
            version = requirements.get_version(pid, requirement.current_version_id)
            if version is None:
                continue
            result = readiness.evaluate(pid, version, stage="submission")
            readiness_rows.append(
                {
                    "human_id": requirement.human_id,
                    "version": version,
                    "blockers": result.blockers,
                    "flaggable": version.state in PRE_APPROVAL_STATES,
                }
            )
    return TEMPLATES.TemplateResponse(
        request,
        "governance.html",
        {
            "actor": actor,
            "project": project,
            "queue": queue,
            "readiness": readiness_rows,
            "may_raise": _can(actor, Action.GATE_TASK_RAISE, pid),
            "may_flag": _can(actor, Action.ARCHITECTURE_FLAG, pid),
        },
    )


@router.post("/projects/{project_id}/governance/fan-out")
def governance_fan_out(
    project_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> RedirectResponse:
    GovernanceFanOut(session, actor).raise_required(ProjectId(project_id))
    return RedirectResponse(
        f"/ui/projects/{project_id}/governance", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/requirement-versions/{version_id}/architecture-flag")
def flag_version(
    version_id: uuid.UUID, session: DbSession, actor: CurrentActor, reason: str = Form(...)
) -> RedirectResponse:
    repo = RequirementVersionRepository(session, actor)
    pid, _version = require_found(
        actor,
        Action.REQUIREMENT_READ,
        ResourceType.REQUIREMENT_VERSION,
        lambda p: repo.get(p, version_id),
    )
    GovernanceFanOut(session, actor).flag_architecture_critical(pid, version_id, reason)
    return RedirectResponse(f"/ui/projects/{pid}/governance", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/projects/{project_id}/traceability", response_class=HTMLResponse)
def traceability_page(
    project_id: uuid.UUID,
    request: Request,
    session: DbSession,
    actor: CurrentActor,
    baseline_id: uuid.UUID | None = None,
) -> HTMLResponse:
    project = _project(session, actor, project_id, Action.TRACE_READ)
    pid = ProjectId(project.id)
    service = ArtifactService(session, actor)
    return TEMPLATES.TemplateResponse(
        request,
        "traceability.html",
        {
            "actor": actor,
            "project": project,
            "baselines": BaselineService(session, actor).list_for_project(pid),
            "baseline_id": baseline_id,
            "columns": RTM_COLUMNS,
            "rows": service.rtm_rows(pid, baseline_id),
            "coverage": service.coverage(pid, baseline_id),
            "may_sync": _can(actor, Action.TRACE_SYNC, pid),
        },
    )


@router.post("/projects/{project_id}/traceability/sync")
def traceability_sync(
    project_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> RedirectResponse:
    TraceGraphSync(session, actor).sync(ProjectId(project_id))
    return RedirectResponse(
        f"/ui/projects/{project_id}/traceability", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/projects/{project_id}/artifacts", response_class=HTMLResponse)
def artifacts_page(
    project_id: uuid.UUID, request: Request, session: DbSession, actor: CurrentActor
) -> HTMLResponse:
    project = _project(session, actor, project_id, Action.ARTIFACT_READ)
    return _artifacts(request, session, actor, project, outcomes=None)


def _artifacts(
    request: Request, session: DbSession, actor: Actor, project: Project, outcomes: object
) -> HTMLResponse:
    pid = ProjectId(project.id)
    service = ArtifactService(session, actor)
    return TEMPLATES.TemplateResponse(
        request,
        "artifacts.html",
        {
            "actor": actor,
            "project": project,
            "baselines": BaselineService(session, actor).list_for_project(pid),
            "artifacts": service.list_artifacts(pid),
            "versions": service.all_versions(pid),
            "types": list(DEFAULT_SET),
            "outcomes": outcomes,
            "may_generate": _can(actor, Action.ARTIFACT_GENERATE, pid),
        },
    )


@router.post("/baselines/{baseline_id}/artifacts", response_class=HTMLResponse)
def generate_artifacts(
    baseline_id: uuid.UUID,
    request: Request,
    session: DbSession,
    actor: CurrentActor,
    artifact_types: list[str] = Form(default=[]),
) -> HTMLResponse:
    repo = BaselineRepository(session, actor)
    pid, _baseline = require_found(
        actor, Action.BASELINE_READ, ResourceType.BASELINE, lambda p: repo.get(p, baseline_id)
    )
    types = tuple(ArtifactType(t) for t in artifact_types) or DEFAULT_SET
    outcomes = ArtifactService(session, actor).generate_set(pid, baseline_id, types)
    project = session.get(Project, pid)
    assert project is not None
    return _artifacts(request, session, actor, project, outcomes=outcomes)


@router.get("/artifact-versions/{version_id}", response_class=HTMLResponse)
def artifact_version_page(
    version_id: uuid.UUID, request: Request, session: DbSession, actor: CurrentActor
) -> HTMLResponse:
    service = ArtifactService(session, actor)
    pid, version = require_found(
        actor,
        Action.ARTIFACT_READ,
        ResourceType.ARTIFACT_VERSION,
        lambda p: service.get_version(p, version_id),
    )
    graph = (
        TraceQueryService(session, actor).graph(pid)
        if _can(actor, Action.TRACE_READ, pid)
        else None
    )
    sections = [
        {
            "row": s,
            "cites": graph.targets(TraceNodeType.ARTIFACT_SECTION, s.id, TraceLinkType.CITES)
            if graph
            else [],
        }
        for s in service.sections(pid, version.id)
    ]
    return TEMPLATES.TemplateResponse(
        request,
        "artifact.html",
        {
            "actor": actor,
            "project_id": pid,
            "version": version,
            "sections": sections,
            "formats": [ArtifactFormat.MARKDOWN, ArtifactFormat.DOCX]
            + ([ArtifactFormat.CSV] if version.artifact_type is ArtifactType.RTM else []),
        },
    )


@router.get("/artifact-versions/{version_id}/download")
def download(
    version_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    format: ArtifactFormat = ArtifactFormat.MARKDOWN,
) -> Response:
    service = ArtifactService(session, actor)
    pid, _version = require_found(
        actor,
        Action.ARTIFACT_READ,
        ResourceType.ARTIFACT_VERSION,
        lambda p: service.get_version(p, version_id),
    )
    export = service.export(pid, version_id, format)
    return Response(
        content=export.data,
        media_type=export.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{export.filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
