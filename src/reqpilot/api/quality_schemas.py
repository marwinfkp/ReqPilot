"""Request and response models for quality and conflict detection (P5; architecture P, API).

No request model carries a severity, a detector, a finding type the client
chooses for detection, a lifecycle state, an approval or a conflict class: those
are the server's to decide. A human's decisions - resolve, dismiss, the G4
resolution - carry the decision and a reason, nothing else.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from reqpilot.domain.enums import (
    ConflictClass,
    ConflictKind,
    ConflictResolution,
    ConflictStatus,
    FindingDetector,
    FindingSeverity,
    GraphRunStatus,
    QualityFindingStatus,
    QualityFindingType,
    ReviewPriority,
)

NOT_APPROVAL = (
    "Findings and conflicts are proposals for human review. Nothing here approves, "
    "baselines or changes a requirement; resolving a conflict is a human decision (G4)."
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QualityRunIn(_Strict):
    #: Empty: analyse every current version and every pair (FR-CNF-001).
    version_ids: list[uuid.UUID] = Field(default_factory=list, max_length=300)
    #: Whether the LLM semantic layer runs; default: unless the provider is the stub.
    semantic: bool | None = None


class QualityRunOut(BaseModel):
    run_id: uuid.UUID
    status: GraphRunStatus
    quality_finding_ids: list[uuid.UUID]
    conflict_ids: list[uuid.UUID]
    semantic_failures: int
    provider_calls: int
    tokens_in: int
    tokens_out: int
    errors: list[str]
    notice: str = NOT_APPROVAL


class ReasonIn(_Strict):
    reason: str = Field(min_length=1, max_length=2000)


class ResolveConflictIn(_Strict):
    resolution: ConflictResolution
    reason: str = Field(min_length=1, max_length=2000)
    #: With choose_a / choose_b: withdraw the other version (P1, guarded).
    withdraw_other: bool = False


class ConflictClarificationIn(_Strict):
    #: Which side the clarifying question is about.
    side: Literal["a", "b"]
    asked_of_stakeholder_id: uuid.UUID


class GlossaryTermIn(_Strict):
    term: str = Field(min_length=1, max_length=200)
    definition: str = Field(min_length=1, max_length=2000)


class GlossaryTermOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    term: str
    definition: str
    created_at: dt.datetime


class QualityFindingDetailOut(BaseModel):
    """A finding with its detection provenance (FR-QAL-*; architecture R.2)."""

    id: uuid.UUID
    requirement_version_id: uuid.UUID
    requirement_human_id: str | None
    version_no: int | None
    finding_type: QualityFindingType
    severity: FindingSeverity
    review_signal: float | None
    review_priority: ReviewPriority
    rationale: str
    span_quote: str | None
    evidence: list[dict]
    related_version_id: uuid.UUID | None
    detected_by: FindingDetector
    rule_id: str | None
    graph_run_id: uuid.UUID | None
    agent_run_id: uuid.UUID | None
    status: QualityFindingStatus
    resolution_reason: str | None
    resolved_by: uuid.UUID | None
    resolved_at: dt.datetime | None
    created_at: dt.datetime
    notice: str = NOT_APPROVAL


class ConflictSideOut(BaseModel):
    version_id: uuid.UUID
    requirement_human_id: str | None
    version_no: int | None
    statement: str | None
    evidence: str
    stakeholder: str | None


class ConflictOut(BaseModel):
    """A conflict "with both sides" (architecture API: ``GET /projects/{id}/conflicts``)."""

    id: uuid.UUID
    conflict_class: ConflictClass
    kind: ConflictKind
    severity: FindingSeverity
    review_signal: float | None
    review_priority: ReviewPriority
    rationale: str
    a: ConflictSideOut
    b: ConflictSideOut
    involves_stakeholder_disagreement: bool
    detected_by: FindingDetector
    rule_id: str | None
    graph_run_id: uuid.UUID | None
    agent_run_id: uuid.UUID | None
    status: ConflictStatus
    resolution: ConflictResolution | None
    resolution_reason: str | None
    resolved_by: uuid.UUID | None
    resolved_at: dt.datetime | None
    created_at: dt.datetime
    notice: str = NOT_APPROVAL
