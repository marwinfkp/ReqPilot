"""Request and response models for compliance and security analysis (P6; architecture S).

No request model carries a mapping, a control, a relationship, a jurisdiction, a
risk level, a gap, a gate decision, a lifecycle state or an approval: those are
the pipeline's (validated) or a human's (through ``/approval-tasks/{id}/decide``).
Every response that shows compliance content carries the standing advisory
notice (``FR-CMP-007``) - a server constant, never model output.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from reqpilot.domain.compliance.language import COMPLIANCE_ADVISORY_NOTICE
from reqpilot.domain.enums import (
    ComplianceGapOrigin,
    ComplianceMappingStatus,
    ComplianceRelationship,
    EvidenceStatus,
    FindingDetector,
    GraphRunStatus,
    NormativeSourceType,
    ObligationKind,
    SecurityControlFamily,
    SecurityFindingStatus,
    SecurityPrivacyCategory,
    SecurityRiskLevel,
)

GATE_NOTICE = (
    "High-impact interpretations are decided by the Compliance Officer at G2 and high-risk "
    "security/privacy requirements by the Security Reviewer at G3, through "
    "POST /api/v1/approval-tasks/{id}/decide. Nothing here approves, baselines or changes "
    "a requirement."
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ComplianceRunIn(_Strict):
    #: Empty: every current version of the project.
    version_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    #: Whether the LLM roles run; default: unless the provider is the offline stub.
    semantic: bool | None = None


class ComplianceRunOut(BaseModel):
    advisory_notice: str = COMPLIANCE_ADVISORY_NOTICE
    run_id: uuid.UUID
    status: GraphRunStatus
    evidence_ids: list[uuid.UUID]
    evidence_unavailable_version_ids: list[uuid.UUID]
    compliance_mapping_ids: list[uuid.UUID]
    compliance_gap_ids: list[uuid.UUID]
    security_finding_ids: list[uuid.UUID]
    claims_dropped: int
    gate_task_ids: list[uuid.UUID]
    semantic_failures: int
    provider_calls: int
    tokens_in: int
    tokens_out: int
    errors: list[str]
    notice: str = GATE_NOTICE


class CitationOut(BaseModel):
    """One citation with the provenance J.5 requires (FR-CMP-005)."""

    evidence_id: str
    source_title: str | None = None
    source_type: str | None = None
    binding: str | None = None
    issuing_body: str | None = None
    jurisdiction: str | None = None
    source_version: str | None = None
    effective_date: str | None = None
    curated_on: str | None = None
    item_key: str | None = None
    clause_ref: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    kb_version: int | None = None
    #: The exact quoted span - present when the citation was re-resolved.
    quote: str | None = None
    #: Whether the citation re-resolved against its evidence row just now.
    resolves: bool | None = None


class ComplianceMappingOut(BaseModel):
    id: uuid.UUID
    requirement_version_id: uuid.UUID
    requirement_label: str
    control_key: str
    control_title: str
    obligation_kind: ObligationKind
    checklist_ref: str
    checklist_domain: str
    checklist_jurisdiction: str
    relationship: ComplianceRelationship
    rationale: str
    candidate_text: str | None
    implied_obligation: str | None
    jurisdiction: str
    source_type: NormativeSourceType
    source_type_binding: str
    citations: list[CitationOut]
    is_high_impact: bool
    high_impact_reasons: list[str]
    review_signal: float | None
    status: ComplianceMappingStatus
    approval_task_id: uuid.UUID | None
    graph_run_id: uuid.UUID | None
    agent_run_id: uuid.UUID | None
    language_rules_version: str
    created_at: dt.datetime


class ComplianceMappingsOut(BaseModel):
    advisory_notice: str = COMPLIANCE_ADVISORY_NOTICE
    mappings: list[ComplianceMappingOut]


class ComplianceMappingDetailOut(BaseModel):
    advisory_notice: str = COMPLIANCE_ADVISORY_NOTICE
    mapping: ComplianceMappingOut


class ComplianceGapOut(BaseModel):
    id: uuid.UUID
    control_key: str
    control_title: str
    obligation_kind: ObligationKind
    is_high_impact: bool
    checklist_ref: str
    checklist_domain: str
    checklist_jurisdiction: str
    origin: ComplianceGapOrigin
    reason: str
    related_mapping_id: uuid.UUID | None
    graph_run_id: uuid.UUID
    created_at: dt.datetime


class ComplianceGapsOut(BaseModel):
    advisory_notice: str = COMPLIANCE_ADVISORY_NOTICE
    #: The run whose gap analysis is shown (the latest); ``None``: never run.
    graph_run_id: uuid.UUID | None
    gaps: list[ComplianceGapOut]


class SecurityFindingOut(BaseModel):
    id: uuid.UUID
    requirement_version_id: uuid.UUID
    requirement_label: str
    category: SecurityPrivacyCategory
    family: SecurityControlFamily
    derived_requirement: str
    rationale: str
    risk_rationale: str | None
    evidence_status: EvidenceStatus
    citations: list[CitationOut]
    #: What the model suggested - advisory only, never read by G3.
    proposed_risk_level: str | None
    normalised_proposed_level: SecurityRiskLevel
    catalogue_floor: SecurityRiskLevel
    #: Authoritative, deterministic (I.7). G3 reads this.
    risk_level: SecurityRiskLevel
    risk_rules_version: str
    escalation_reason: str
    detected_by: FindingDetector
    source_signal_finding_id: uuid.UUID | None
    review_signal: float | None
    status: SecurityFindingStatus
    approval_task_id: uuid.UUID | None
    graph_run_id: uuid.UUID | None
    created_at: dt.datetime


class SecurityFindingsOut(BaseModel):
    advisory_notice: str = COMPLIANCE_ADVISORY_NOTICE
    findings: list[SecurityFindingOut]


class SecurityFindingDetailOut(BaseModel):
    advisory_notice: str = COMPLIANCE_ADVISORY_NOTICE
    finding: SecurityFindingOut


def citation_out(snapshot: dict[str, Any], **live: Any) -> CitationOut:
    fields: dict[str, Any] = {k: snapshot.get(k) for k in CitationOut.model_fields if k in snapshot}
    fields.update({k: v for k, v in live.items() if v is not None})
    return CitationOut.model_validate(fields)
