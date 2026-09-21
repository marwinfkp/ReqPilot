"""Request and response schemas for the knowledge-base and retrieval API.

What these schemas deliberately do **not** accept:

* **No evidence from the client.** Evidence is only ever recorded server-side,
  from a retrieval the server itself just ran. There is no endpoint that takes a
  chunk id and turns it into evidence.
* **No retrieval scope from the client.** A retrieval request names the project
  and may narrow by source type and applicability. It cannot name a source,
  a jurisdiction or a KB version: those come from the project (J.4).
* **No vectors in any response.** Embeddings are an implementation detail of
  retrieval and are never returned.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field

from reqpilot.domain.enums import (
    SOURCE_TYPE_BINDING,
    ChunkStrategy,
    KnowledgeItemStatus,
    LicenceClass,
    NormativeSourceType,
    RequirementCategory,
    SupersessionKind,
    TextOrigin,
)
from reqpilot.domain.models.knowledge import (
    Control,
    Evidence,
    KnowledgeChunk,
    KnowledgeItem,
    NormativeSource,
)
from reqpilot.retrieval.contracts import (
    Citation,
    EmptyReason,
    RetrievalOutcome,
    RetrievedChunk,
)

# ---------------------------------------------------------------------------
# Knowledge-base administration
# ---------------------------------------------------------------------------


class SourceIn(BaseModel):
    source_type: NormativeSourceType
    issuing_body: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=500)
    jurisdiction: str = Field(min_length=2, max_length=20)
    version: str = Field(min_length=1, max_length=100)
    effective_date: dt.date | None = None
    retrieved_at: dt.date
    source_url: str | None = Field(default=None, max_length=1000)
    licence_class: LicenceClass
    licence_note: str = Field(min_length=1, max_length=2000)


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_type: NormativeSourceType
    binding: str
    issuing_body: str
    title: str
    jurisdiction: str
    version: str
    effective_date: dt.date | None
    retrieved_at: dt.date
    source_url: str | None
    licence_class: LicenceClass
    licence_note: str

    @classmethod
    def of(cls, source: NormativeSource) -> SourceOut:
        return cls(
            id=source.id,
            source_type=source.source_type,
            binding=SOURCE_TYPE_BINDING[source.source_type],
            issuing_body=source.issuing_body,
            title=source.title,
            jurisdiction=source.jurisdiction,
            version=source.version,
            effective_date=source.effective_date,
            retrieved_at=source.retrieved_at,
            source_url=source.source_url,
            licence_class=source.licence_class,
            licence_note=source.licence_note,
        )


class ControlIn(BaseModel):
    normative_source_id: uuid.UUID
    control_ref: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=500)
    paraphrase: str = Field(min_length=1, max_length=8000)
    applicability: list[str] = Field(default_factory=list)


class ControlOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    normative_source_id: uuid.UUID
    control_ref: str
    title: str
    paraphrase: str
    applicability: list[str]


class ItemContentIn(BaseModel):
    text: str = Field(min_length=1, max_length=60000)
    text_origin: TextOrigin
    title: str | None = Field(default=None, max_length=500)
    clause_ref: str | None = Field(default=None, max_length=200)
    applicability: list[str] = Field(default_factory=list)
    control_id: uuid.UUID | None = None


class ItemIn(ItemContentIn):
    """``POST /kb/items`` (architecture S) - version 1 of a new item."""

    normative_source_id: uuid.UUID
    item_key: str = Field(min_length=3, max_length=100)


class VersionIn(ItemContentIn):
    """A new version of an item. It supersedes the current one (``FR-RAG-006``)."""

    reason: str = Field(min_length=1, max_length=1000)
    normative_source_id: uuid.UUID | None = None


class RetireIn(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class SupersedeIn(BaseModel):
    """``POST /kb/items/{id}/supersede`` (architecture S)."""

    superseded_by_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=1000)


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_key: str
    version_no: int
    normative_source_id: uuid.UUID
    control_id: uuid.UUID | None
    title: str | None
    clause_ref: str | None
    text_origin: TextOrigin
    content_hash: str
    applicability: list[str]
    kb_version: int
    status: KnowledgeItemStatus
    superseded_by_id: uuid.UUID | None
    supersession_kind: SupersessionKind | None
    superseded_in_kb_version: int | None
    supersession_reason: str | None
    created_at: dt.datetime


class ChunkOut(BaseModel):
    """A chunk's identity, span and structure. Never its vector."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ordinal: int
    char_start: int
    char_end: int
    strategy: ChunkStrategy
    structure_label: str | None
    token_count: int
    embedding_model: str
    text: str

    @classmethod
    def of(cls, chunk: KnowledgeChunk) -> ChunkOut:
        return cls.model_validate(chunk)


