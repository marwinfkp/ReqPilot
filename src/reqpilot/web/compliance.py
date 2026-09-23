"""Demonstration UI for compliance and security analysis (P6; M1).

One page per project: the standing advisory notice (``FR-CMP-007``), candidate
mappings with every citation's provenance, the rule engine's gaps, the implied
checkpoints and obligations, and the derived security/privacy requirements with
the model's proposed level beside the authoritative one. G2/G3 decisions are
taken in the approval queue, through the same approval service as the API.
Server-side authorisation is the only authorisation.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from reqpilot.api.compliance_schemas import GATE_NOTICE
from reqpilot.api.dependencies import (
    AppSettings,
    ComplianceRulesDep,
    CurrentActor,
    DbSession,
    ExtractionRulesDep,
    Gateway,
    RetrieverFactoryDep,
    SecurityRulesDep,
)
from reqpilot.api.routes.compliance import finding_out, mapping_out
from reqpilot.domain.enums import Action, ResourceType
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.identity import Project
from reqpilot.domain.policy import Actor, ResourceRef, can, require
from reqpilot.graph.runner import AnalysisRunner
from reqpilot.services.compliance import ComplianceReadService
from reqpilot.services.compliance.report import IMPLIED_KINDS

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)


def _project(session: DbSession, actor: Actor, project_id: uuid.UUID, action: Action) -> Project:
    pid = ProjectId(project_id)
    require(actor, action, ResourceRef(resource_type=ResourceType.PROJECT, project_id=pid))
    project = session.get(Project, pid)
    if project is None:  # pragma: no cover - membership implies existence
        raise LookupError("project not found")
    return project


@router.get("/projects/{project_id}/compliance", response_class=HTMLResponse)
def compliance_page(
    project_id: uuid.UUID, request: Request, session: DbSession, actor: CurrentActor
) -> HTMLResponse:
    project = _project(session, actor, project_id, Action.COMPLIANCE_READ)
    pid = ProjectId(project.id)
    reads = ComplianceReadService(session, actor)
    overview = reads.overview(pid)
    may_read_security = can(
        actor, Action.SECURITY_READ, ResourceRef(resource_type=ResourceType.PROJECT, project_id=pid)
    ).allowed
    return TEMPLATES.TemplateResponse(
        request,
        "compliance.html",
        {
            "actor": actor,
            "project": project,
            "advisory_notice": overview.advisory_notice,
            "gate_notice": GATE_NOTICE,
            "mappings": [mapping_out(session, actor, pid, m) for m in overview.mappings],
            "gaps": overview.gaps,
            "gap_run_id": overview.gap_run_id,
            "implied": [m for m in overview.mappings if m.obligation_kind in IMPLIED_KINDS],
            "findings": [finding_out(session, actor, pid, f) for f in overview.findings]
            if may_read_security
            else [],
            "may_run": can(
                actor,
                Action.RUN_START,
                ResourceRef(resource_type=ResourceType.PROJECT, project_id=pid),
            ).allowed,
        },
    )


@router.post("/projects/{project_id}/compliance-runs")
def run_compliance(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    gateway: Gateway,
    extraction_rules: ExtractionRulesDep,
    compliance_rules: ComplianceRulesDep,
    security_rules: SecurityRulesDep,
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
        retriever=retrievers(session, actor),
    ).analyse_compliance(actor=actor, project_id=ProjectId(project_id))
    return RedirectResponse(
        f"/ui/projects/{project_id}/compliance", status_code=status.HTTP_303_SEE_OTHER
    )
