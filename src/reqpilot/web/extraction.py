"""Module M1 - the extraction and review pages (architecture ADR-008, M.5).

Deliberately plain. What these pages must make obvious:

* **Batch mode.** The analyst adds a source, then runs extraction over it. There
  is no interview and no follow-up question (that is P4).
* **The model proposes.** Every requirement shows the verbatim source spans it
  came from, its review signal with the caveat that it is not a probability,
  and the prompt and model that produced it.
* **The review queue is not approval.** Resolving an item decides what happens
  to an AI proposal. Approval is gate G1, on the approvals page, unchanged.

As everywhere in the UI, hidden buttons are a convenience; every action goes
through the same services as the API, which authorise through ``policy.can``.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from reqpilot.api.dependencies import (
    AppSettings,
    CurrentActor,
    DbSession,
    ExtractionRulesDep,
    Gateway,
    Rules,
)
from reqpilot.api.lookup import require_found
from reqpilot.domain.classification import display_label
from reqpilot.domain.enums import (
    Action,
    DataSensitivity,
    RequirementCategory,
    ResourceType,
    ReviewReason,
    ReviewResolution,
    ReviewStatus,
    SourceDocumentType,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.identity import Project
from reqpilot.domain.policy import Actor, ResourceRef, can, require
from reqpilot.graph.runner import AnalysisRunner
from reqpilot.repositories.extraction import CandidateRepository, RunRepository
from reqpilot.repositories.requirements import RequirementVersionRepository
from reqpilot.services.classification import ClassificationService
from reqpilot.services.extraction import SourceDocumentService
from reqpilot.services.review.service import ALLOWED_RESOLUTIONS, ReviewQueueService

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
TEMPLATES.env.globals["label"] = display_label

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)

NOT_APPROVAL = (
    "This queue is for reviewing AI proposals. Resolving an item approves nothing: "
    "requirements are approved only at gate G1, by an Analyst and a Compliance Officer, "
    "on the approvals page."
)
SIGNAL_CAVEAT = "review-prioritisation signal, not a calibrated probability"


def _may(actor: Actor, action: Action, project_id: ProjectId) -> bool:
    return can(
        actor, action, ResourceRef(resource_type=ResourceType.PROJECT, project_id=project_id)
    ).allowed


def _project(session: DbSession, actor: Actor, project_id: uuid.UUID, action: Action) -> Project:
    pid = ProjectId(project_id)
    require(actor, action, ResourceRef(resource_type=ResourceType.PROJECT, project_id=pid))
    project = session.get(Project, pid)
    if project is None:  # pragma: no cover - membership implies existence
        raise LookupError("project not found")
    return project


def _see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# Sources and runs
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/sources", response_class=HTMLResponse)
def sources_page(
    project_id: uuid.UUID,
    request: Request,
    session: DbSession,
    actor: CurrentActor,
    rules: Rules,
    extraction_rules: ExtractionRulesDep,
    gateway: Gateway,
) -> HTMLResponse:
    project = _project(session, actor, project_id, Action.SOURCE_READ)
    pid = ProjectId(project.id)
    service = SourceDocumentService(
        session, actor, retrieval_rules=rules, extraction_rules=extraction_rules
    )
    return TEMPLATES.TemplateResponse(
        request,
        "sources.html",
        {
            "actor": actor,
            "project": project,
            "sources": service.list_documents(pid),
            "runs": RunRepository(session, actor).list_runs(pid),
            "doc_types": list(SourceDocumentType),
            "sensitivities": list(DataSensitivity),
            "may_add": _may(actor, Action.SOURCE_CREATE, pid),
            "may_run": _may(actor, Action.RUN_START, pid),
            "provider": gateway.provider_name,
            "provider_is_model": gateway.is_model,
        },
    )


@router.post("/projects/{project_id}/sources")
def add_source(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    rules: Rules,
    extraction_rules: ExtractionRulesDep,
    doc_type: SourceDocumentType = Form(...),
    title: str = Form(...),
    text: str = Form(...),
    sensitivity: DataSensitivity = Form(DataSensitivity.UNCLASSIFIED),
) -> RedirectResponse:
    SourceDocumentService(
        session, actor, retrieval_rules=rules, extraction_rules=extraction_rules
    ).add_text(
        project_id=ProjectId(project_id),
        doc_type=doc_type,
        title=title,
        text=text,
        sensitivity=sensitivity,
    )
    return _see_other(f"/ui/projects/{project_id}/sources")


@router.post("/projects/{project_id}/sources/upload")
async def upload_source(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    rules: Rules,
    extraction_rules: ExtractionRulesDep,
    file: UploadFile = File(...),
    doc_type: SourceDocumentType = Form(...),
    title: str = Form(...),
    sensitivity: DataSensitivity = Form(DataSensitivity.UNCLASSIFIED),
) -> RedirectResponse:
    data = await file.read()
    SourceDocumentService(
        session, actor, retrieval_rules=rules, extraction_rules=extraction_rules
    ).add_file(
        project_id=ProjectId(project_id),
        doc_type=doc_type,
        title=title,
        data=data,
        filename=file.filename or "upload.txt",
        sensitivity=sensitivity,
    )
    return _see_other(f"/ui/projects/{project_id}/sources")


@router.get("/sources/{source_id}", response_class=HTMLResponse)
def source_page(
    source_id: uuid.UUID,
    request: Request,
    session: DbSession,
    actor: CurrentActor,
    rules: Rules,
    extraction_rules: ExtractionRulesDep,
) -> HTMLResponse:
    service = SourceDocumentService(
        session, actor, retrieval_rules=rules, extraction_rules=extraction_rules
    )
    pid, document = require_found(
        actor, Action.SOURCE_READ, ResourceType.SOURCE_DOCUMENT, lambda p: service.get(p, source_id)
    )
    return TEMPLATES.TemplateResponse(
        request,
        "source.html",
        {
            "actor": actor,
            "project_id": pid,
            "document": document,
            "chunks": service.chunks(pid, document.id),
        },
    )


@router.post("/projects/{project_id}/analysis-runs")
def start_run(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    gateway: Gateway,
    extraction_rules: ExtractionRulesDep,
    settings: AppSettings,
    domain: str = Form(...),
    source_ids: list[uuid.UUID] = Form(...),
) -> RedirectResponse:
    summary = AnalysisRunner(session, gateway, extraction_rules, settings=settings).extract(
        actor=actor, project_id=ProjectId(project_id), source_ids=source_ids, domain=domain
    )
    return _see_other(f"/ui/runs/{summary.run_id}")


@router.post("/projects/{project_id}/classification-runs")
def start_classification(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    gateway: Gateway,
    extraction_rules: ExtractionRulesDep,
    settings: AppSettings,
    version_ids: list[uuid.UUID] = Form(...),
) -> RedirectResponse:
    summary = AnalysisRunner(session, gateway, extraction_rules, settings=settings).classify(
        actor=actor, project_id=ProjectId(project_id), version_ids=version_ids
    )
    return _see_other(f"/ui/runs/{summary.run_id}")


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_page(
    run_id: uuid.UUID, request: Request, session: DbSession, actor: CurrentActor
) -> HTMLResponse:
    runs = RunRepository(session, actor)
    pid, run = require_found(
        actor, Action.RUN_READ, ResourceType.GRAPH_RUN, lambda p: runs.get_run(p, run_id)
    )
    agent_runs = []
    for agent_run in runs.agent_runs(pid, run.id):
        model = (
            runs.get_model_version(pid, uuid.UUID(agent_run.model_version_id))
            if agent_run.model_version_id
            else None
        )
        template = (
            runs.get_prompt_template(pid, uuid.UUID(agent_run.prompt_template_id))
            if agent_run.prompt_template_id
            else None
        )
        agent_runs.append({"run": agent_run, "model": model, "template": template})
    return TEMPLATES.TemplateResponse(
        request,
        "run.html",
        {
            "actor": actor,
            "project_id": pid,
            "run": run,
            "agent_runs": agent_runs,
            "candidates": CandidateRepository(session, actor).list_for_run(pid, run.id),
            "signal_caveat": SIGNAL_CAVEAT,
        },
    )


# ---------------------------------------------------------------------------
# The review queue
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/review", response_class=HTMLResponse)
def review_page(
    project_id: uuid.UUID,
    request: Request,
    session: DbSession,
    actor: CurrentActor,
    show: str = "open",
) -> HTMLResponse:
    project = _project(session, actor, project_id, Action.REVIEW_READ)
    pid = ProjectId(project.id)
    status_filter = ReviewStatus.OPEN if show == "open" else None
    items = ReviewQueueService(session, actor).list(pid, status=status_filter)
    return TEMPLATES.TemplateResponse(
        request,
        "review.html",
        {
            "actor": actor,
            "project": project,
            "items": items,
            "show": show,
            "allowed": {
                reason: sorted(r.value for r in ALLOWED_RESOLUTIONS[reason])
                for reason in ReviewReason
            },
            "categories": list(RequirementCategory),
            "may_resolve": _may(actor, Action.REVIEW_RESOLVE, pid),
            "not_approval": NOT_APPROVAL,
            "signal_caveat": SIGNAL_CAVEAT,
        },
    )


@router.post("/review-items/{item_id}/resolve")
def resolve_item(
    item_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    resolution: ReviewResolution = Form(...),
    note: str = Form(""),
    categories: list[RequirementCategory] | None = Form(None),
    keep: str = Form("related"),
) -> RedirectResponse:
    service = ReviewQueueService(session, actor)
    pid, _item = require_found(
        actor, Action.REVIEW_READ, ResourceType.REVIEW_ITEM, lambda p: service.get(p, item_id)
    )
    service.resolve(
        project_id=pid,
        item_id=item_id,
        resolution=resolution,
        note=note,
        categories=categories or [],
        keep="subject" if keep == "subject" else "related",
    )
    return _see_other(f"/ui/projects/{pid}/review")


@router.post("/requirement-versions/{version_id}/classification")
def override_classification(
    version_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    requirement_id: uuid.UUID = Form(...),
    reason: str = Form(...),
    categories: list[RequirementCategory] | None = Form(None),
) -> RedirectResponse:
    versions = RequirementVersionRepository(session, actor)
    pid, _version = require_found(
        actor,
        Action.REQUIREMENT_READ,
        ResourceType.REQUIREMENT_VERSION,
        lambda p: versions.get(p, version_id),
    )
    ClassificationService(session, actor).override(
        project_id=pid, version_id=version_id, categories=categories or [], reason=reason
    )
    return _see_other(f"/ui/requirements/{requirement_id}")
