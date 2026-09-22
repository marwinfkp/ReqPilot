"""Demonstration UI for quality and conflict detection (P5; M1).

One page per project: run the quality analysis, see findings (with their
evidence, detector and review priority) and conflicts (both sides, both
stakeholders), and close them as the policy allows. Every decision is a form
post handled by the same services as the API; server-side authorisation is the
only authorisation.
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
    ElicitationRulesDep,
    Embedder,
    ExtractionRulesDep,
    Gateway,
    QualityRulesDep,
)
from reqpilot.api.lookup import require_found
from reqpilot.api.quality_schemas import NOT_APPROVAL
from reqpilot.api.routes.quality import _Versions, conflict_out, finding_out
from reqpilot.domain.enums import (
    Action,
    ConflictResolution,
    QualityFindingStatus,
    ResourceType,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.identity import Project
from reqpilot.domain.models.quality import Conflict
from reqpilot.domain.policy import Actor, ResourceRef, can, require
from reqpilot.graph.clarification_runner import ClarificationRunner
from reqpilot.graph.runner import AnalysisRunner
from reqpilot.services.elicitation import StakeholderService
from reqpilot.services.quality import ConflictService, FindingReviewService, GlossaryService

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)


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


@router.get("/projects/{project_id}/quality", response_class=HTMLResponse)
def quality_page(
    project_id: uuid.UUID,
    request: Request,
    session: DbSession,
    actor: CurrentActor,
    rules: QualityRulesDep,
    elicitation_rules: ElicitationRulesDep,
) -> HTMLResponse:
    project = _project(session, actor, project_id, Action.QUALITY_FINDING_READ)
    pid = ProjectId(project.id)
    versions = _Versions(session, actor, pid)
    findings = sorted(
        FindingReviewService(session, actor).list_for_project(pid),
        key=lambda f: (f.status is not QualityFindingStatus.OPEN, f.created_at),
    )
    conflicts = sorted(
        ConflictService(session, actor).list_for_project(pid),
        key=lambda c: (c.status.value in ("resolved", "dismissed"), c.created_at),
    )
    return TEMPLATES.TemplateResponse(
        request,
        "quality.html",
        {
            "actor": actor,
            "project": project,
            "findings": [finding_out(f, versions, rules) for f in findings],
            "conflicts": [conflict_out(c, versions, rules) for c in conflicts],
            "glossary": GlossaryService(session, actor).list_terms(pid),
            "stakeholders": StakeholderService(session, actor, elicitation_rules).list_stakeholders(
                pid
            ),
            "resolutions": list(ConflictResolution),
            "may_run": _may(actor, Action.RUN_START, pid),
            "may_close": _may(actor, Action.QUALITY_FINDING_RESOLVE, pid),
            "may_resolve": _may(actor, Action.CONFLICT_RESOLVE, pid),
            "may_glossary": _may(actor, Action.GLOSSARY_MANAGE, pid),
            "not_approval": NOT_APPROVAL,
        },
    )


@router.post("/projects/{project_id}/quality-runs")
def run_quality(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    gateway: Gateway,
    extraction_rules: ExtractionRulesDep,
    quality_rules: QualityRulesDep,
    embedder: Embedder,
    settings: AppSettings,
) -> RedirectResponse:
    AnalysisRunner(
        session,
        gateway,
        extraction_rules,
        settings=settings,
        quality_rules=quality_rules,
        embedder=embedder,
    ).analyse_quality(actor=actor, project_id=ProjectId(project_id))
    return _see_other(f"/ui/projects/{project_id}/quality")


def _finding_project(session: DbSession, actor: Actor, finding_id: uuid.UUID) -> ProjectId:
    service = FindingReviewService(session, actor)
    pid, _finding = require_found(
        actor,
        Action.QUALITY_FINDING_READ,
        ResourceType.QUALITY_FINDING,
        lambda p: service.get(p, finding_id),
    )
    return pid


@router.post("/quality-findings/{finding_id}/resolve")
def resolve_finding(
    finding_id: uuid.UUID, session: DbSession, actor: CurrentActor, reason: str = Form(...)
) -> RedirectResponse:
    pid = _finding_project(session, actor, finding_id)
    FindingReviewService(session, actor).resolve(pid, finding_id, reason)
    return _see_other(f"/ui/projects/{pid}/quality")


@router.post("/quality-findings/{finding_id}/dismiss")
def dismiss_finding(
    finding_id: uuid.UUID, session: DbSession, actor: CurrentActor, reason: str = Form(...)
) -> RedirectResponse:
    pid = _finding_project(session, actor, finding_id)
    FindingReviewService(session, actor).dismiss(pid, finding_id, reason)
    return _see_other(f"/ui/projects/{pid}/quality")


def _conflict(
    session: DbSession, actor: Actor, conflict_id: uuid.UUID
) -> tuple[ProjectId, Conflict]:
    service = ConflictService(session, actor)
    return require_found(
        actor, Action.CONFLICT_READ, ResourceType.CONFLICT, lambda p: service.get(p, conflict_id)
    )


@router.post("/conflicts/{conflict_id}/review")
def review_conflict(
    conflict_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> RedirectResponse:
    pid, conflict = _conflict(session, actor, conflict_id)
    ConflictService(session, actor).review(pid, conflict.id)
    return _see_other(f"/ui/projects/{pid}/quality")


@router.post("/conflicts/{conflict_id}/resolve")
def resolve_conflict(
    conflict_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    resolution: ConflictResolution = Form(...),
    reason: str = Form(...),
    withdraw_other: bool = Form(False),
) -> RedirectResponse:
    pid, conflict = _conflict(session, actor, conflict_id)
    ConflictService(session, actor).resolve(
        pid, conflict.id, resolution=resolution, reason=reason, withdraw_other=withdraw_other
    )
    return _see_other(f"/ui/projects/{pid}/quality")


@router.post("/conflicts/{conflict_id}/dismiss")
def dismiss_conflict(
    conflict_id: uuid.UUID, session: DbSession, actor: CurrentActor, reason: str = Form(...)
) -> RedirectResponse:
    pid, conflict = _conflict(session, actor, conflict_id)
    ConflictService(session, actor).dismiss(pid, conflict.id, reason)
    return _see_other(f"/ui/projects/{pid}/quality")


@router.post("/conflicts/{conflict_id}/clarify")
def clarify_conflict(
    conflict_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    elicitation_rules: ElicitationRulesDep,
    extraction_rules: ExtractionRulesDep,
    gateway: Gateway,
    settings: AppSettings,
    side: str = Form(...),
    asked_of: uuid.UUID = Form(...),
) -> RedirectResponse:
    pid, conflict = _conflict(session, actor, conflict_id)
    finding = ConflictService(session, actor).clarification_finding(pid, conflict.id, side=side)
    ClarificationRunner(
        session, gateway, elicitation_rules, extraction_rules, settings=settings
    ).raise_for_finding(actor=actor, project_id=pid, finding_id=finding.id, asked_of=asked_of)
    return _see_other(f"/ui/projects/{pid}/clarifications")


@router.post("/projects/{project_id}/glossary")
def add_term(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    term: str = Form(...),
    definition: str = Form(...),
) -> RedirectResponse:
    GlossaryService(session, actor).add(ProjectId(project_id), term=term, definition=definition)
    return _see_other(f"/ui/projects/{project_id}/quality")
