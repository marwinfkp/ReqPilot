"""Demonstration UI for the SDLC recommendation (P9; M1).

Two pages: a project's recommendation runs (and a form to start one from an
approved baseline), and one run - the 13 factors with their evidence and
provenance, a form to override a factor with a reason, the computed ranking with
every rule effect, the reversal conditions, the explanation with any
discrepancy shown beside it, and the G6 status with decision forms that post to
the one approval path.

**The UI is not the enforcement point.** Every rule P9 adds - approved inputs
only, bounded proposals, recorded overrides, the persisted ranking, the
consistency check, G6 co-approval, self-approval refusal - holds with these
pages removed. Server-side authorisation is the only authorisation.
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
from reqpilot.api.routes.sdlc import sdlc_run_out
from reqpilot.api.sdlc_schemas import SDLC_NOTICE
from reqpilot.domain.enums import Action, ApprovalTaskStatus, ResourceType, Role
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.identity import Project
from reqpilot.domain.policy import Actor, ResourceRef, can, require
from reqpilot.graph.sdlc_runner import SdlcRunner
from reqpilot.services.baseline import BaselineService
from reqpilot.services.sdlc.service import SdlcService

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)


def _may(actor: Actor, pid: ProjectId, action: Action) -> bool:
    return can(
        actor, action, ResourceRef(resource_type=ResourceType.SDLC_RUN, project_id=pid)
    ).allowed


@router.get("/projects/{project_id}/sdlc", response_class=HTMLResponse)
def sdlc_page(
    project_id: uuid.UUID,
    request: Request,
    session: DbSession,
    actor: CurrentActor,
    sdlc_rules: SdlcRulesDep,
) -> HTMLResponse:
    pid = ProjectId(project_id)
    require(
        actor, Action.SDLC_READ, ResourceRef(resource_type=ResourceType.SDLC_RUN, project_id=pid)
    )
    project = session.get(Project, pid)
    may_start = _may(actor, pid, Action.SDLC_RUN_START)
    baselines = BaselineService(session, actor).list_for_project(pid) if may_start else []
    return TEMPLATES.TemplateResponse(
        request,
        "sdlc.html",
        {
            "actor": actor,
            "project": project,
            "notice": SDLC_NOTICE,
            "runs": SdlcService(session, actor, sdlc_rules).list_runs(pid),
            "baselines": baselines,
            "may_start": may_start,
            "ruleset": sdlc_rules.ruleset_ref,
            "candidates": sdlc_rules.config.candidates,
        },
    )


@router.post("/projects/{project_id}/sdlc-runs")
def start_run(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    gateway: Gateway,
    sdlc_rules: SdlcRulesDep,
    risk_rules: RiskRulesDep,
    settings: AppSettings,
    baseline_id: str = Form(...),
) -> RedirectResponse:
    summary = SdlcRunner(
        session, gateway, settings=settings, sdlc_rules=sdlc_rules, risk_rules=risk_rules
    ).start(actor=actor, project_id=ProjectId(project_id), baseline_id=uuid.UUID(baseline_id))
    target = (
        f"/ui/sdlc-runs/{summary.sdlc_run_id}"
        if summary.sdlc_run_id
        else f"/ui/projects/{project_id}/sdlc"
    )
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/sdlc-runs/{run_id}", response_class=HTMLResponse)
def run_page(
    run_id: uuid.UUID,
    request: Request,
    session: DbSession,
    actor: CurrentActor,
    sdlc_rules: SdlcRulesDep,
) -> HTMLResponse:
    service = SdlcService(session, actor, sdlc_rules)
    pid, run = require_found(
        actor, Action.SDLC_READ, ResourceType.SDLC_RUN, lambda p: service.get(p, run_id)
    )
    view = sdlc_run_out(session, actor, sdlc_rules, pid, run)
    roles = actor.roles_in(pid)
    return TEMPLATES.TemplateResponse(
        request,
        "sdlc_run.html",
        {
            "actor": actor,
            "project": session.get(Project, pid),
            "run": view,
            "override_roles": [r for r in (Role.ANALYST, Role.PROJECT_MANAGER) if r in roles],
            "may_override": _may(actor, pid, Action.SDLC_FACTOR_OVERRIDE),
            "may_explain": _may(actor, pid, Action.SDLC_EXPLAIN),
            "signable": {
                t.id
                for t in view.g6_tasks
                if t.status is ApprovalTaskStatus.OPEN and t.required_role in roles
            },
        },
    )


@router.post("/sdlc-runs/{run_id}/factors/{factor_id}/override")
def override(
    run_id: uuid.UUID,
    factor_id: str,
    session: DbSession,
    actor: CurrentActor,
    gateway: Gateway,
    sdlc_rules: SdlcRulesDep,
    risk_rules: RiskRulesDep,
    settings: AppSettings,
    score: int = Form(...),
    reason: str = Form(...),
    role: str = Form(...),
) -> RedirectResponse:
    service = SdlcService(session, actor, sdlc_rules)
    pid, run = require_found(
        actor, Action.SDLC_READ, ResourceType.SDLC_RUN, lambda p: service.get(p, run_id)
    )
    summary = SdlcRunner(
        session, gateway, settings=settings, sdlc_rules=sdlc_rules, risk_rules=risk_rules
    ).override(
        actor=actor,
        project_id=pid,
        run_id=run.id,
        factor=factor_id,
        new_score=score,
        reason=reason,
        role=Role(role),
    )
    return RedirectResponse(
        f"/ui/sdlc-runs/{summary.sdlc_run_id or run.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/sdlc-runs/{run_id}/explanation")
def explain(
    run_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    gateway: Gateway,
    sdlc_rules: SdlcRulesDep,
    risk_rules: RiskRulesDep,
    settings: AppSettings,
) -> RedirectResponse:
    service = SdlcService(session, actor, sdlc_rules)
    pid, run = require_found(
        actor, Action.SDLC_READ, ResourceType.SDLC_RUN, lambda p: service.get(p, run_id)
    )
    SdlcRunner(
        session, gateway, settings=settings, sdlc_rules=sdlc_rules, risk_rules=risk_rules
    ).explain(actor=actor, project_id=pid, run_id=run.id)
    return RedirectResponse(f"/ui/sdlc-runs/{run.id}", status_code=status.HTTP_303_SEE_OTHER)
