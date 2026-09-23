"""Request and response models for risk analysis and the register (P7; architecture S).

**No request model carries a severity.** Not the run request, not the
human-entered risk, not the decision. A person supplies the two ordinal ratings
and their rationales, exactly as a model does, and the published matrix turns
them into the severity (``FR-RSK-004``). There is likewise no field for a gate
decision, a G8 status, an approval, a lifecycle state or a baseline: G8 is
decided through ``POST /approval-tasks/{id}/decide``, and nothing here approves,
baselines or changes a requirement.

Every response that shows the register carries the standing register notice - a
server constant, never model output - because a reader who sees ordinal ratings
in a table may otherwise read them as probabilities, and because an AI-suggested
mitigation must be labelled as one (``FR-RSK-005``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from reqpilot.domain.enums import (
    FindingDetector,
    GraphRunStatus,
    MitigationStatus,
    RiskCategory,
    RiskImpact,
    RiskLikelihood,
    RiskScope,
    RiskSeverity,
    RiskStatus,
    Role,
)
from reqpilot.services.risk.register import REGISTER_NOTICE

GATE_NOTICE = (
    "A high-severity risk is reviewed by the Security Reviewer at G8, through "
    "POST /api/v1/approval-tasks/{id}/decide, and blocks its requirement's baseline until "
    "it is. Nothing here decides G8, approves or baselines anything."
)

SCOPE_NOTICE = (
    "Risks are project, engineering, security, privacy, compliance and operational risks. "
    "ReqPilot does not compute borrower credit risk, customer risk ratings, probability of "
    "default or fraud scores, and makes no lending decision (FR-RSK-011)."
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RiskRunIn(_Strict):
    #: Empty: every current version of the project.
    version_ids: list[uuid.UUID] = Field(default_factory=list, max_length=60)
    #: Whether the LLM role runs; default: unless the provider is the offline stub.
    semantic: bool | None = None


class RiskRunOut(BaseModel):
    run_id: uuid.UUID
    status: GraphRunStatus
    risk_ids: list[uuid.UUID]
    #: Proposals the FR-RSK-011 scope guard refused. Counted, never stored.
    risks_out_of_scope: int
    claims_dropped: int
    gate_task_ids: list[uuid.UUID]
    semantic_failures: int
    provider_calls: int
    tokens_in: int
    tokens_out: int
    errors: list[str]
    notice: str = REGISTER_NOTICE
    gate_notice: str = GATE_NOTICE


class MitigationOut(BaseModel):
    id: uuid.UUID
    suggestion: str
    is_ai_generated: bool
    status: MitigationStatus
    #: How the register presents it: AI-suggested mitigations are labelled as
    #: requiring human validation until a human accepts one (``FR-RSK-005``).
    label: str


class RiskOut(BaseModel):
    """One register entry (``FR-RSK-008``)."""

    id: uuid.UUID
    scope: RiskScope
    requirement_version_id: uuid.UUID | None
    requirement_label: str | None
    category: RiskCategory
    title: str
    description: str
    likelihood: RiskLikelihood
    impact: RiskImpact
    #: **Computed** by the matrix from the two ratings. Never supplied.
    severity: RiskSeverity
    matrix_version: str
    likelihood_rationale: str
    impact_rationale: str
    mitigations: list[MitigationOut]
    citations: list[dict[str, Any]]
    evidence_count: int
    owner_role: Role
    status: RiskStatus
    detected_by: FindingDetector
    approval_task_id: uuid.UUID | None
    decision_rationale: str | None
    #: Whether this entry still blocks its requirement's baseline (``FR-RSK-007``).
    blocking_baseline: bool
    created_at: dt.datetime
    updated_at: dt.datetime


class RisksOut(BaseModel):
    risks: list[RiskOut]
    notice: str = REGISTER_NOTICE
    scope_notice: str = SCOPE_NOTICE


class FactorInputOut(BaseModel):
    """One I.6 SDLC factor input (``FR-RSK-009``). An input, not a recommendation."""

    key: str
    value: int
    description: str
    counts: dict[str, int]
    evidence_risk_ids: list[uuid.UUID]


class RegisterOut(BaseModel):
    """The register plus its aggregate measures."""

    risks: list[RiskOut]
    by_severity: dict[str, int]
    by_category: dict[str, int]
    by_status: dict[str, int]
    blocking_baseline: int
    matrix_version: str
    rules_version: str
    #: FR-RSK-009: aggregate measures as inputs to the SDLC factor profile. P9
    #: does the scoring; P7 exposes the inputs and stops there.
    factor_inputs: list[FactorInputOut]
    notice: str = REGISTER_NOTICE
    scope_notice: str = SCOPE_NOTICE
    gate_notice: str = GATE_NOTICE


class MatrixCellOut(BaseModel):
    likelihood: RiskLikelihood
    impact: RiskImpact
    severity: RiskSeverity


class MatrixOut(BaseModel):
    """The published matrix (``FR-RSK-004``). Readable so a rating is explainable."""

    matrix_version: str
    cells: list[MatrixCellOut]
    likelihood_scale: dict[str, str]
    impact_scale: dict[str, str]
    escalating_severity: RiskSeverity
    notice: str = REGISTER_NOTICE


class RiskIn(_Strict):
    """A human adding a risk (``FR-RSK-010``). No severity field: the matrix rates it."""

    category: RiskCategory
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=2000)
    likelihood: RiskLikelihood
    impact: RiskImpact
    likelihood_rationale: str = Field(min_length=1, max_length=1200)
    impact_rationale: str = Field(min_length=1, max_length=1200)
    #: FR-RSK-006: at least one piece of this project's evidence.
    evidence_ids: list[uuid.UUID] = Field(min_length=1, max_length=8)
    requirement_version_id: uuid.UUID | None = None
    mitigation: str | None = Field(default=None, max_length=600)


class RiskDecisionIn(_Strict):
    """Accepting, mitigating, rejecting or closing a risk, with the rationale."""

    status: RiskStatus
    rationale: str = Field(min_length=1, max_length=2000)


class MitigationDecisionIn(_Strict):
    status: MitigationStatus
    rationale: str | None = Field(default=None, max_length=2000)


class MitigationIn(_Strict):
    suggestion: str = Field(min_length=1, max_length=600)
