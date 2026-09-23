"""Risk analysis and risk-register endpoints (P7; architecture S, I.3-I.6, M.3).

What P7 needs to be usable, and no more:

* ``POST /projects/{id}/risk-runs`` - start a risk run (Analyst; ``RUN_START``).
* ``GET  /projects/{id}/risks`` and ``/risks/{id}`` - the register entries, with
  ratings, the computed severity, both rationales, mitigations labelled by
  provenance, and evidence.
* ``GET  /projects/{id}/risk-register`` - the whole register plus the aggregate
  measures that feed the SDLC factor profile (``FR-RSK-008``, ``FR-RSK-009``).
* ``GET  /projects/{id}/risk-register.md`` - the register artefact as Markdown.
* ``GET  /risk-matrix`` - the published matrix, so any rating is explainable.
* ``POST /projects/{id}/risks`` - a human adding a risk (``FR-RSK-010``).
* ``POST /risks/{id}/decision`` - accept, mitigate, reject or close, with the
  rationale (``FR-RSK-010``).
* ``POST /risks/{id}/mitigations`` and ``POST /risk-mitigations/{id}/decision`` -
  a human's own mitigation, and accepting or rejecting an AI-suggested one
  (``FR-RSK-005``).

**G8 is decided through the existing** ``POST /approval-tasks/{id}/decide``;
there is no other decision path, and no endpoint here closes a gate or
baselines anything. No request body carries a severity: the matrix computes it.

Every handler authorises through the policy; a resource outside the caller's
reach is a 404, exactly as for one that does not exist.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

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
from reqpilot.api.lookup import require_found
from reqpilot.api.risk_schemas import (
    FactorInputOut,
    MatrixCellOut,
    MatrixOut,
    MitigationDecisionIn,
    MitigationIn,
    MitigationOut,
    RegisterOut,
    RiskDecisionIn,
    RiskIn,
    RiskOut,
    RiskRunIn,
    RiskRunOut,
    RisksOut,
)
from reqpilot.domain.enums import Action, ResourceType, RiskImpact, RiskLikelihood
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.risk import Risk, RiskMitigation
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.domain.risk.matrix import ESCALATING_SEVERITY
from reqpilot.graph.runner import AnalysisRunner
from reqpilot.rules.risk import RiskRules
from reqpilot.services.risk import RiskRegisterService, RiskService
from reqpilot.services.risk.register import RiskView

router = APIRouter(prefix="/api/v1", tags=["risk"])

#: The scales, from architecture I.2, rendered so a rating can be read.
LIKELIHOOD_SCALE = {
    "L1": "Unlikely - would require an unusual combination of circumstances",
    "L2": "Possible - plausible within this project's normal course",
    "L3": "Likely - expected unless specifically prevented",
}
IMPACT_SCALE = {
    "I1": "Minor - local rework; no compliance, security or schedule consequence",
    "I2": "Moderate - significant rework, schedule slip, or a control weakness to remediate",
    "I3": "Major - regulatory exposure, security compromise, or project-level failure",
}


def _project(actor: Actor, project_id: uuid.UUID, action: Action) -> ProjectId:
    pid = ProjectId(project_id)
    require(actor, action, ResourceRef(resource_type=ResourceType.PROJECT, project_id=pid))
    return pid


def risk_out(view: RiskView) -> RiskOut:
    return RiskOut(
        id=view.id,
        scope=view.scope,
        requirement_version_id=view.requirement_version_id,
        requirement_label=(None if view.requirement_human_id is None else view.subject),
        category=view.category,
        title=view.title,
        description=view.description,
        likelihood=RiskLikelihood(view.likelihood),
        impact=RiskImpact(view.impact),
        severity=view.severity,
        matrix_version=view.matrix_version,
        likelihood_rationale=view.likelihood_rationale,
        impact_rationale=view.impact_rationale,
        mitigations=[
            MitigationOut(
                id=m.id,
                suggestion=m.suggestion,
                is_ai_generated=m.is_ai_generated,
                status=m.status,
                label=m.label,
            )
            for m in view.mitigations
        ],
        citations=[dict(c) for c in view.citations],
        evidence_count=view.evidence_count,
        owner_role=view.owner_role,
        status=view.status,
        detected_by=view.detected_by,  # type: ignore[arg-type]
        approval_task_id=view.approval_task_id,
        decision_rationale=view.decision_rationale,
        blocking_baseline=view.blocking,
        created_at=_dt(view.created_at),
        updated_at=_dt(view.updated_at),
    )


def _dt(value: str):  # type: ignore[no-untyped-def]
    import datetime as dt

    return dt.datetime.fromisoformat(value)


def _view(session: Session, actor: Actor, project_id: ProjectId, risk_id: uuid.UUID) -> RiskView:
    register = RiskRegisterService(session, actor).register(project_id)
    found = next((r for r in register.risks if r.id == risk_id), None)
    if found is None:  # pragma: no cover - the caller resolved it a moment ago
        from fastapi import HTTPException
        from fastapi import status as http_status

        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="risk not found")
    return found


# --- runs ----------------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/risk-runs",
    response_model=RiskRunOut,
    status_code=status.HTTP_201_CREATED,
)
def start_risk_run(
    project_id: uuid.UUID,
    payload: RiskRunIn,
    session: DbSession,
    actor: CurrentActor,
    gateway: Gateway,
    extraction_rules: ExtractionRulesDep,
    compliance_rules: ComplianceRulesDep,
    security_rules: SecurityRulesDep,
    risk_rules: RiskRulesDep,
    retrievers: RetrieverFactoryDep,
    settings: AppSettings,
) -> RiskRunOut:
    """Risk analysis (C.3 nodes 18-19 and the G8 part of 20). Analyst only (RUN_START)."""
    pid = _project(actor, project_id, Action.RUN_START)
    summary = AnalysisRunner(
        session,
        gateway,
        extraction_rules,
        settings=settings,
        compliance_rules=compliance_rules,
        security_rules=security_rules,
        risk_rules=risk_rules,
        retriever=retrievers(session, actor),
    ).analyse_risk(
        actor=actor, project_id=pid, version_ids=payload.version_ids, semantic=payload.semantic
    )
    return RiskRunOut(
        run_id=summary.run_id,
        status=summary.status,
        risk_ids=list(summary.risk_ids),
        risks_out_of_scope=summary.risks_out_of_scope,
        claims_dropped=summary.claims_dropped,
        gate_task_ids=list(summary.gate_task_ids),
        semantic_failures=summary.semantic_failures,
        provider_calls=summary.provider_calls,
        tokens_in=summary.tokens_in,
        tokens_out=summary.tokens_out,
        errors=list(summary.errors),
    )


# --- the register --------------------------------------------------------------------------


@router.get("/risk-matrix", response_model=MatrixOut)
def get_matrix(risk_rules: RiskRulesDep) -> MatrixOut:
    """The published severity matrix (``FR-RSK-004``). No project scope: it is the same
    for every project, and publishing it is what makes a rating explainable."""
    return _matrix_out(risk_rules)


def _matrix_out(rules: RiskRules) -> MatrixOut:
    return MatrixOut(
        matrix_version=rules.matrix.version,
        cells=[
            MatrixCellOut(likelihood=likelihood, impact=impact, severity=severity)
            for (likelihood, impact), severity in sorted(
                rules.matrix.cells.items(), key=lambda i: (i[0][0].value, i[0][1].value)
            )
        ],
        likelihood_scale=LIKELIHOOD_SCALE,
        impact_scale=IMPACT_SCALE,
        escalating_severity=ESCALATING_SEVERITY,
    )


@router.get("/projects/{project_id}/risks", response_model=RisksOut)
def list_risks(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    version_id: uuid.UUID | None = None,
) -> RisksOut:
    pid = _project(actor, project_id, Action.RISK_READ)
    register = RiskRegisterService(session, actor).register(pid)
    risks = [
        r for r in register.risks if version_id is None or r.requirement_version_id == version_id
    ]
    return RisksOut(risks=[risk_out(r) for r in risks])


@router.get("/risks/{risk_id}", response_model=RiskOut)
def get_risk(risk_id: uuid.UUID, session: DbSession, actor: CurrentActor) -> RiskOut:
    from reqpilot.repositories.risk import RiskRepository

    pid, _risk = require_found(
        actor,
        Action.RISK_READ,
        ResourceType.RISK,
        lambda project_id: RiskRepository(session, actor).get(project_id, risk_id),
    )
    return risk_out(_view(session, actor, pid, risk_id))


@router.get("/projects/{project_id}/risk-register", response_model=RegisterOut)
def get_register(
    project_id: uuid.UUID, session: DbSession, actor: CurrentActor, risk_rules: RiskRulesDep
) -> RegisterOut:
    """The Risk Register (``FR-RSK-008``) with its aggregate measures (``FR-RSK-009``).

    A query over the persisted rows (``[DESIGN] D7``), not a stored document.
    """
    pid = _project(actor, project_id, Action.RISK_READ)
    service = RiskRegisterService(session, actor, risk_rules)
    register = service.register(pid)
    return RegisterOut(
        risks=[risk_out(r) for r in register.risks],
        by_severity=register.by_severity,
        by_category=register.by_category,
        by_status=register.by_status,
        blocking_baseline=len(register.blocking),
        matrix_version=register.matrix_version,
        rules_version=register.rules_version,
        factor_inputs=[
            FactorInputOut(
                key=f.key,
                value=f.value,
                description=f.description,
                counts=f.counts,
                evidence_risk_ids=list(f.evidence_risk_ids),
            )
            for f in service.factor_inputs(pid)
        ],
    )


@router.get("/projects/{project_id}/risk-register.md", response_class=PlainTextResponse)
def get_register_markdown(
    project_id: uuid.UUID, session: DbSession, actor: CurrentActor, risk_rules: RiskRulesDep
) -> str:
    """The register artefact, rendered deterministically. No model is involved."""
    pid = _project(actor, project_id, Action.RISK_READ)
    return RiskRegisterService(session, actor, risk_rules).render_markdown(pid)


# --- human risk management (FR-RSK-010, FR-RSK-005) ----------------------------------------


@router.post(
    "/projects/{project_id}/risks", response_model=RiskOut, status_code=status.HTTP_201_CREATED
)
def add_risk(
    project_id: uuid.UUID,
    payload: RiskIn,
    session: DbSession,
    actor: CurrentActor,
    risk_rules: RiskRulesDep,
) -> RiskOut:
    """A human adding a risk. Rated by the same matrix; refused by the same scope guard."""
    pid = _project(actor, project_id, Action.RISK_MANAGE)
    risk = RiskService(session, actor, risk_rules).add_risk(
        project_id=pid,
        category=payload.category,
        title=payload.title,
        description=payload.description,
        likelihood=payload.likelihood,
        impact=payload.impact,
        likelihood_rationale=payload.likelihood_rationale,
        impact_rationale=payload.impact_rationale,
        evidence_ids=payload.evidence_ids,
        requirement_version_id=payload.requirement_version_id,
        mitigation=payload.mitigation,
    )
    return risk_out(_view(session, actor, pid, risk.id))


@router.post("/risks/{risk_id}/decision", response_model=RiskOut)
def decide_risk(
    risk_id: uuid.UUID,
    payload: RiskDecisionIn,
    session: DbSession,
    actor: CurrentActor,
    risk_rules: RiskRulesDep,
) -> RiskOut:
    """Accept, mitigate, reject or close a risk, with a recorded rationale.

    This is the register entry's own decision. It does **not** decide G8: a
    blocking G8 task is cleared only at the gate, and the baseline guard still
    counts it until then.
    """
    from reqpilot.repositories.risk import RiskRepository

    pid, _risk = require_found(
        actor,
        Action.RISK_READ,
        ResourceType.RISK,
        lambda project_id: RiskRepository(session, actor).get(project_id, risk_id),
    )
    RiskService(session, actor, risk_rules).decide(
        project_id=pid, risk_id=risk_id, status=payload.status, rationale=payload.rationale
    )
    return risk_out(_view(session, actor, pid, risk_id))


@router.post(
    "/risks/{risk_id}/mitigations",
    response_model=MitigationOut,
    status_code=status.HTTP_201_CREATED,
)
def add_mitigation(
    risk_id: uuid.UUID,
    payload: MitigationIn,
    session: DbSession,
    actor: CurrentActor,
    risk_rules: RiskRulesDep,
) -> MitigationOut:
    from reqpilot.repositories.risk import RiskRepository

    pid, _risk = require_found(
        actor,
        Action.RISK_READ,
        ResourceType.RISK,
        lambda project_id: RiskRepository(session, actor).get(project_id, risk_id),
    )
    mitigation = RiskService(session, actor, risk_rules).add_mitigation(
        project_id=pid, risk_id=risk_id, suggestion=payload.suggestion
    )
    return _mitigation_out(mitigation)


@router.post("/risk-mitigations/{mitigation_id}/decision", response_model=MitigationOut)
def decide_mitigation(
    mitigation_id: uuid.UUID,
    payload: MitigationDecisionIn,
    session: DbSession,
    actor: CurrentActor,
    risk_rules: RiskRulesDep,
) -> MitigationOut:
    """Accept or reject an AI-suggested mitigation (``FR-RSK-005``)."""
    from reqpilot.repositories.risk import RiskMitigationRepository

    pid, _mitigation = require_found(
        actor,
        Action.RISK_READ,
        ResourceType.RISK_MITIGATION,
        lambda project_id: RiskMitigationRepository(session, actor).get(project_id, mitigation_id),
    )
    mitigation = RiskService(session, actor, risk_rules).decide_mitigation(
        project_id=pid,
        mitigation_id=mitigation_id,
        status=payload.status,
        rationale=payload.rationale,
    )
    return _mitigation_out(mitigation)


def _mitigation_out(mitigation: RiskMitigation) -> MitigationOut:
    from reqpilot.services.risk.register import MitigationView

    view = MitigationView(
        id=mitigation.id,
        suggestion=mitigation.suggestion,
        is_ai_generated=bool(mitigation.is_ai_generated),
        status=mitigation.status,
    )
    return MitigationOut(
        id=view.id,
        suggestion=view.suggestion,
        is_ai_generated=view.is_ai_generated,
        status=view.status,
        label=view.label,
    )


__all__ = ["Risk", "router"]
