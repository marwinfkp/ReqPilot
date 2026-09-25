"""Schemas for the P8 governance, traceability and artefact endpoints.

What these schemas deliberately do **not** carry: no request field can set an
approval, a gate outcome, a baseline, a trace link, a risk severity or an
artefact's authority. A generation request names a baseline and artefact types;
everything else is read from the records.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field

from reqpilot.api.schemas import ApprovalTaskOut
from reqpilot.domain.enums import ArtifactType, Gate


class QueueEntryOut(BaseModel):
    kind: str
    item_id: str
    title: str
    blocking: bool
    severity: str | None
    review_signal: float | None
    created_at: dt.datetime
    gate: Gate | None
    required_role: str | None
    subject_type: str | None
    subject_id: str | None
    subject_label: str | None
    actionable: bool
    link: str | None


class QueueOut(BaseModel):
    ordering_rule: str
    entries: list[QueueEntryOut]


class BlockerOut(BaseModel):
    code: str
    message: str
    gate: Gate | None
    subject_type: str | None
    subject_id: str | None


class ReadinessOut(BaseModel):
    version_id: uuid.UUID
    ready: bool
    blockers: list[BlockerOut]


class FanOutOut(BaseModel):
    g4: list[ApprovalTaskOut]
    g5: list[ApprovalTaskOut]
    g7: list[ApprovalTaskOut]


class FlagIn(BaseModel):
    """An analyst's reason for flagging a version architecture-critical (G5)."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)


class SyncOut(BaseModel):
    created: int
    already_present: int
    by_link_type: dict[str, int]
    unresolved_source_refs: int


class TraceLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    from_type: str
    from_id: str
    link_type: str
    to_type: str
    to_id: str
    anchor_version_id: uuid.UUID | None
    origin: str
    created_at: dt.datetime


class RtmOut(BaseModel):
    scope: str
    columns: list[str]
    rows: list[dict[str, str]]


class CoverageVersionOut(BaseModel):
    version_id: str
    requirement: str
    state: str
    fully_traced: bool
    missing: list[str]


class CoverageOut(BaseModel):
    scope_kind: str
    scope_label: str
    definition_version: str
    total: int
    fully_traced: int
    e6: float | None
    counts: dict[str, int]
    orphan_requirements: list[str]
    unsourced_statements: list[str]
    unlinked_risks: list[str]
    project_level_risks: int
    findings_without_parent: list[str]
    versions: list[CoverageVersionOut]


class GenerateIn(BaseModel):
    """Which artefacts to generate from the baseline. Nothing else is accepted."""

    model_config = ConfigDict(extra="forbid")

    artifact_types: list[ArtifactType] = Field(default_factory=list)


class ArtifactVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    artifact_id: uuid.UUID
    version_no: int
    artifact_type: ArtifactType
    baseline_id: uuid.UUID
    template_id: str
    template_version: str
    generator: str
    model_identifier: str
    prompt_version: str | None
    kb_version: int | None
    kb_version_source: str
    content_hash: str
    input_fingerprint: str
    markdown_sha256: str
    section_count: int
    cited_version_count: int
    generated_by: uuid.UUID
    generated_at: dt.datetime


class GenerationOut(BaseModel):
    artifact_type: ArtifactType
    refused: bool
    reused_identical_version: bool
    blockers: list[str]
    version: ArtifactVersionOut | None


class ArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    artifact_type: ArtifactType
    title: str
    current_version_id: uuid.UUID | None
    created_at: dt.datetime


class SectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    section_key: str
    number: str
    title: str
    ordinal: int
    kind: str
    content_hash: str
    cites_requirement_versions: list[str] = Field(default_factory=list)


class ArtifactVersionDetailOut(BaseModel):
    version: ArtifactVersionOut
    sections: list[SectionOut]
    markdown: str
