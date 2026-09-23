"""Compliance and security analysis endpoints (P6; architecture S, K, I.7, M.3).

What P6 needs to be usable, and no more:

* ``POST /projects/{id}/compliance-runs`` - start a compliance and security run
  (Analyst; ``RUN_START``). Retrieval goes through the P2 allowlisted service.
* ``GET  /projects/{id}/compliance-mappings`` and ``/compliance-mappings/{id}`` -
  candidate mappings with every citation's provenance; the detail re-resolves
  each citation against its evidence row.
* ``GET  /projects/{id}/compliance-gaps`` - the rule engine's gaps (latest run).
* ``GET  /projects/{id}/security-privacy-findings`` and
  ``/security-privacy-findings/{id}`` - derived requirements with the model's
  proposed level beside the authoritative one and its reason.
* ``GET  /projects/{id}/compliance-report`` - the generated compliance artefact
  (Markdown), advisory notice first and last, language-checked.

G2 and G3 are decided through the existing ``POST /approval-tasks/{id}/decide``;
there is no other decision path. Every handler authorises through the policy; a
resource outside the caller's reach is a 404, exactly as for one that does not
exist. Every response carries the advisory notice (``FR-CMP-007``).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from reqpilot.api.compliance_schemas import (
    ComplianceGapOut,
    ComplianceGapsOut,
    ComplianceMappingDetailOut,
    ComplianceMappingOut,
    ComplianceMappingsOut,
    ComplianceRunIn,
    ComplianceRunOut,
    SecurityFindingDetailOut,
    SecurityFindingOut,
    SecurityFindingsOut,
    citation_out,
)
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
from reqpilot.api.lookup import require_found
from reqpilot.domain.enums import Action, ResourceType
from reqpilot.domain.errors import CitationError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.compliance import ComplianceMapping, SecurityPrivacyFinding
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.graph.runner import AnalysisRunner
from reqpilot.services.compliance import ComplianceReadService
from reqpilot.services.compliance.report import binding_of
from reqpilot.services.knowledge.evidence import EvidenceService

router = APIRouter(prefix="/api/v1", tags=["compliance"])


def _project(actor: Actor, project_id: uuid.UUID, action: Action) -> ProjectId:
    pid = ProjectId(project_id)
    require(actor, action, ResourceRef(resource_type=ResourceType.PROJECT, project_id=pid))
    return pid


def mapping_out(
    session: Session,
    actor: Actor,
    project_id: ProjectId,
    mapping: ComplianceMapping,
    *,
    resolve: bool = False,
) -> ComplianceMappingOut:
    reads = ComplianceReadService(session, actor)
    citations = []
    evidence = EvidenceService(session, actor)
    for snapshot in mapping.citations or []:
        live: dict[str, object] = {}
        if resolve:
            try:
                cited = evidence.describe(project_id, uuid.UUID(str(snapshot["evidence_id"])))
                live = {"quote": cited.quote, "resolves": True}
            except (CitationError, KeyError, ValueError):
                live = {"resolves": False}
        citations.append(citation_out(snapshot, **live))
    return ComplianceMappingOut(
        id=mapping.id,
        requirement_version_id=mapping.requirement_version_id,
        requirement_label=reads.label(project_id, mapping.requirement_version_id),
        control_key=mapping.control_key,
        control_title=mapping.control_title,
        obligation_kind=mapping.obligation_kind,
        checklist_ref=mapping.checklist_ref,
        checklist_domain=mapping.checklist_domain,
        checklist_jurisdiction=mapping.checklist_jurisdiction,
        relationship=mapping.relationship,
        rationale=mapping.rationale,
        candidate_text=mapping.candidate_text,
        implied_obligation=mapping.implied_obligation,
        jurisdiction=mapping.jurisdiction,
        source_type=mapping.source_type,
        source_type_binding=binding_of(mapping.source_type),
        citations=citations,
        is_high_impact=mapping.is_high_impact,
        high_impact_reasons=list(mapping.high_impact_reasons or []),
        review_signal=mapping.review_signal,
        status=mapping.status,
        approval_task_id=mapping.approval_task_id,
        graph_run_id=mapping.graph_run_id,
        agent_run_id=mapping.agent_run_id,
        language_rules_version=mapping.language_rules_version,
        created_at=mapping.created_at,
    )


def finding_out(
    session: Session, actor: Actor, project_id: ProjectId, finding: SecurityPrivacyFinding
) -> SecurityFindingOut:
    reads = ComplianceReadService(session, actor)
    return SecurityFindingOut(
        id=finding.id,
        requirement_version_id=finding.requirement_version_id,
        requirement_label=reads.label(project_id, finding.requirement_version_id),
        category=finding.category,
        family=finding.family,
        derived_requirement=finding.derived_requirement,
        rationale=finding.rationale,
        risk_rationale=finding.risk_rationale,
        evidence_status=finding.evidence_status,
        citations=[citation_out(c) for c in finding.citations or []],
        proposed_risk_level=finding.proposed_risk_level,
        normalised_proposed_level=finding.normalised_proposed_level,
        catalogue_floor=finding.catalogue_floor,
        risk_level=finding.risk_level,
        risk_rules_version=finding.risk_rules_version,
        escalation_reason=finding.escalation_reason,
        detected_by=finding.detected_by,
        source_signal_finding_id=finding.source_signal_finding_id,
        review_signal=finding.review_signal,
        status=finding.status,
        approval_task_id=finding.approval_task_id,
        graph_run_id=finding.graph_run_id,
        created_at=finding.created_at,
    )


# --- runs ----------------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/compliance-runs",
    response_model=ComplianceRunOut,
    status_code=status.HTTP_201_CREATED,
)
def start_compliance_run(
    project_id: uuid.UUID,
    payload: ComplianceRunIn,
    session: DbSession,
    actor: CurrentActor,
    gateway: Gateway,
    extraction_rules: ExtractionRulesDep,
    compliance_rules: ComplianceRulesDep,
    security_rules: SecurityRulesDep,
    retrievers: RetrieverFactoryDep,
    settings: AppSettings,
) -> ComplianceRunOut:
    """Compliance and security analysis (C.3 nodes 12-17, 20). Analyst only (RUN_START)."""
    pid = _project(actor, project_id, Action.RUN_START)
    summary = AnalysisRunner(
        session,
        gateway,
        extraction_rules,
        settings=settings,
        compliance_rules=compliance_rules,
        security_rules=security_rules,
        retriever=retrievers(session, actor),
    ).analyse_compliance(
        actor=actor, project_id=pid, version_ids=payload.version_ids, semantic=payload.semantic
    )
    return ComplianceRunOut(
        run_id=summary.run_id,
        status=summary.status,
        evidence_ids=list(summary.evidence_ids),
        evidence_unavailable_version_ids=list(summary.evidence_unavailable_ids),
        compliance_mapping_ids=list(summary.compliance_mapping_ids),
        compliance_gap_ids=list(summary.compliance_gap_ids),
        security_finding_ids=list(summary.security_finding_ids),
        claims_dropped=summary.claims_dropped,
        gate_task_ids=list(summary.gate_task_ids),
        semantic_failures=summary.semantic_failures,
        provider_calls=summary.provider_calls,
        tokens_in=summary.tokens_in,
        tokens_out=summary.tokens_out,
        errors=list(summary.errors),
    )


# --- mappings ------------------------------------------------------------------------------


@router.get("/projects/{project_id}/compliance-mappings", response_model=ComplianceMappingsOut)
def list_mappings(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    version_id: uuid.UUID | None = None,
) -> ComplianceMappingsOut:
    pid = _project(actor, project_id, Action.COMPLIANCE_READ)
    reads = ComplianceReadService(session, actor)
    return ComplianceMappingsOut(
        mappings=[
            mapping_out(session, actor, pid, m) for m in reads.mappings(pid, version_id=version_id)
        ]
    )


@router.get("/compliance-mappings/{mapping_id}", response_model=ComplianceMappingDetailOut)
def get_mapping(
    mapping_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> ComplianceMappingDetailOut:
    reads = ComplianceReadService(session, actor)
    pid, mapping = require_found(
        actor,
        Action.COMPLIANCE_READ,
        ResourceType.COMPLIANCE_MAPPING,
        lambda p: reads.mapping(p, mapping_id),
    )
    return ComplianceMappingDetailOut(
        mapping=mapping_out(session, actor, pid, mapping, resolve=True)
    )


# --- gaps ----------------------------------------------------------------------------------


@router.get("/projects/{project_id}/compliance-gaps", response_model=ComplianceGapsOut)
def list_gaps(project_id: uuid.UUID, session: DbSession, actor: CurrentActor) -> ComplianceGapsOut:
    pid = _project(actor, project_id, Action.COMPLIANCE_READ)
    run_id, gaps = ComplianceReadService(session, actor).current_gaps(pid)
    return ComplianceGapsOut(
        graph_run_id=run_id,
        gaps=[
            ComplianceGapOut(
                id=g.id,
                control_key=g.control_key,
                control_title=g.control_title,
                obligation_kind=g.obligation_kind,
                is_high_impact=g.is_high_impact,
                checklist_ref=g.checklist_ref,
                checklist_domain=g.checklist_domain,
                checklist_jurisdiction=g.checklist_jurisdiction,
                origin=g.origin,
                reason=g.reason,
                related_mapping_id=g.related_mapping_id,
                graph_run_id=g.graph_run_id,
                created_at=g.created_at,
            )
            for g in gaps
        ],
    )


# --- security / privacy --------------------------------------------------------------------


@router.get("/projects/{project_id}/security-privacy-findings", response_model=SecurityFindingsOut)
def list_findings(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    version_id: uuid.UUID | None = None,
) -> SecurityFindingsOut:
    pid = _project(actor, project_id, Action.SECURITY_READ)
    reads = ComplianceReadService(session, actor)
    return SecurityFindingsOut(
        findings=[
            finding_out(session, actor, pid, f) for f in reads.findings(pid, version_id=version_id)
        ]
    )


@router.get("/security-privacy-findings/{finding_id}", response_model=SecurityFindingDetailOut)
def get_finding(
    finding_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> SecurityFindingDetailOut:
    reads = ComplianceReadService(session, actor)
    pid, finding = require_found(
        actor,
        Action.SECURITY_READ,
        ResourceType.SECURITY_PRIVACY_FINDING,
        lambda p: reads.finding(p, finding_id),
    )
    return SecurityFindingDetailOut(finding=finding_out(session, actor, pid, finding))


# --- the generated compliance artefact -----------------------------------------------------


@router.get("/projects/{project_id}/compliance-report", response_class=PlainTextResponse)
def compliance_report(
    project_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> PlainTextResponse:
    """The compliance analysis summary (Markdown). Advisory notice first and last."""
    pid = _project(actor, project_id, Action.COMPLIANCE_READ)
    text = ComplianceReadService(session, actor).render_markdown(pid)
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")
