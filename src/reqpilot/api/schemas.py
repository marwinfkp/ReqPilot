"""Request and response schemas for the repository API.

Two things these schemas deliberately do **not** expose:

* **No state field on any write schema.** There is no way to ask the API to set
  a lifecycle state; a transition is its own endpoint and is validated against
  the approved table. ``PATCH /requirements/{id}`` creates a successor version,
  it does not edit one.
* **No approval field anywhere except the decide endpoint.** Approval comes from
  a recorded decision or not at all.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field

from reqpilot.domain.enums import (
    ApprovalDecisionType,
    ApprovalTaskStatus,
    Gate,
    RequirementCategory,
    RequirementPriority,
    Role,
)
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.requirement_ids import RequirementKind


class SourceRef(BaseModel):
    """Provenance for a requirement. At least one is required to advance."""

    kind: str = Field(description="utterance | source_chunk | document")
    ref: str
    span: list[int] | None = None


class RequirementContentIn(BaseModel):
    """The governed content of a version, supplied manually.

    Nothing here is generated: this phase has no extraction and no
    classification. A category is set by a human or left unset.
    """

    statement: str = Field(min_length=1, max_length=4000)
    original_text: str | None = Field(default=None, max_length=8000)
    category: RequirementCategory | None = None
    priority: RequirementPriority | None = None
    justification: str | None = Field(default=None, max_length=4000)
    dependencies: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)


class CreateRequirementIn(RequirementContentIn):
    domain: str = Field(description="Identifier domain token, e.g. LOAN")
    kind: RequirementKind = RequirementKind.FUNCTIONAL
    human_id: str | None = Field(
        default=None, description="Optional explicit id; allocated when omitted"
    )


class UpdateRequirementIn(RequirementContentIn):
    """Creates a **new version**. The predecessor is never modified."""

    change_reason: str = Field(min_length=1, max_length=1000)


class TransitionIn(BaseModel):
    """Request one guarded lifecycle transition."""

    target: RequirementState


class WithdrawIn(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class SubmitBaselineIn(BaseModel):
    """The G1 trigger. Creates approval tasks - **not** a baseline."""

    version_ids: list[uuid.UUID] = Field(min_length=1)
    label: str | None = Field(default=None, max_length=200)


class DecideIn(BaseModel):
    """A human decision at a gate. The only approval path in the system."""

    decision: ApprovalDecisionType
    role_exercised: Role
    justification: str | None = Field(default=None, max_length=2000)
    baseline_label: str | None = Field(default=None, max_length=200)


# --- responses --------------------------------------------------------------


class RequirementVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    requirement_id: uuid.UUID
    version_no: int
    state: RequirementState
    statement: str
    original_text: str | None
    category: RequirementCategory | None
    priority: RequirementPriority | None
    justification: str | None
    dependencies: list
    assumptions: list
    source_refs: list
    content_hash: str
    change_reason: str | None
    created_by: uuid.UUID | None
    created_at: dt.datetime
    superseded_by_id: uuid.UUID | None


class RequirementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    human_id: str
    current_version_id: uuid.UUID | None
    baselined_version_id: uuid.UUID | None
    created_at: dt.datetime


class RequirementDetailOut(BaseModel):
    requirement: RequirementOut
    current_version: RequirementVersionOut | None
    versions: list[RequirementVersionOut]
    available_transitions: list[RequirementState]


class ApprovalDecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_id: uuid.UUID
    decided_by: uuid.UUID
    role_exercised: Role
    decision: ApprovalDecisionType
    justification: str | None
    subject_version_hash: str
    decided_at: dt.datetime


class ApprovalTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    gate: Gate
    task_group_id: uuid.UUID | None
    subject_type: str
    subject_id: uuid.UUID
    subject_version: str | None
    subject_version_hash: str
    required_role: str
    status: ApprovalTaskStatus
    blocking: bool
    created_at: dt.datetime


class DecisionOut(BaseModel):
    """What a decision did, including whether it committed a baseline."""

    task: ApprovalTaskOut
    decision: ApprovalDecisionOut
    task_closed: bool
    group_complete: bool
    baseline_id: uuid.UUID | None


class BaselineMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    requirement_version_id: uuid.UUID
    version_hash: str


class BaselineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    label: str
    created_by: uuid.UUID | None
    approval_decision_id: uuid.UUID
    frozen_at: dt.datetime


class BaselineDetailOut(BaseModel):
    baseline: BaselineOut
    members: list[RequirementVersionOut]


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seq: int
    occurred_at: dt.datetime
    actor_kind: str
    actor_ref: str
    event_type: str
    subject_type: str | None
    subject_id: str | None
    subject_version: str | None
    payload: dict
