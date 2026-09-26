"""Request and response models for the SDLC recommendation (P9; architecture S, L, M.3).

**No request model carries a ranking, a candidate score, a weight, a rule, a
selection or a G6 decision.** A run is started from a baseline; a factor is
overridden with a score and a reason (``FR-SDL-003``); an explanation is retried.
The ranking comes only from the versioned MCDA and rules, and the selection only
from four human G6 approvals through ``POST /approval-tasks/{id}/decide``.

Every response that shows a recommendation carries the standing notice - a
server constant, never model output - because a reader must know that the
explanation is advisory and that the ranking, not the model, is authoritative.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from reqpilot.domain.enums import (
    ApprovalTaskStatus,
    ExplanationStatus,
    GraphRunStatus,
    Role,
    SdlcRunStatus,
)

SDLC_NOTICE = (
    "The ranking is computed by a versioned multi-criteria model and declarative rules from "
    "the approved factor profile. The explanation is AI-generated and advisory: it is checked "
    "against the computed ranking, and any discrepancy is shown, never silently corrected. "
    "The SDLC is selected only when the Project Manager, Architect, Security Reviewer and "
    "Compliance Officer all approve it at G6 (POST /api/v1/approval-tasks/{id}/decide)."
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SdlcRunIn(_Strict):
    baseline_id: uuid.UUID
    #: Whether the LLM role runs; default: unless the provider is the offline stub.
    semantic: bool | None = None


class FactorOverrideIn(_Strict):
    score: int = Field(ge=1, le=5)
    reason: str = Field(min_length=1, max_length=2000)
    #: The role the override is made in (Analyst or Project Manager).
    role: Role


class SdlcRunStartOut(BaseModel):
    graph_run_id: uuid.UUID
    status: GraphRunStatus
    sdlc_run_id: uuid.UUID | None
    explanation_status: str | None
    g6_task_ids: list[uuid.UUID]
    proposals_accepted: int
    proposals_rejected: int
    semantic_failures: int
    provider_calls: int
    tokens_in: int
    tokens_out: int
    errors: list[str]


class FactorOut(BaseModel):
    factor_id: str
    label: str
    score: int
    source: str
    weight: float
    derived_score: int
    rationale: str
    evidence_refs: list[str]
    evidence_state: str
    basis: dict[str, Any]
    proposal_status: str
    proposed_score: int | None
    proposal_rationale: str | None
    proposal_evidence_refs: list[str]
    proposal_rejection_reason: str | None
    is_overridden: bool
    previous_score: int | None
    override_reason: str | None
    overridden_by: uuid.UUID | None
    override_role: str | None
    overridden_at: dt.datetime | None


class CandidateOut(BaseModel):
    candidate_key: str
    label: str
    rank: int
    score: float
    mcda_score: float
    raw_score: float
    max_raw: float
    vetoed_by: list[str]
    boosted_by: list[str]
    required_by: list[str]
    contributions: dict[str, float]


class RuleApplicationOut(BaseModel):
    rule_id: str
    effect: str
    affected_candidate: str | None
    changed_ranking: bool
    reason: str
    trigger_values: dict[str, int]


class G6TaskOut(BaseModel):
    id: uuid.UUID
    required_role: Role
    status: ApprovalTaskStatus
    subject_version_hash: str


class ExplanationOut(BaseModel):
    status: ExplanationStatus
    #: AI-generated and advisory.
    narrative: str | None
    counter_arguments: list[dict[str, Any]]
    #: Non-authoritative: what the model asserted, shown beside the computed values.
    asserted_top_candidate: str | None
    asserted_scores: dict[str, float]
    discrepancies: list[dict[str, str]]
    attempts: int
    model: str | None
    prompt: str | None


class SdlcRunSummaryOut(BaseModel):
    id: uuid.UUID
    status: SdlcRunStatus
    baseline_id: uuid.UUID
    top_candidate: str
    runner_up_candidate: str | None
    explanation_status: ExplanationStatus
    selected_candidate: str | None
    supersedes_run_id: uuid.UUID | None
    created_at: dt.datetime


class SdlcRunOut(BaseModel):
    notice: str = SDLC_NOTICE
    id: uuid.UUID
    project_id: uuid.UUID
    status: SdlcRunStatus
    baseline_id: uuid.UUID
    graph_run_id: uuid.UUID | None
    supersedes_run_id: uuid.UUID | None
    ruleset_ref: str
    rules_sha256: str
    weights_version: str
    input_fingerprint: str
    profile_hash: str
    ranking_hash: str
    recommendation_hash: str | None
    top_candidate: str
    runner_up_candidate: str | None
    reversal_conditions: list[dict[str, Any]]
    facts_summary: dict[str, Any]
    factors: list[FactorOut]
    candidates: list[CandidateOut]
    rules_applied: list[RuleApplicationOut]
    explanation: ExplanationOut
    g6_task_group_id: uuid.UUID | None
    g6_tasks: list[G6TaskOut]
    g6_status: str
    selected_candidate: str | None
    selected_at: dt.datetime | None
    created_by: uuid.UUID
    created_at: dt.datetime
