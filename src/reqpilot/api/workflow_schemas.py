"""Request and response models for project workflows (P10; ``FR-WFL-001``..``-008``).

**No request model carries a source, a provenance link, a key, a kind, a
``mandatory`` flag, a phase, a project, an SDLC selection or an approval.** A
workflow is generated from a run id alone - G6 is verified from the persisted
approval records - and an edit names only the wording it changes and a reason.
``extra="forbid"`` makes an attempt to send any other field a 422, not a silently
ignored value.

Every response that shows a workflow carries the standing notice - a server
constant - because a reader must be able to tell the gates *in* the generated
workflow (production readiness included) from ReqPilot's own gates G1-G8.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from reqpilot.domain.enums import GraphRunStatus, WorkflowStatus

WORKFLOW_NOTICE = (
    "This workflow is generated deterministically from the SDLC selection that passed G6 and "
    "the project's approved records. Its gates - the production-readiness approval included - "
    "are gates of the generated project's own process, not ReqPilot gates G1-G8: ReqPilot "
    "records them and does not operate them. Every compliance checkpoint and risk activity "
    "names the record it was derived from; edits by the Project Manager are logged."
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Reason(_Strict):
    #: Why the Project Manager made the change (FR-WFL-007; recorded in the change log).
    reason: str = Field(min_length=1, max_length=2000)


class PhaseEditIn(_Reason):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    responsible_roles: list[str] | None = Field(default=None, max_length=50)
    deliverables: list[str] | None = Field(default=None, max_length=50)
    entry_criteria: list[str] | None = Field(default=None, max_length=50)
    exit_criteria: list[str] | None = Field(default=None, max_length=50)
    testing_requirements: list[str] | None = Field(default=None, max_length=50)
    traceability_requirements: list[str] | None = Field(default=None, max_length=50)


class ActivityEditIn(_Reason):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    responsible_roles: list[str] | None = Field(default=None, max_length=50)
    deliverables: list[str] | None = Field(default=None, max_length=50)


class GateEditIn(_Reason):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    purpose: str | None = Field(default=None, max_length=4000)
    approver_roles: list[str] | None = Field(default=None, max_length=50)
    required_evidence: list[str] | None = Field(default=None, max_length=50)
    entry_criteria: list[str] | None = Field(default=None, max_length=50)
    exit_criteria: list[str] | None = Field(default=None, max_length=50)


class ActivityAddIn(_Reason):
    name: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=4000)
    responsible_roles: list[str] = Field(min_length=1, max_length=50)
    deliverables: list[str] = Field(min_length=1, max_length=50)


class ActivityRemoveIn(_Reason):
    pass


def edit_changes(payload: BaseModel) -> dict[str, Any]:
    """The fields a client actually sent, minus the reason."""
    return {k: v for k, v in payload.model_dump(exclude_unset=True).items() if k != "reason"}


class WorkflowGenerateOut(BaseModel):
    graph_run_id: uuid.UUID
    status: GraphRunStatus
    sdlc_run_id: uuid.UUID | None
    workflow_id: uuid.UUID | None
    workflow_status: str | None
    #: The same run and approved inputs already had this workflow; it was returned.
    reused: bool
    errors: list[str]
    #: A refusal's validation codes.
    findings: list[dict[str, str]]


class FindingOut(BaseModel):
    code: str
    severity: str
    message: str
    subject: str = ""


class SourceOut(BaseModel):
    source_type: str
    source_id: str
    relation: str
    label: str


class ActivityOut(BaseModel):
    id: uuid.UUID
    key: str
    kind: str
    name: str
    description: str
    responsible_roles: list[str]
    deliverables: list[str]
    mandatory: bool
    origin: str
    sources: list[SourceOut]


class GateOut(BaseModel):
    id: uuid.UUID
    key: str
    kind: str
    name: str
    purpose: str
    approver_roles: list[str]
    required_evidence: list[str]
    entry_criteria: list[str]
    exit_criteria: list[str]
    mandatory: bool
    origin: str
    sources: list[SourceOut]
    #: Always false: a workflow gate is a gate of the generated project, not G1-G8.
    is_reqpilot_gate: Literal[False] = False


class PhaseOut(BaseModel):
    id: uuid.UUID
    position: int
    key: str
    name: str
    description: str
    stages: list[str]
    cycle: str | None
    verifies_phase_key: str | None
    responsible_roles: list[str]
    deliverables: list[str]
    entry_criteria: list[str]
    exit_criteria: list[str]
    testing_requirements: list[str]
    traceability_requirements: list[str]
    origin: str
    activities: list[ActivityOut]
    gates: list[GateOut]


class WorkflowSummaryOut(BaseModel):
    id: uuid.UUID
    sdlc_run_id: uuid.UUID
    candidate_key: str
    status: WorkflowStatus
    revision: int
    open_items: int
    created_at: dt.datetime
    updated_at: dt.datetime


class WorkflowOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    sdlc_run_id: uuid.UUID
    selected_candidate_id: uuid.UUID
    candidate_key: str
    candidate_label: str
    composition: list[str]
    approach: str
    baseline_id: uuid.UUID
    supersedes_workflow_id: uuid.UUID | None
    status: WorkflowStatus
    revision: int
    template_ref: str
    input_fingerprint: str
    generated_hash: str
    content_hash: str
    realises: list[SourceOut]
    open_items: list[FindingOut]
    phases: list[PhaseOut]
    notice: str = WORKFLOW_NOTICE
    created_at: dt.datetime
    updated_at: dt.datetime


class ChangeOut(BaseModel):
    id: uuid.UUID
    revision: int
    operation: str
    element_type: str
    element_id: uuid.UUID
    element_key: str
    changes: dict[str, Any]
    reason: str
    actor_id: uuid.UUID
    role_exercised: str
    content_hash_before: str
    content_hash_after: str
    created_at: dt.datetime
