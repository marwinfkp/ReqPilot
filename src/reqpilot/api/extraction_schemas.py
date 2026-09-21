"""Request and response schemas for sources, extraction runs, labels and reviews.

Deliberately absent from every write schema: a lifecycle state, an approval, a
requirement identifier, a risk level and applicable regulations. None of those
can be supplied through this surface - the pipeline and P1 own the first three,
and later phases own the last two.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from reqpilot.domain.enums import (
    AgentRole,
    AgentRunStatus,
    CandidateStatus,
    DataSensitivity,
    GraphRunStatus,
    MaskingStatus,
    ProposalSource,
    RequirementCategory,
    RequirementPriority,
    ReviewReason,
    ReviewResolution,
    ReviewStatus,
    SourceDocumentType,
)
from reqpilot.domain.lifecycle import RequirementState

SIGNAL_CAVEAT = "review-prioritisation signal, not a calibrated probability"

# --- sources ------------------------------------------------------------------


class SourceTextIn(BaseModel):
    doc_type: SourceDocumentType
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1)
    #: Declared by the uploader. Unmasked content may leave the machine - to an
    #: external model such as the OpenAI provider - only if it is synthetic.
    sensitivity: DataSensitivity = DataSensitivity.UNCLASSIFIED


class SourceChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ordinal: int
    char_start: int
    char_end: int
    speaker: str | None
    strategy: str
    text: str


class SourceDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    doc_type: SourceDocumentType
    title: str
    filename: str | None
    content_hash: str
    char_count: int
    sensitivity: DataSensitivity
    masking_status: MaskingStatus
    masker_id: str
    created_at: dt.datetime


class SourceCreatedOut(BaseModel):
    created: bool
    source: SourceDocumentOut


class SourceDetailOut(SourceDocumentOut):
    chunks: list[SourceChunkOut]


# --- runs ---------------------------------------------------------------------


class AnalysisRunIn(BaseModel):
    """Batch extraction from sources and/or interview sessions, or classification."""

    source_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    #: P4: interview sessions whose stakeholder answers are extracted from
    #: (architecture API ``scope.utterance_ids``, taken a session at a time).
    session_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    #: The identifier domain token, e.g. ``LOAN`` (``FR-EXT-004``).
    domain: str | None = Field(default=None, max_length=16)
    version_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _one_scope(self) -> AnalysisRunIn:
        extracting = bool(self.source_ids or self.session_ids)
        if extracting == bool(self.version_ids):
            raise ValueError(
                "give source_ids and/or session_ids (extraction), or version_ids (classification)"
            )
        if extracting and not self.domain:
            raise ValueError("an extraction run needs the identifier domain, e.g. LOAN")
        return self


class RunSummaryOut(BaseModel):
    run_id: uuid.UUID
    status: GraphRunStatus
    requirement_version_ids: list[uuid.UUID]
    classified_version_ids: list[uuid.UUID]
    review_item_ids: list[uuid.UUID]
    accepted: int
    merged: int
    rejected: int
    errors: list[str]
    provider_calls: int
    tokens_in: int
    tokens_out: int
    cost_estimate: float | None


class AgentRunOut(BaseModel):
    id: uuid.UUID
    node: str
    role: AgentRole
    status: AgentRunStatus
    prompt: str | None
    provider: str | None
    model: str | None
    is_model: bool | None
    attempts: int | None
    tokens_in: int | None
    tokens_out: int | None
    cost_estimate: float | None
    error_code: str | None


class CandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_key: str
    ordinal: int
    statement: str
    status: CandidateStatus
    review_signal: float
    original_text: str | None
    spans: list
    findings: list
    requirement_version_id: uuid.UUID | None
    merged_into_id: uuid.UUID | None


class RunDetailOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    graph_name: str
    status: GraphRunStatus
    started_by: uuid.UUID | None
    started_at: dt.datetime
    finished_at: dt.datetime | None
    agent_runs: list[AgentRunOut]
    candidates: list[CandidateOut]


# --- review queue -------------------------------------------------------------


class ReviewItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reason: ReviewReason
    subject_type: str
    subject_id: uuid.UUID
    related_subject_id: uuid.UUID | None
    requirement_version_id: uuid.UUID | None
    category: RequirementCategory | None
    review_signal: float | None
    detail: dict
    status: ReviewStatus
    resolution: ReviewResolution | None
    resolution_note: str | None
    resolved_by: uuid.UUID | None
    resolved_at: dt.datetime | None
    created_at: dt.datetime
    #: Always present, so the queue is never mistaken for approval.
    notice: str = (
        "AI-proposal review. Resolving this item approves nothing: requirements are "
        "approved only at gate G1, by an Analyst and a Compliance Officer."
    )


class ResolveIn(BaseModel):
    resolution: ReviewResolution
    note: str | None = Field(default=None, max_length=2000)
    categories: list[RequirementCategory] = Field(default_factory=list, max_length=13)
    keep: Literal["subject", "related"] = "related"


# --- classification, criteria, the record -------------------------------------


class LabelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    category: RequirementCategory
    review_signal: float | None
    source: ProposalSource
    needs_review: bool
    rationale: str | None
    revision_no: int
    created_by: uuid.UUID | None
    change_reason: str | None
    created_at: dt.datetime


class ClassificationOut(BaseModel):
    requirement_version_id: uuid.UUID
    current: list[LabelOut]
    history: list[list[LabelOut]]
    signal_interpretation: str = SIGNAL_CAVEAT


class OverrideIn(BaseModel):
    categories: list[RequirementCategory] = Field(min_length=1, max_length=13)
    reason: str = Field(min_length=1, max_length=1000)


class CriterionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ordinal: int
    given_text: str
    when_text: str
    then_text: str
    source: ProposalSource
    copied_from_id: uuid.UUID | None


class MergeIn(BaseModel):
    duplicate_requirement_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=1000)


class RequirementRecordOut(BaseModel):
    """The thirteen fields of the problem statement's section 8 (``FR-EXT-002``)."""

    id: str
    requirement_id: uuid.UUID
    version_id: uuid.UUID
    version_no: int
    statement: str
    original_text: str | None
    category: list[RequirementCategory]
    source_stakeholders: list[str]
    source_refs: list
    business_justification: str | None
    priority: RequirementPriority | None
    dependencies: list
    assumptions: list
    acceptance_criteria: list[CriterionOut]
    applicable_regulations: str
    risk_level: str
    confidence_score: float | None
    confidence_interpretation: str = SIGNAL_CAVEAT
    approval_status: RequirementState
    open_review_items: int | None
