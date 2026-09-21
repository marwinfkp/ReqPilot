"""Project sources, extraction proposals, classification and the review queue.

Tables for roadmap phase P3 (architecture G.3, G.4, G.7, M.5):

* ``source_document`` / ``source_chunk`` - the **project corpus** (J.1): what a
  stakeholder said or wrote, stored once, segmented with exact character
  offsets. Traceability roots, so append-only (G.3). Project chunks carry an
  embedding column but P3 does not fill it: J.2 requires masking before
  anything reaches the vector store, and masking is not implemented yet.
* ``extraction_candidate`` - every proposal the extraction role made, accepted
  or not, with what deterministic validation decided about it. The record of
  "the model proposed; the code disposed".
* ``requirement_classification`` - multi-label classification of a requirement
  version (G.4), in revisions: a human override is a new revision, never an
  edit (``FR-CLS-003``).
* ``acceptance_criterion`` - Given/When/Then proposals bound to one version (G.4).
* ``review_item`` - the review-queue foundation (M.5) for AI proposals. It is
  **not** approval: requirements are approved only at gate G1.
* ``prompt_template`` / ``model_version`` - which exact prompt text and which
  model produced a proposal (G.7, ``FR-AUD-001``).

Mutability, enforced here in the ORM and again by database triggers:

* append-only: ``source_document``, ``source_chunk``, ``requirement_classification``,
  ``acceptance_criterion``, ``prompt_template``, ``model_version``;
* ``extraction_candidate``: content fixed; the decision is written once, from
  ``proposed`` to a terminal status;
* ``review_item``: the resolution is written once, from ``open`` to ``resolved``.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.types import Uuid

from reqpilot.domain.enums import (
    AgentRole,
    CandidateStatus,
    ChunkStrategy,
    DataSensitivity,
    MaskingStatus,
    ProposalSource,
    RequirementCategory,
    ReviewReason,
    ReviewResolution,
    ReviewStatus,
    SourceDocumentType,
)
from reqpilot.domain.errors import ImmutableRecordError
from reqpilot.domain.models.audit import JsonType
from reqpilot.domain.models.base import Base, created_at_column, uuid_pk
from reqpilot.domain.models.knowledge import EMBEDDING_DIMENSION
from reqpilot.domain.requirement_ids import RequirementKind

# ---------------------------------------------------------------------------
# The project corpus
# ---------------------------------------------------------------------------


class SourceDocument(Base):
    """One uploaded project source, stored once (architecture G.3, ``FR-ING-004``).

    ``text`` is the normalised text every offset refers to (``\\r\\n`` becomes
    ``\\n``, so offsets do not depend on the uploader's platform).
    """

    __tablename__ = "source_document"
    __table_args__ = (
        # The same text uploaded twice to one project is the same source.
        UniqueConstraint("project_id", "content_hash", name="project_content"),
        CheckConstraint("char_count >= 1", name="not_empty"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    doc_type: Mapped[SourceDocumentType] = mapped_column(
        SAEnum(SourceDocumentType, name="source_document_type_enum"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(300), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Declared by the uploader (``FR-ING-004``); decides whether the text may
    #: leave the machine unmasked (see the LLM gateway's egress rule).
    sensitivity: Mapped[DataSensitivity] = mapped_column(
        SAEnum(DataSensitivity, name="data_sensitivity_enum"), nullable=False
    )
    #: Whether ``text`` passed a protective masking stage (J.2), and which one.
    masking_status: Mapped[MaskingStatus] = mapped_column(
        SAEnum(MaskingStatus, name="masking_status_enum"), nullable=False
    )
    masker_id: Mapped[str] = mapped_column(String(100), nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()


class SourceChunk(Base):
    """One segment of a project source: a speaker turn or a section (J.3).

    Offsets are within the document's ``text`` and the invariant
    ``text == document.text[char_start:char_end]`` holds for every row. The id is
    derived from the segment itself (:func:`reqpilot.domain.ids.source_chunk_id_for`).
    """

    __tablename__ = "source_chunk"
    __table_args__ = (
        UniqueConstraint("source_document_id", "ordinal", name="document_ordinal"),
        CheckConstraint("char_start >= 0 AND char_end > char_start", name="span_ordered"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("source_document.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy: Mapped[ChunkStrategy] = mapped_column(
        SAEnum(ChunkStrategy, name="chunk_strategy_enum"), nullable=False
    )
    #: The speaker label of a transcript turn, exactly as written in the source.
    speaker: Mapped[str | None] = mapped_column(String(200), nullable=True)
    structure_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Deliberately empty in P3: no unmasked project text reaches the vector
    #: store (architecture J.2). Filled once masking exists.
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSION), nullable=True
    )
    embedding_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()


# ---------------------------------------------------------------------------
# Extraction proposals
# ---------------------------------------------------------------------------


class ExtractionCandidate(Base):
    """One requirement the extraction role proposed, and what became of it.

    ``statement`` and ``proposal`` are exactly what the model returned (after
    schema validation); nothing here is authoritative. The decision columns are
    written once, by deterministic validation.
    """

    __tablename__ = "extraction_candidate"
    __table_args__ = (
        UniqueConstraint("graph_run_id", "candidate_key", name="run_candidate_key"),
        CheckConstraint(
            "(status = 'PROPOSED' AND decided_at IS NULL) OR "
            "(status <> 'PROPOSED' AND decided_at IS NOT NULL)",
            name="decided_once",
        ),
        CheckConstraint(
            "status <> 'ACCEPTED' OR requirement_version_id IS NOT NULL",
            name="accepted_has_version",
        ),
        CheckConstraint(
            "status <> 'MERGED' OR merged_into_id IS NOT NULL", name="merged_has_target"
        ),
        Index("ix_extraction_candidate_project_status", "project_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    graph_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("graph_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agent_run.id", ondelete="CASCADE"), nullable=False
    )
    #: The model's temporary key. **Never** a requirement id (``FR-EXT-004``).
    candidate_key: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Position in the batch, which fixes the order ids are allocated in.
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    requirement_kind: Mapped[RequirementKind] = mapped_column(
        SAEnum(RequirementKind, name="requirement_kind_enum"), nullable=False
    )
    #: The whole schema-valid proposal as the model returned it.
    proposal: Mapped[dict] = mapped_column(JsonType, nullable=False)
    review_signal: Mapped[float] = mapped_column(Float, nullable=False)

    # --- the deterministic decision, written once ---------------------------
    status: Mapped[CandidateStatus] = mapped_column(
        SAEnum(CandidateStatus, name="candidate_status_enum"),
        nullable=False,
        default=CandidateStatus.PROPOSED,
    )
    #: Source spans resolved by code: ``[{ref, document, span, quote, speaker}]``.
    spans: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    #: The verbatim source wording, assembled from the resolved spans.
    original_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: What validation found: ``[{code, message}]``.
    findings: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("extraction_candidate.id", ondelete="CASCADE"), nullable=True
    )
    requirement_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("requirement_version.id", ondelete="CASCADE"), nullable=True
    )
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()

    CONTENT_FIELDS: frozenset[str] = frozenset(
        {
            "id",
            "project_id",
            "graph_run_id",
            "agent_run_id",
            "candidate_key",
            "ordinal",
            "statement",
            "requirement_kind",
            "proposal",
            "review_signal",
            "created_at",
        }
    )


# ---------------------------------------------------------------------------
# Classification and acceptance criteria
# ---------------------------------------------------------------------------


class RequirementClassification(Base):
    """One label of one classification revision of a requirement version.

    The labels sharing ``(requirement_version_id, revision_no)`` are one
    classification. The highest revision is the current one; earlier revisions
    are the history a human override preserves (``FR-CLS-003``).

    ``review_signal`` is the model's heuristic review-prioritisation signal -
    **not** a calibrated probability (approved Phase 0 H.1). A human label has
    none.
    """

    __tablename__ = "requirement_classification"
    __table_args__ = (
        UniqueConstraint(
            "requirement_version_id", "revision_no", "category", name="version_revision_category"
        ),
        CheckConstraint("revision_no >= 1", name="revision_positive"),
        CheckConstraint(
            "review_signal IS NULL OR (review_signal >= 0 AND review_signal <= 1)",
            name="signal_in_range",
        ),
        CheckConstraint(
            "(source = 'AGENT' AND review_signal IS NOT NULL) OR "
            "(source = 'HUMAN' AND review_signal IS NULL AND needs_review = false)",
            name="source_signal_coherent",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requirement_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("requirement_version.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[RequirementCategory] = mapped_column(
        SAEnum(RequirementCategory, name="requirement_category_enum"), nullable=False
    )
    review_signal: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[ProposalSource] = mapped_column(
        SAEnum(ProposalSource, name="proposal_source_enum"), nullable=False
    )
    #: Set by the deterministic threshold policy when the label was recorded.
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_run.id", ondelete="CASCADE"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    change_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()


class AcceptanceCriterion(Base):
    """A Given/When/Then acceptance criterion of one version (``FR-EXT-006``).

    A proposal, bound to the version it was created with. It approves nothing
    and is never compliance evidence.
    """

    __tablename__ = "acceptance_criterion"
    __table_args__ = (
        UniqueConstraint("requirement_version_id", "ordinal", name="version_ordinal"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requirement_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("requirement_version.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    given_text: Mapped[str] = mapped_column(Text, nullable=False)
    when_text: Mapped[str] = mapped_column(Text, nullable=False)
    then_text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[ProposalSource] = mapped_column(
        SAEnum(ProposalSource, name="proposal_source_enum"), nullable=False
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_run.id", ondelete="CASCADE"), nullable=True
    )
    #: Set when a merge carried the criterion forward to a successor version.
    copied_from_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("acceptance_criterion.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[dt.datetime] = created_at_column()


# ---------------------------------------------------------------------------
# The review queue (architecture M.5)
# ---------------------------------------------------------------------------


class ReviewItem(Base):
    """One AI proposal awaiting a human look (``FR-CLS-002``, ``FR-HIL-006``).

    Resolving an item never approves, baselines or transitions anything by
    itself. Approval is gate G1, unchanged.
    """

    __tablename__ = "review_item"
    __table_args__ = (
        CheckConstraint(
            "(status = 'OPEN' AND resolution IS NULL AND resolved_at IS NULL "
            " AND resolved_by IS NULL) OR "
            "(status = 'RESOLVED' AND resolution IS NOT NULL AND resolved_at IS NOT NULL "
            " AND resolved_by IS NOT NULL)",
            name="resolution_coherent",
        ),
        Index("ix_review_item_project_status", "project_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[ReviewReason] = mapped_column(
        SAEnum(ReviewReason, name="review_reason_enum"), nullable=False
    )
    #: What the item is about: ``requirement_version``, ``extraction_candidate``
    #: or ``agent_run``.
    subject_type: Mapped[str] = mapped_column(String(50), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: For a possible duplicate, the other requirement version.
    related_subject_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    requirement_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("requirement_version.id", ondelete="CASCADE"), nullable=True, index=True
    )
    #: For a label item, which label.
    category: Mapped[RequirementCategory | None] = mapped_column(
        SAEnum(RequirementCategory, name="requirement_category_enum"), nullable=True
    )
    #: The lower the signal, the higher the item sorts (architecture M.5).
    review_signal: Mapped[float | None] = mapped_column(Float, nullable=True)
    graph_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("graph_run.id", ondelete="CASCADE"), nullable=True
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_run.id", ondelete="CASCADE"), nullable=True
    )
    #: References and codes only, like an audit payload.
    detail: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)

    status: Mapped[ReviewStatus] = mapped_column(
        SAEnum(ReviewStatus, name="review_status_enum"),
        nullable=False,
        default=ReviewStatus.OPEN,
    )
    resolution: Mapped[ReviewResolution | None] = mapped_column(
        SAEnum(ReviewResolution, name="review_resolution_enum"), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()

    RESOLUTION_FIELDS: frozenset[str] = frozenset(
        {"status", "resolution", "resolution_note", "resolved_by", "resolved_at"}
    )


# ---------------------------------------------------------------------------
# Prompt and model provenance (architecture G.7, R.2)
# ---------------------------------------------------------------------------


class PromptTemplate(Base):
    """The exact text of one prompt-template version that was used (DQ-04).

    Templates live as versioned files; this row records the text a run actually
    used, keyed by name and version. A changed text under an unchanged version is
    refused by the registry, so a version always means one text.
    """

    __tablename__ = "prompt_template"
    __table_args__ = (UniqueConstraint("name", "version", name="name_version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[AgentRole] = mapped_column(
        SAEnum(AgentRole, name="agent_role_enum"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(20), nullable=False)
    template_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    template_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


class ModelVersion(Base):
    """One provider/model/parameter combination that produced output (G.7)."""

    __tablename__ = "model_version"
    __table_args__ = (
        UniqueConstraint("provider", "model_id", "params_hash", name="provider_model_params"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)
    #: A stub or scripted provider is not a model. Its output never counts
    #: towards an evaluation metric.
    is_model: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


# ---------------------------------------------------------------------------
# ORM-level immutability (the database triggers are the second layer)
# ---------------------------------------------------------------------------

_APPEND_ONLY: tuple[type[Base], ...] = (
    SourceDocument,
    SourceChunk,
    RequirementClassification,
    AcceptanceCriterion,
    PromptTemplate,
    ModelVersion,
)


def _changed_columns(instance: Base) -> set[str]:
    state = inspect(instance)
    return {attr.key for attr in state.mapper.column_attrs if state.attrs[attr.key].history.deleted}


def _previous(instance: Base, attribute: str) -> object:
    deleted = inspect(instance).attrs[attribute].history.deleted
    return deleted[0] if deleted else None


@event.listens_for(Session, "before_flush")
def _guard_extraction_immutability(session: Session, _context: object, _instances: object) -> None:
    """Refuse any flush that edits or deletes extraction history.

    Registered on the ``Session`` class, so no session in the process can skip it.
    Project deletion removes these rows through the database's cascade, not here.
    """
    for instance in session.deleted:
        if isinstance(instance, (*_APPEND_ONLY, ExtractionCandidate, ReviewItem)):
            raise ImmutableRecordError(f"{type(instance).__name__} rows are never deleted")

    for instance in session.dirty:
        changed = _changed_columns(instance)
        if not changed:
            continue
        if isinstance(instance, _APPEND_ONLY):
            raise ImmutableRecordError(
                f"{type(instance).__name__} rows are append-only; record a new row instead"
            )
        if isinstance(instance, ExtractionCandidate):
            content = changed & ExtractionCandidate.CONTENT_FIELDS
            if content:
                raise ImmutableRecordError(
                    f"an extraction proposal is immutable (attempted: {sorted(content)})"
                )
            if (
                "status" in changed
                and _previous(instance, "status") is not CandidateStatus.PROPOSED
            ):
                raise ImmutableRecordError("an extraction candidate is decided once")
            if "status" not in changed and instance.status is not CandidateStatus.PROPOSED:
                raise ImmutableRecordError("a decided extraction candidate cannot change")
        elif isinstance(instance, ReviewItem):
            if changed - ReviewItem.RESOLUTION_FIELDS:
                raise ImmutableRecordError("a review item's subject and detail are immutable")
            if _previous(instance, "status") is not ReviewStatus.OPEN and "status" in changed:
                raise ImmutableRecordError("a review item is resolved once")
            if "status" not in changed:
                raise ImmutableRecordError("a resolved review item cannot change")
