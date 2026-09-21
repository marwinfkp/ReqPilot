"""Request and response models for elicitation and clarification (P4; architecture P, API).

No request model carries a lifecycle state, an approval, a coverage value, a
follow-up count, a topic choice or a graph route: those are the server's to
decide. A session or clarification is addressed by its id in the path, and the
server derives everything else from authorised, persisted state.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field

from reqpilot.domain.enums import (
    ClarificationStatus,
    DataSensitivity,
    FindingSeverity,
    InterviewSessionStatus,
    QualityFindingStatus,
    QualityFindingType,
    ReanalysisStatus,
    SpeakerKind,
    StakeholderAuthority,
)

NOT_APPROVAL = (
    "Answering an interview or a clarification approves nothing. Requirements are "
    "approved only at gate G1, by an Analyst and a Compliance Officer."
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- stakeholders -------------------------------------------------------------------


class StakeholderIn(_Strict):
    name: str = Field(min_length=1, max_length=200)
    #: A role of the interview templates, e.g. ``product_owner``.
    stakeholder_role: str = Field(min_length=1, max_length=50)
    authority_level: StakeholderAuthority
    #: The project member (holding the Stakeholder role) who is this stakeholder.
    user_id: uuid.UUID | None = None


class StakeholderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    stakeholder_role: str
    authority_level: StakeholderAuthority
    user_id: uuid.UUID | None
    created_at: dt.datetime


# --- sessions ---------------------------------------------------------------------


class SessionIn(_Strict):
    stakeholder_id: uuid.UUID
    #: Defaults to the template of the stakeholder's role (``FR-ELI-001``).
    template_id: str | None = Field(default=None, max_length=100)
    #: Declared by the analyst (``FR-ING-004``). Only ``synthetic`` text may leave
    #: this machine for an external model until masking exists (P11).
    sensitivity: DataSensitivity = DataSensitivity.UNCLASSIFIED


class AnswerIn(_Strict):
    text: str = Field(min_length=1, max_length=4000)


class UtteranceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    seq: int
    speaker_kind: SpeakerKind
    speaker_ref: uuid.UUID | None
    stakeholder_role: str | None
    text: str
    topic_id: str | None
    is_followup: bool
    replies_to_id: uuid.UUID | None
    recorded_by: uuid.UUID | None
    on_behalf: bool
    created_at: dt.datetime


class CoverageOut(BaseModel):
    """Live coverage (``FR-ELI-006``) - computed by the deterministic tracker."""

    session_id: uuid.UUID
    status: InterviewSessionStatus
    current_topic: str | None
    followups_this_topic: int
    max_followups: int
    applicable: list[str]
    covered: list[str]
    unresolved: list[str]
    in_progress: list[str]
    remaining: list[str]
    required_remaining: list[str]
    topics: dict[str, dict]


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    stakeholder_id: uuid.UUID
    template_id: str | None
    template_version: str | None
    status: InterviewSessionStatus
    sensitivity: DataSensitivity
    current_topic: str | None
    followups_this_topic: int
    questions_asked: int
    followups_asked: int
    stall_reason: str | None
    graph_run_id: uuid.UUID | None
    created_at: dt.datetime
    updated_at: dt.datetime
    completed_at: dt.datetime | None


class TurnOut(BaseModel):
    """``{utterance, next_question | complete}`` (architecture API)."""

    session: SessionOut
    utterance: UtteranceOut | None = None
    next_question: UtteranceOut | None
    complete: bool
    coverage: CoverageOut
    notice: str = NOT_APPROVAL


class SuggestionOut(BaseModel):
    """``FR-ELI-007`` (secondary): who to interview next, and why."""

    stakeholder_role: str | None
    template_id: str | None
    gap_topics: list[str]
    covered_topics: list[str]
    basis: str


# --- quality findings and clarifications --------------------------------------------


class FindingIn(_Strict):
    finding_type: QualityFindingType
    severity: FindingSeverity
    rationale: str = Field(min_length=1, max_length=2000)
    #: Words of the statement the finding is about, if any.
    span_quote: str | None = Field(default=None, max_length=500)


class FindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    requirement_version_id: uuid.UUID
    finding_type: QualityFindingType
    severity: FindingSeverity
    rationale: str
    span_quote: str | None
    status: QualityFindingStatus
    created_at: dt.datetime


class RaiseClarificationIn(_Strict):
    asked_of_stakeholder_id: uuid.UUID


class ClarificationAnswerIn(_Strict):
    answer: str = Field(min_length=1, max_length=4000)


class DismissIn(_Strict):
    reason: str = Field(min_length=1, max_length=2000)


class ClarificationOut(BaseModel):
    """One row of the open-issues list (``FR-CLR-002``)."""

    id: uuid.UUID
    requirement_version_id: uuid.UUID
    requirement_human_id: str
    version_no: int
    statement: str
    quality_finding_id: uuid.UUID
    finding_type: QualityFindingType
    question: str
    expected_answer_shape: str
    status: ClarificationStatus
    asked_of_stakeholder_id: uuid.UUID
    stakeholder_name: str
    assignee_user_id: uuid.UUID | None
    age_seconds: int
    created_at: dt.datetime
    answered_at: dt.datetime | None
    answer: str | None
    dismissed_reason: str | None
    reanalysis_status: ReanalysisStatus | None
    resulting_version_id: uuid.UUID | None
    resulting_version_no: int | None
    notice: str = NOT_APPROVAL


class ClarificationAnswerOut(BaseModel):
    clarification: ClarificationOut
    reanalysis_run_id: uuid.UUID | None
    error: str | None = None