class ItemDetailOut(ItemOut):
    text: str
    chunks: list[ChunkOut]
    versions: list[ItemOut]

    @classmethod
    def of(
        cls, item: KnowledgeItem, chunks: list[KnowledgeChunk], versions: list[KnowledgeItem]
    ) -> ItemDetailOut:
        base = ItemOut.model_validate(item).model_dump()
        return cls(
            **base,
            text=item.text,
            chunks=[ChunkOut.of(c) for c in chunks],
            versions=[ItemOut.model_validate(v) for v in versions],
        )


class KbVersionOut(BaseModel):
    current_kb_version: int


# ---------------------------------------------------------------------------
# Project scope
# ---------------------------------------------------------------------------


class ScopeIn(BaseModel):
    jurisdiction_scope: list[str] = Field(default_factory=list)
    kb_version_pin: int | None = Field(default=None, ge=1)


class AllowlistIn(BaseModel):
    normative_source_id: uuid.UUID


class ScopeOut(BaseModel):
    project_id: uuid.UUID
    jurisdiction_scope: list[str]
    kb_version_pin: int | None
    current_kb_version: int
    effective_kb_version: int
    allowlisted_sources: list[SourceOut]


# ---------------------------------------------------------------------------
# Retrieval, evidence, citations
# ---------------------------------------------------------------------------


class RetrievalIn(BaseModel):
    """One retrieval. May narrow the project's scope; can never widen it."""

    query: str = Field(min_length=1, max_length=2000)
    source_types: list[NormativeSourceType] = Field(default_factory=list)
    applicability: list[str] = Field(default_factory=list)
    requirement_category: RequirementCategory | None = None
    as_of: dt.date | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)
    #: Persist the result as evidence, server-side, in the same transaction.
    record_evidence: bool = False
    graph_run_id: uuid.UUID | None = None


class RetrievedChunkOut(BaseModel):
    rank: int
    chunk_id: uuid.UUID
    knowledge_item_id: uuid.UUID
    item_key: str
    item_version_no: int
    item_title: str | None
    clause_ref: str | None
    normative_source_id: uuid.UUID
    source_title: str
    source_type: NormativeSourceType
    binding: str
    issuing_body: str
    jurisdiction: str
    source_version: str
    effective_date: dt.date | None
    retrieved_at: dt.date
    char_start: int
    char_end: int
    text: str
    structure_label: str | None
    fused_score: float
    vector_similarity: float
    vector_rank: int | None
    keyword_rank: int | None

    @classmethod
    def of(cls, chunk: RetrievedChunk) -> RetrievedChunkOut:
        return cls(
            **chunk.model_dump(include=set(cls.model_fields) - {"binding"}), binding=chunk.binding
        )


class RetrievalOut(BaseModel):
    retrieval_id: uuid.UUID
    outcome: RetrievalOutcome
    empty_reason: EmptyReason | None
    requires_human_review: bool
    as_of: dt.date
    kb_version: int
    kb_version_pinned: bool
    embedding_model: str
    ruleset_version: str
    chunks: list[RetrievedChunkOut]
    evidence_ids: list[uuid.UUID] = Field(default_factory=list)


class EvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    retrieval_id: uuid.UUID
    knowledge_chunk_id: uuid.UUID | None
    target_id: uuid.UUID
    char_start: int
    char_end: int
    rank: int
    retrieval_score: float
    kb_version: int
    embedding_model: str
    ruleset_version: str
    graph_run_id: uuid.UUID | None
    created_at: dt.datetime

    @classmethod
    def of(cls, evidence: Evidence) -> EvidenceOut:
        return cls.model_validate(evidence)


class CitationCheckIn(BaseModel):
    cited_evidence_ids: list[str] = Field(default_factory=list, max_length=200)
    allowed_evidence_ids: list[uuid.UUID] = Field(default_factory=list, max_length=2000)


class CitationCheckOut(BaseModel):
    all_resolved: bool
    resolved: list[Citation]
    rejected: list[tuple[str, str]]


def controls_out(controls: list[Control]) -> list[ControlOut]:
    return [ControlOut.model_validate(c) for c in controls]
