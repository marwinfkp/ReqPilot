"""Demonstration UI for risk analysis and the register (P7; M1).

One page per project: the published severity matrix, the register with every
entry's ratings, rationales, computed severity, mitigations and evidence, the
aggregate measures, and which entries currently block a baseline.

**The UI is not the enforcement point.** Every governance rule P7 adds - the
matrix, the G8 escalation, the baseline block, the scope guard, the policy -
holds with this page removed, and the tests exercise all of them without it.
What the page adds is that a reviewer can see *why* a severity is what it is.

G8 decisions are taken in the approval queue, through the same approval service
as the API. Server-side authorisation is the only authorisation.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from reqpilot.api.dependencies import (
    AppSettings,
    ComplianceRulesDep,
    CurrentActor,
    DbSession,
    ExtractionRulesDep,
    Gateway,
    RetrieverFactoryDep,
    RiskRulesDep,
    SecurityRulesDep,
)
from reqpilot.api.risk_schemas import GATE_NOTICE, SCOPE_NOTICE
from reqpilot.api.routes.risk import IMPACT_SCALE, LIKELIHOOD_SCALE, risk_out
from reqpilot.domain.enums import Action, ResourceType
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.identity import Project
from reqpilot.domain.policy import Actor, ResourceRef, can, require
from reqpilot.graph.runner import AnalysisRunner
from reqpilot.services.risk import RiskRegisterService
from reqpilot.services.risk.register import REGISTER_NOTICE

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)


def _project(session: DbSession, actor: Actor, project_id: uuid.UUID, action: Action) -> Project:
    pid = ProjectId(project_id)
    require(actor, action, ResourceRef(resource_type=ResourceType.PROJECT, project_id=pid))
    project = session.get(Project, pid)
    if project is None:  # pragma: no cover - membership implies existence
        raise LookupError("project not found")
    return project


@router.get("/projects/{project_id}/risks", response_class=HTMLResponse)
def risk_page(
    project_id: uuid.UUID,
    request: Request,
    session: DbSession,
    actor: CurrentActor,
    risk_rules: RiskRulesDep,
) -> HTMLResponse:
    project = _project(session, actor, project_id, Action.RISK_READ)
    pid = ProjectId(project.id)
    service = RiskRegisterService(session, actor, risk_rules)
    register = service.register(pid)
    matrix = risk_rules.matrix
    return TEMPLATES.TemplateResponse(
        request,
        "risk.html",
        {
            "actor": actor,
            "project": project,
            "notice": REGISTER_NOTICE,
            "scope_notice": SCOPE_NOTICE,
            "gate_notice": GATE_NOTICE,
            "risks": [risk_out(r) for r in register.risks],
            "by_severity": register.by_severity,
            "by_category": register.by_category,
            "by_status": register.by_status,
            "blocking": len(register.blocking),
            "matrix_version": register.matrix_version,
            "rules_version": register.rules_version,
            "matrix_rows": [
                (
                    likelihood.value,
                    [
                        matrix.severity(likelihood, impact).value
                        for impact in sorted({i for _l, i in matrix.cells}, key=lambda i: i.value)
                    ],
                )
                for likelihood in sorted(
                    {level for level, _i in matrix.cells},
                    key=lambda level: level.value,
                    reverse=True,
                )
            ],
            "impact_headers": sorted({i.value for _level, i in matrix.cells}),
            "likelihood_scale": LIKELIHOOD_SCALE,
            "impact_scale": IMPACT_SCALE,
            "factors": service.factor_inputs(pid),
            "may_run": can(
                actor,
                Action.RUN_START,
                ResourceRef(resource_type=ResourceType.PROJECT, project_id=pid),
            ).allowed,
        },
    )


@router.post("/projects/{project_id}/risk-runs")
def run_risk(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    gateway: Gateway,
    extraction_rules: ExtractionRulesDep,
    compliance_rules: ComplianceRulesDep,
    security_rules: SecurityRulesDep,
    risk_rules: RiskRulesDep,
    retrievers: RetrieverFactoryDep,
    settings: AppSettings,
) -> RedirectResponse:
    AnalysisRunner(
        session,
        gateway,
        extraction_rules,
        settings=settings,
        compliance_rules=compliance_rules,
        security_rules=security_rules,
        risk_rules=risk_rules,
        retriever=retrievers(session, actor),
    ).analyse_risk(actor=actor, project_id=ProjectId(project_id))
    return RedirectResponse(
        f"/ui/projects/{project_id}/risks", status_code=status.HTTP_303_SEE_OTHER
    )
